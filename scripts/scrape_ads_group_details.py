"""Phase 2 of ADS Group UK scraping — fetch each member's detail page
and extract the JSON-LD ``Organization`` block.

Why
---
The listing pages give us name + region + website + 300-char preview.
Detail pages expose, in a single ``<script type="application/ld+json">``
block :

    description    full long-form description (5-30 sentences)
    address        {streetAddress, addressLocality, postalCode}
    contactPoint   [...]            usually empty
    knowsAbout     [capability ...] 5-100 standardised tags
                                   (Whole Aircraft, Avionics, Composites,
                                    ISO 9001, etc.)

These ``knowsAbout`` tags are the goldmine — they map cleanly onto our
75 canonical product categories and our 5 supply-chain tiers. Once we
have them, we can auto-classify every UK ADS member into the same
filterable taxonomy as Eurosatory exhibitors.

Persistence
-----------
Per row in ``attendance_signals`` (``source_platform='ads-group-uk'``):

    source_snippet         ← full ``description`` (replacing 300-char preview)
    signal_strength_reason ← human-readable summary
                             "ADS Group UK · {N} capabilities · {locality}"
    notes                  ← JSON blob ``[ads-detail] {…}``
                             (preserving any pre-existing ``[email] …`` lines
                              from the LLM enrichment step)

The JSON blob holds: ``address``, ``knowsAbout`` list, ``logo``, plus the
``website`` URL — enough to feed downstream taxonomy mapping.

Usage
-----
    python -m scripts.scrape_ads_group_details --limit 5    # smoke test
    python -m scripts.scrape_ads_group_details              # full run

"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from html import unescape
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "eurosatory.db"
USER_AGENT = (
    "Mozilla/5.0 (compatible; eurosatory-scraper/1.0; "
    "+contact: a.bertantoine@gmail.com)"
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_JSONLD_RX = re.compile(
    r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
    re.DOTALL,
)


def parse_detail(html: str) -> Optional[dict]:
    """Return the parsed JSON-LD Organization block, or None."""
    m = _JSONLD_RX.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if data.get("@type") != "Organization":
        return None
    return {
        "name":        data.get("name") or "",
        "url":         data.get("url") or "",
        "logo":        data.get("logo") or "",
        "description": (data.get("description") or "").strip(),
        "address":     data.get("address") or {},
        "contactPoint": data.get("contactPoint") or [],
        "knowsAbout":  data.get("knowsAbout") or [],
    }


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------


def fetch_targets(con: sqlite3.Connection, limit: Optional[int]) -> list[dict]:
    cur = con.cursor()
    q = (
        "SELECT id, source_url, notes "
        "FROM attendance_signals "
        "WHERE source_platform='ads-group-uk' "
        "  AND (notes IS NULL OR notes NOT LIKE '%[ads-detail]%') "
        "ORDER BY id"
    )
    cur.execute(q + (f" LIMIT {int(limit)}" if limit else ""))
    return [
        {"id": r[0], "url": r[1], "notes": r[2] or ""}
        for r in cur.fetchall()
    ]


def update_signal(con: sqlite3.Connection, sid: int, payload: dict,
                  existing_notes: str) -> None:
    desc = payload.get("description") or ""
    addr = payload.get("address") or {}
    locality = addr.get("addressLocality") or addr.get("postalCode") or ""
    n_caps = len(payload.get("knowsAbout") or [])
    summary = (
        f"ADS Group UK directory · {n_caps} capabilities"
        + (f" · {locality}" if locality else "")
    )
    blob = {
        "address": addr,
        "knowsAbout": payload.get("knowsAbout") or [],
        "website": payload.get("url") or "",
        "logo": payload.get("logo") or "",
    }
    blob_line = f"[ads-detail] {json.dumps(blob, ensure_ascii=False)}"
    new_notes = (
        f"{existing_notes}\n{blob_line}".strip()
        if existing_notes else blob_line
    )
    new_notes = new_notes[:8000]
    cur = con.cursor()
    cur.execute(
        "UPDATE attendance_signals "
        "SET source_snippet = ?, "
        "    signal_strength_reason = ?, "
        "    notes = ? "
        "WHERE id = ?",
        (desc[:1500], summary[:500], new_notes, sid),
    )


# ---------------------------------------------------------------------------
# Async fetch
# ---------------------------------------------------------------------------


async def fetch_detail(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url, timeout=30)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:  # noqa: BLE001
        return None


async def process_one(sem, client, row, results):
    async with sem:
        html = await fetch_detail(client, row["url"])
        if not html:
            results.append((row["id"], None, "fetch_failed"))
            return
        data = parse_detail(html)
        if not data:
            results.append((row["id"], None, "no_jsonld"))
            return
        results.append((row["id"], data, "ok"))


async def main_async(args) -> int:
    con = sqlite3.connect(DB_PATH)
    targets = fetch_targets(con, args.limit)
    if not targets:
        print("Aucune fiche ADS sans détails.")
        return 0
    print(f"Targets : {len(targets)} fiches détail à scraper")

    sem = asyncio.Semaphore(args.concurrency)
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        results: list = []
        tasks = [
            process_one(sem, client, t, results) for t in targets
        ]
        n_done = 0
        n_ok = 0
        n_err = 0
        n_no_jsonld = 0
        t0 = time.time()
        # Use as_completed-like behaviour but commit periodically
        BATCH = 50
        for i, coro in enumerate(asyncio.as_completed(tasks)):
            await coro
            n_done += 1
            # Commit a batch every BATCH items
            if n_done % BATCH == 0 or n_done == len(targets):
                # Flush results into DB
                while results:
                    sid, data, status = results.pop(0)
                    if status == "ok":
                        # Find existing notes (already cached in `targets`)
                        notes = next(
                            (t["notes"] for t in targets if t["id"] == sid), ""
                        )
                        update_signal(con, sid, data, notes)
                        n_ok += 1
                    elif status == "no_jsonld":
                        n_no_jsonld += 1
                    else:
                        n_err += 1
                con.commit()
                dt = time.time() - t0
                rate = n_done / dt if dt > 0 else 0
                print(
                    f"  [{n_done:>4}/{len(targets)}] "
                    f"ok={n_ok}  no_jsonld={n_no_jsonld}  err={n_err}  "
                    f"({rate:.2f}/s)"
                )

    # Final flush in case anything remains
    while results:
        sid, data, status = results.pop(0)
        if status == "ok":
            notes = next((t["notes"] for t in targets if t["id"] == sid), "")
            update_signal(con, sid, data, notes)
            n_ok += 1
        elif status == "no_jsonld":
            n_no_jsonld += 1
        else:
            n_err += 1
    con.commit()
    con.close()

    dt = time.time() - t0
    print(
        f"\nDone in {dt:.1f}s : ok={n_ok}, "
        f"no_jsonld={n_no_jsonld}, fetch_err={n_err}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N rows (smoke test).")
    p.add_argument("--concurrency", type=int, default=4,
                   help="Concurrent fetches (default 4 — be polite).")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
