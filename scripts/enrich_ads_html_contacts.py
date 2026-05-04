"""Phase 3 of ADS Group enrichment — re-fetch each member detail page
and extract the publicly displayed contact card from the HTML body.

The ``Contact us`` block on every ADS member page (when populated)
exposes :

    <h3 class="m-0">{contact_name}</h3>
    <strong class="mb-0">{contact_role}</strong>
    <a href="tel:{phone}">{phone}</a>

This is in addition to what the JSON-LD already gave us (description,
address, knowsAbout). Email is NEVER exposed (only via a contact form),
so we don't try.

Updates the existing ``attendance_signals`` row in place :

    person_name   ← contact name
    person_role   ← contact role
    entity_type   ← 'person' (when name found)
    notes         ← appends ``[phone] ...`` line

Idempotent : a re-run that finds the same data is a no-op (the lines
are replace-or-add, not append-blindly).
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sqlite3
import sys
import time
from html import unescape
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

DB_PATH = ROOT / "data" / "eurosatory.db"
USER_AGENT = (
    "Mozilla/5.0 (compatible; eurosatory-scraper/1.0; "
    "+contact: a.bertantoine@gmail.com)"
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_CONTACT_BLOCK_RX = re.compile(
    r'<div class="event-info-card">(.*?)</div>\s*</div>\s*</div>',
    re.DOTALL,
)
_NAME_RX = re.compile(r'<h3 class="m-0">([^<]*)</h3>')
_ROLE_RX = re.compile(r'<strong class="mb-0">([^<]*)</strong>')
_PHONE_RX = re.compile(r'<a href="tel:[^"]*">([^<]+)</a>')


def parse_contact_card(html: str) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {
        "name": None, "role": None, "phone": None,
    }
    m = _CONTACT_BLOCK_RX.search(html)
    if not m:
        return out
    block = m.group(1)
    if (n := _NAME_RX.search(block)):
        v = unescape(n.group(1)).strip()
        # Skip placeholder (". .")
        if v and re.sub(r"[.\s]", "", v):
            out["name"] = v
    if (r := _ROLE_RX.search(block)):
        v = unescape(r.group(1)).strip()
        if v:
            out["role"] = v
    if (p := _PHONE_RX.search(block)):
        v = unescape(p.group(1)).strip()
        if v:
            out["phone"] = v
    return out


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def replace_or_add_line(notes: str, prefix: str, value: str) -> str:
    """Replace the line starting with ``prefix`` if present, else append."""
    line = f"{prefix} {value}"
    lines = (notes or "").splitlines()
    out: list[str] = []
    replaced = False
    for ln in lines:
        if ln.startswith(prefix):
            out.append(line)
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(line)
    return "\n".join(out).strip()


def fetch_targets(con: sqlite3.Connection,
                  limit: Optional[int]) -> list[dict]:
    cur = con.cursor()
    cur.execute(
        "SELECT id, source_url, person_name, notes "
        "FROM attendance_signals "
        "WHERE source_platform='ads-group-uk' "
        "ORDER BY id"
    )
    rows = []
    for sid, url, person, notes in cur.fetchall():
        rows.append({
            "id": sid, "url": url,
            "person_name": person or "",
            "notes": notes or "",
        })
    if limit:
        rows = rows[:limit]
    return rows


def update_signal(con: sqlite3.Connection, sid: int, parsed: dict,
                  current_notes: str) -> dict:
    updates: dict[str, str] = {}
    if parsed.get("name"):
        updates["person_name"] = parsed["name"][:255]
        updates["entity_type"] = "person"
    if parsed.get("role"):
        updates["person_role"] = parsed["role"][:255]
    new_notes = current_notes
    if parsed.get("phone"):
        new_notes = replace_or_add_line(new_notes, "[phone]", parsed["phone"])
    if new_notes != current_notes:
        updates["notes"] = new_notes[:8000]
    if not updates:
        return updates
    cur = con.cursor()
    sets = ", ".join(f"{k} = ?" for k in updates)
    cur.execute(
        f"UPDATE attendance_signals SET {sets} WHERE id = ?",
        list(updates.values()) + [sid],
    )
    return updates


# ---------------------------------------------------------------------------
# Async fetch
# ---------------------------------------------------------------------------


async def fetch(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url, timeout=30)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:  # noqa: BLE001
        return None


async def process_one(sem, client, row, results):
    async with sem:
        html = await fetch(client, row["url"])
        if not html:
            results.append((row["id"], None, "fetch_failed"))
            return
        parsed = parse_contact_card(html)
        results.append((row["id"], parsed, "ok"))


async def main_async(args) -> int:
    con = sqlite3.connect(DB_PATH)
    targets = fetch_targets(con, args.limit)
    if not targets:
        print("Aucune cible.")
        return 0
    print(f"Targets : {len(targets)} fiches ADS UK à enrichir")

    sem = asyncio.Semaphore(args.concurrency)
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        results: list = []
        tasks = [process_one(sem, client, t, results) for t in targets]
        n_done = 0
        n_with_name = 0
        n_with_role = 0
        n_with_phone = 0
        n_err = 0
        t0 = time.time()
        BATCH = 50
        for coro in asyncio.as_completed(tasks):
            await coro
            n_done += 1
            if n_done % BATCH == 0 or n_done == len(targets):
                while results:
                    sid, parsed, status = results.pop(0)
                    if status != "ok" or parsed is None:
                        n_err += 1
                        continue
                    notes = next(
                        (t["notes"] for t in targets if t["id"] == sid), ""
                    )
                    upd = update_signal(con, sid, parsed, notes)
                    if upd.get("person_name"):
                        n_with_name += 1
                    if upd.get("person_role"):
                        n_with_role += 1
                    if "[phone]" in (upd.get("notes") or ""):
                        n_with_phone += 1
                con.commit()
                dt = time.time() - t0
                rate = n_done / dt if dt > 0 else 0
                print(
                    f"  [{n_done:>4}/{len(targets)}] "
                    f"+name={n_with_name} +role={n_with_role} "
                    f"+phone={n_with_phone} err={n_err} ({rate:.2f}/s)"
                )

    # Final flush
    while results:
        sid, parsed, status = results.pop(0)
        if status != "ok" or parsed is None:
            n_err += 1
            continue
        notes = next((t["notes"] for t in targets if t["id"] == sid), "")
        upd = update_signal(con, sid, parsed, notes)
        if upd.get("person_name"):
            n_with_name += 1
        if upd.get("person_role"):
            n_with_role += 1
        if "[phone]" in (upd.get("notes") or ""):
            n_with_phone += 1
    con.commit()
    con.close()
    dt = time.time() - t0
    print(
        f"\nDone in {dt:.1f}s : "
        f"+name={n_with_name}, +role={n_with_role}, +phone={n_with_phone}, "
        f"err={n_err}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=4)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
