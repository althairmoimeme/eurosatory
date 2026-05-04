"""Scrape the BDLI member directory (German aerospace industry assoc).

Source : https://www.bdli.de/en/association/our-member-companies (~317 members).

Per-member detail page (e.g.
https://www.bdli.de/en/mitglied/acc-aviation-coaching-consulting-gmbh)
exposes :

    Company name (H1)
    Address (street + postal + city)
    Website
    GPS coordinates (lat / lon)
    Constituency (German electoral district)

NO email, NO phone, NO named contact (BDLI doesn't publish them).
This still complements BDSV with the aerospace-focused list.

Persists into ``attendance_signals`` as ``source_platform='bdli-de'``.
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
LIST_URL = "https://www.bdli.de/en/association/our-member-companies"
DETAIL_BASE = "https://www.bdli.de"
USER_AGENT = (
    "Mozilla/5.0 (compatible; eurosatory-scraper/1.0; "
    "+contact: a.bertantoine@gmail.com)"
)
PER_HOST_DELAY = 0.4


_TAG_RX = re.compile(r"<[^>]+>")


def html_to_text(s: str) -> str:
    return unescape(_TAG_RX.sub(" ", s or "")).strip()


_LINK_RX = re.compile(r'href="(/en/mitglied/[a-z0-9-]+)"')


def list_member_urls(session: requests.Session) -> list[str]:
    r = session.get(LIST_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    seen: list[str] = []
    seen_set: set[str] = set()
    for path in _LINK_RX.findall(r.text):
        if path not in seen_set:
            seen_set.add(path)
            seen.append(DETAIL_BASE + path)
    return seen


_H1_RX = re.compile(r"<h1[^>]*>([^<]+)</h1>")
_CONTACT_BLOCK_RX = re.compile(
    r'<div class="m-member__contact">\s*(.*?)\s*</div>',
    re.DOTALL,
)
_WEBSITE_RX = re.compile(
    r'<div class="m-member__website">\s*([^<]+)\s*</div>',
)
_LATLON_RX = re.compile(
    r'data-lat="([^"]+)"\s+data-lon="([^"]+)"'
)
_DISTRICT_RX = re.compile(
    r'<div class="m-member__district">\s*<span[^>]*>\s*Constituency:[^<]*</span>\s*([^<]+)<',
)


def parse_detail(html: str) -> dict:
    out: dict = {
        "name": "", "address": "", "website": "",
        "lat": "", "lon": "", "district": "",
    }
    if (m := _H1_RX.search(html)):
        out["name"] = unescape(m.group(1)).strip()
    if (m := _CONTACT_BLOCK_RX.search(html)):
        body = m.group(1)
        # Lines split by <br>
        lines = [
            unescape(s).strip()
            for s in re.split(r"<br\s*/?>", body)
            if s.strip()
        ]
        # Drop the first line if it equals the company name
        if lines and out["name"] and lines[0].lower() == out["name"].lower():
            lines = lines[1:]
        out["address"] = " | ".join(lines)
    if (m := _WEBSITE_RX.search(html)):
        out["website"] = m.group(1).strip()
    if (m := _LATLON_RX.search(html)):
        out["lat"] = m.group(1).strip()
        out["lon"] = m.group(2).strip()
    if (m := _DISTRICT_RX.search(html)):
        out["district"] = unescape(m.group(1)).strip()
    return out


def canonical(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").lower().strip())
    n = re.sub(r"\s+(gmbh(\s*&\s*co\.?\s*kg)?|ag|kg|ohg|se|mbh|gbr|ug|e\.v\.|ev|ltd|inc)\.?$", "", n)
    return n.strip()[:80]


def existing_canonicals(con: sqlite3.Connection) -> set[str]:
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT canonical_company_name FROM attendance_signals "
        "WHERE source_platform='bdli-de'"
    )
    return {r[0] for r in cur.fetchall() if r[0]}


def insert_signal(con: sqlite3.Connection, url: str, data: dict) -> bool:
    name = data.get("name") or ""
    if not name:
        return False
    canon = canonical(name)
    if not canon:
        return False
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM attendance_signals WHERE source_platform='bdli-de' "
        "AND canonical_company_name = ? LIMIT 1",
        (canon,),
    )
    if cur.fetchone():
        return False

    notes_lines = []
    if data.get("website"):
        notes_lines.append(f"[website] {data['website']}")
    if data.get("address"):
        notes_lines.append(f"[address] {data['address']}")
    if data.get("district"):
        notes_lines.append(f"[district] {data['district']}")
    if data.get("lat") and data.get("lon"):
        notes_lines.append(f"[geo] {data['lat']},{data['lon']}")
    notes = "\n".join(notes_lines)[:8000]

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
            2026, 'company', NULL, NULL,
            ?, ?, 'Germany',
            'bdli-de', ?, ?, ?,
            'trade_association_member', ?,
            'Listed in BDLI — Bundesverband der Deutschen Luft- und Raumfahrtindustrie (German Aerospace Industries Association).',
            1, 0, 0, 0, 0,
            'high', 'pending', ?
        )""",
        (
            name[:400], canon,
            url[:900],
            f"BDLI member: {name}"[:400],
            "",
            f"Member of BDLI (German aerospace industries)",
            notes,
        ),
    )
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    sess = requests.Session()
    print("Fetching BDLI listing…")
    urls = list_member_urls(sess)
    print(f"Found {len(urls)} members.")
    if args.limit:
        urls = urls[: args.limit]

    con = sqlite3.connect(DB_PATH)
    already = existing_canonicals(con)
    print(f"Existing BDLI rows: {len(already)}\n")

    n_inserted = 0
    n_skip = 0
    n_err = 0
    t0 = time.time()
    for i, url in enumerate(urls, 1):
        try:
            r = sess.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            if r.status_code != 200:
                n_err += 1
                continue
            data = parse_detail(r.text)
            if not data["name"]:
                n_err += 1
                continue
            if insert_signal(con, url, data):
                n_inserted += 1
            else:
                n_skip += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {url}: {e!r}")
            n_err += 1
        if i % 25 == 0 or i == len(urls):
            con.commit()
            dt = time.time() - t0
            print(f"  [{i:>4}/{len(urls)}] inserted={n_inserted} "
                  f"skipped={n_skip} err={n_err} ({i/dt:.2f}/s)")
        time.sleep(PER_HOST_DELAY)

    con.commit()
    con.close()
    print(f"\nDone in {time.time()-t0:.1f}s : inserted={n_inserted}, "
          f"skipped={n_skip}, err={n_err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
