"""Phase 4 of ADS Group enrichment — capture ALL contact cards on each
member detail page (some companies expose 2 or 3 named contacts).

The previous script (``enrich_ads_html_contacts.py``) updated the parent
ADS row with the FIRST contact card. This script handles 2nd, 3rd…
contact cards by INSERTING new ``attendance_signals`` rows linked to the
parent company via ``canonical_company_name``.

Each additional contact becomes a row with:
    entity_type = 'person'
    person_name / person_role / phone in notes
    company_name = parent ADS company
    source_platform = 'ads-group-uk-2nd-contact'
    source_url = parent ADS URL

Idempotent: if a row with same canonical_company_name + person_name from
this platform already exists, we skip it.
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
# Multi-card parser
# ---------------------------------------------------------------------------


_CARD_BLOCK_RX = re.compile(
    # Each event-info-card spans up to (but not including) the next
    # ``event-info-card`` opener — non-greedy lookahead.
    r'<div class="event-info-card">'
    r'\s*<h3 class="m-0">(?P<name>[^<]*)</h3>'
    r'(?P<rest>.*?)'
    r'(?=<div class="event-info-card">|</div>\s*</div>\s*</div>)',
    re.DOTALL,
)
_ROLE_RX = re.compile(r'<strong class="mb-0">([^<]*)</strong>')
_PHONE_RX = re.compile(r'<a href="tel:[^"]*">([^<]+)</a>')


def parse_all_contacts(html: str) -> list[dict]:
    out: list[dict] = []
    for m in _CARD_BLOCK_RX.finditer(html):
        name = unescape(m.group("name")).strip()
        # Skip placeholders ". ."
        if not name or not re.sub(r"[.\s]", "", name):
            continue
        rest = m.group("rest")
        role = None
        if (rm := _ROLE_RX.search(rest)):
            v = unescape(rm.group(1)).strip()
            if v:
                role = v
        phone = None
        if (pm := _PHONE_RX.search(rest)):
            v = unescape(pm.group(1)).strip()
            if v:
                phone = v
        out.append({"name": name, "role": role, "phone": phone})
    return out


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def fetch_targets(con: sqlite3.Connection,
                  limit: Optional[int]) -> list[dict]:
    cur = con.cursor()
    cur.execute(
        "SELECT id, source_url, company_name, canonical_company_name "
        "FROM attendance_signals "
        "WHERE source_platform='ads-group-uk' "
        "ORDER BY id"
    )
    rows = [
        {"id": r[0], "url": r[1], "company": r[2], "canonical": r[3]}
        for r in cur.fetchall()
    ]
    if limit:
        rows = rows[:limit]
    return rows


def existing_extra_contacts(con: sqlite3.Connection) -> set[tuple[str, str]]:
    """Set of (canonical, person_name_lower) already inserted as 'extra'."""
    cur = con.cursor()
    cur.execute(
        "SELECT canonical_company_name, LOWER(person_name) "
        "FROM attendance_signals "
        "WHERE source_platform='ads-group-uk-2nd-contact'"
    )
    return {(r[0], r[1]) for r in cur.fetchall() if r[0] and r[1]}


def insert_extra_contact(
    con: sqlite3.Connection,
    parent: dict,
    contact: dict,
) -> bool:
    if not contact.get("name"):
        return False
    notes_lines = []
    if contact.get("phone"):
        notes_lines.append(f"[phone] {contact['phone']}")
    notes = "\n".join(notes_lines)[:2000]
    cur = con.cursor()
    cur.execute(
        """INSERT INTO attendance_signals (
            edition_year, entity_type, person_name, person_role,
            company_name, canonical_company_name, country,
            source_platform, source_url, source_title, source_snippet,
            signal_type, signal_text, signal_strength_reason,
            is_company_post, is_personal_post, is_official_delegation,
            is_exhibitor_employee, is_duplicate,
            presence_confidence, manual_validation_status, notes
        ) VALUES (
            2026, 'person', ?, ?,
            ?, ?, 'United Kingdom',
            'ads-group-uk-2nd-contact', ?, ?, ?,
            'leadership_lookup', ?,
            'Additional contact card on the ADS Group UK member page.',
            0, 0, 0, 0, 0,
            'high', 'pending', ?
        )""",
        (
            contact["name"][:255],
            (contact.get("role") or "")[:255],
            parent["company"][:400],
            parent["canonical"][:80],
            parent["url"][:900],
            f"{contact['name']} — {contact.get('role') or ''} @ {parent['company']}"[:400],
            "",
            f"{contact['name']} ({contact.get('role') or '?'}) chez {parent['company']}"[:500],
            notes,
        ),
    )
    return True


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


async def process_one(sem, client, parent, results):
    async with sem:
        html = await fetch(client, parent["url"])
        if not html:
            results.append((parent, None))
            return
        contacts = parse_all_contacts(html)
        # First contact already in parent row — only return 2nd, 3rd…
        results.append((parent, contacts[1:] if len(contacts) > 1 else []))


async def main_async(args) -> int:
    con = sqlite3.connect(DB_PATH)
    targets = fetch_targets(con, args.limit)
    if not targets:
        print("Aucune cible.")
        return 0
    print(f"Targets : {len(targets)} fiches ADS UK")
    already = existing_extra_contacts(con)
    print(f"Existing extra contacts: {len(already)}")

    sem = asyncio.Semaphore(args.concurrency)
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        results: list = []
        tasks = [process_one(sem, client, t, results) for t in targets]
        n_done = 0
        n_inserted = 0
        n_pages_with_extra = 0
        n_err = 0
        t0 = time.time()
        for coro in asyncio.as_completed(tasks):
            await coro
            n_done += 1
            if n_done % 50 == 0 or n_done == len(targets):
                while results:
                    parent, extras = results.pop(0)
                    if extras is None:
                        n_err += 1
                        continue
                    if extras:
                        n_pages_with_extra += 1
                    for c in extras:
                        key = (parent["canonical"], (c["name"] or "").lower())
                        if key in already:
                            continue
                        if insert_extra_contact(con, parent, c):
                            n_inserted += 1
                            already.add(key)
                con.commit()
                dt = time.time() - t0
                rate = n_done / dt if dt > 0 else 0
                print(
                    f"  [{n_done:>4}/{len(targets)}] "
                    f"pages_with_extra={n_pages_with_extra} "
                    f"inserted={n_inserted} err={n_err} ({rate:.2f}/s)"
                )
    while results:
        parent, extras = results.pop(0)
        if extras is None:
            n_err += 1
            continue
        if extras:
            n_pages_with_extra += 1
        for c in extras:
            key = (parent["canonical"], (c["name"] or "").lower())
            if key in already:
                continue
            if insert_extra_contact(con, parent, c):
                n_inserted += 1
                already.add(key)
    con.commit()
    con.close()
    dt = time.time() - t0
    print(
        f"\nDone in {dt:.1f}s : "
        f"pages_with_extra={n_pages_with_extra} · {n_inserted} extra contacts inserted · err={n_err}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=6)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
