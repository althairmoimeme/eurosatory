"""Scrape the ADS Group UK members directory and persist into attendance_signals.

ADS Group is the UK trade association for Aerospace, Defence, Security and
Space — ~2 007 member companies in May 2026.  Source :
    https://www.adsgroup.org.uk/members/page/{1..126}/

Per-member fields exposed on the listing page:
    • Company name
    • UK region   (e.g. South East, Northern Ireland)
    • Website URL
    • Phone       (NOT persisted — user explicitly excluded)
    • Short description (≤ 300 chars before "…")
    • Detail-page URL  (kept for future deep-scrape of capabilities)

Persisted as ``attendance_signals`` rows with :
    entity_type        = "company"
    company_name       = ADS-listed name (kept verbatim)
    canonical_company_name = lowercased / stripped (matches the dedupe scheme
                              used elsewhere)
    country            = "United Kingdom"
    source_platform    = "ads-group-uk"
    signal_type        = "trade_association_member"
    signal_text        = "Member of ADS Group UK ({region})"
    presence_confidence= "high"   (verified industry directory)

We DO check for canonical-name duplicates within attendance_signals so a
re-run of this script is idempotent — it inserts only the new ones.

Usage
-----
    python -m scripts.scrape_ads_group_members --limit 5  # smoke test
    python -m scripts.scrape_ads_group_members            # full run
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from html import unescape
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "eurosatory.db"
BASE = "https://www.adsgroup.org.uk/members/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; eurosatory-scraper/1.0; "
    "+contact: a.bertantoine@gmail.com)"
)
PER_HOST_DELAY = 0.8  # be polite — 1.25 req/s


# ---------------------------------------------------------------------------
# Regex parsers — Beautiful Soup would be cleaner but the listing markup is
# stable and small enough that a single multi-line regex per card is faster.
# ---------------------------------------------------------------------------


# 1) Isolate each card. Each card is a <div class="result-item ..."> ...
#    </div> closing right before the *next* result-item or the trailing
#    pagination block. We split on the opening tag.
CARD_SPLIT_RX = re.compile(r'<div class="result-item[^"]*">', re.DOTALL)

# 2) Per-card field extractors — applied to the substring between two
#    "result-item" boundaries, so they cannot cross-pollute.
NAME_RX = re.compile(
    r'<h5><a href="(?P<url>https://www\.adsgroup\.org\.uk/members/[a-z0-9-]+/?)">'
    r'(?P<name>[^<]+)</a></h5>',
    re.DOTALL,
)
REGION_RX = re.compile(
    r'<p class="location[^"]*"><a[^>]+>(?P<region>[^<]+)</a></p>',
    re.DOTALL,
)
WEBSITE_RX = re.compile(
    r'<p class="website[^"]*"><a[^>]+>(?P<website>[^<]+)</a></p>',
    re.DOTALL,
)
DESC_RX = re.compile(
    r'<p class="mt-3">(?P<desc>[^<]+)</p>', re.DOTALL,
)


def fetch_page(session: requests.Session, page: int) -> str:
    url = BASE if page == 1 else f"{BASE}page/{page}/"
    r = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text


def parse_page(html: str) -> list[dict]:
    """Split the page into per-card substrings, then run the field
    extractors on each substring (no cross-card bleed)."""
    chunks = CARD_SPLIT_RX.split(html)[1:]  # drop the pre-first-card preamble
    out: list[dict] = []
    for chunk in chunks:
        # Cut the chunk at the first '</div>' that closes the card to keep
        # extractors strictly inside the boundary.
        end = chunk.find('</ul>\n</div>')  # the card is <div><ul>…</ul></div>
        body = chunk[:end] if end != -1 else chunk
        m_name = NAME_RX.search(body)
        if not m_name:
            continue
        url = m_name.group("url").rstrip("/") + "/"
        name = unescape(m_name.group("name")).strip()
        if not name:
            continue
        region = ""
        if (m := REGION_RX.search(body)):
            region = unescape(m.group("region")).strip()
        website = None
        if (m := WEBSITE_RX.search(body)):
            website = m.group("website").strip() or None
        desc = ""
        if (m := DESC_RX.search(body)):
            desc = unescape(m.group("desc")).strip()
        out.append({
            "url": url,
            "name": name,
            "region": region,
            "website": website,
            "description": desc[:1500],
        })
    return out


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def canonical(name: str) -> str:
    """Lowercase / strip / collapse spaces — same scheme as
    ``app.attendance.dedup.canonical_company_name``.
    """
    n = re.sub(r"\s+", " ", (name or "").lower().strip())
    # Drop a trailing legal suffix to maximise dedup hits
    n = re.sub(
        r"\s+(ltd|limited|plc|llp|inc|corp|sa|gmbh|sas|sarl|bv)\.?$",
        "", n,
    )
    return n.strip()


def existing_canonicals(con: sqlite3.Connection) -> set[str]:
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT canonical_company_name "
        "FROM attendance_signals "
        "WHERE source_platform = 'ads-group-uk'"
    )
    return {r[0] for r in cur.fetchall() if r[0]}


def insert_signal(con: sqlite3.Connection, member: dict) -> bool:
    """Insert one ADS member as an attendance_signals row.

    Returns True if a new row was created."""
    canon = canonical(member["name"])
    if not canon:
        return False
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM attendance_signals "
        "WHERE source_platform='ads-group-uk' "
        "  AND canonical_company_name = ? "
        "LIMIT 1",
        (canon,),
    )
    if cur.fetchone():
        return False  # idempotent
    region = member.get("region") or ""
    signal_text = (
        f"Member of ADS Group UK"
        + (f" ({region})" if region else "")
    )
    cur.execute(
        """INSERT INTO attendance_signals (
            edition_year, entity_type, company_name, canonical_company_name,
            country, source_platform, source_url, source_title, source_snippet,
            signal_type, signal_text, signal_strength_reason,
            is_company_post, is_personal_post, is_official_delegation,
            is_exhibitor_employee, is_duplicate,
            presence_confidence, manual_validation_status
        ) VALUES (
            2026, 'company', ?, ?, 'United Kingdom',
            'ads-group-uk', ?, ?, ?,
            'trade_association_member', ?,
            'Listed in ADS Group UK members directory (~2 007 entries).',
            1, 0, 0, 0, 0,
            'high', 'pending'
        )""",
        (
            member["name"][:400],
            canon[:80],
            member["url"][:900],
            f"ADS Group UK member: {member['name']}"[:400],
            (member.get("description") or "")[:1500],
            signal_text[:500],
        ),
    )
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Pages to fetch (default: all 126).")
    args = p.parse_args()

    sess = requests.Session()
    con = sqlite3.connect(DB_PATH)
    already = existing_canonicals(con)
    print(f"Existing ADS-group rows in DB: {len(already)}")

    n_pages = 126 if args.limit is None else args.limit
    n_inserted = 0
    n_skipped = 0
    n_seen = 0
    t0 = time.time()
    for page in range(1, n_pages + 1):
        try:
            html = fetch_page(sess, page)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ page {page} failed: {e!r}")
            time.sleep(2)
            continue
        members = parse_page(html)
        if not members:
            # End of pagination
            print(f"  page {page}: 0 members → stopping.")
            break
        for m in members:
            n_seen += 1
            inserted = insert_signal(con, m)
            if inserted:
                n_inserted += 1
            else:
                n_skipped += 1
        con.commit()
        if page % 10 == 0 or page == n_pages or len(members) < 16:
            dt = time.time() - t0
            rate = page / dt if dt > 0 else 0
            print(
                f"  page {page:>3}: {len(members)} members "
                f"(total seen={n_seen}, inserted={n_inserted}, "
                f"skipped={n_skipped}, {rate:.2f} pages/s)"
            )
        time.sleep(PER_HOST_DELAY)

    con.close()
    print(
        f"\nDone in {time.time()-t0:.1f}s : "
        f"seen={n_seen}, inserted={n_inserted} new, "
        f"skipped={n_skipped} (already in DB)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
