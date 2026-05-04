"""Scrape the BDSV member directory (German federal defence industry assoc).

Source : https://www.bdsv.eu/mitglieder.html  → ~443 member companies.

Per-member detail page exposes (fully public) :
    company_name      <h1>
    street + postal/city  <p>
    dl.facts:
        Ansprechpartner  → contact person full name
        Telefon          → phone
        E-Mail           → corporate email
        Web              → website
        LinkedIn         → handle / URL
    description (Beschreibung)
    portfolio (Portfolio / Leistungen / Kompetenzen)

Persists into ``attendance_signals`` as ``entity_type='person'`` rows
(each fiche names an Ansprechpartner) with full contact info in
``notes`` (existing convention `[email] …`, `[linkedin] …`).

Usage
-----
    python -m scripts.scrape_bdsv_members --limit 5    # smoke test
    python -m scripts.scrape_bdsv_members              # full run
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
LISTING_URL = "https://www.bdsv.eu/mitglieder.html"
DETAIL_URL = "https://www.bdsv.eu/mitglieder/mitglied-details.html?show={}"
USER_AGENT = (
    "Mozilla/5.0 (compatible; eurosatory-scraper/1.0; "
    "+contact: a.bertantoine@gmail.com)"
)
PER_HOST_DELAY = 0.5


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------


_ID_RX = re.compile(r"mitglied-details\.html\?show=(\d+)")
_H1_RX = re.compile(r"<h1>([^<]+)</h1>", re.DOTALL)
_ADDR_RX = re.compile(
    r'<div class="col-md-8">\s*<p>([^<]+(?:<br\s*/?>[^<]*)*)</p>',
    re.DOTALL,
)
_FACTS_RX = re.compile(
    r'<dl class="facts">\s*(.*?)\s*</dl>', re.DOTALL,
)
_DT_DD_RX = re.compile(
    r"<dt>\s*([^<]+?)\s*</dt>\s*<dd>\s*([^<]+?)\s*</dd>", re.DOTALL,
)
_BESCHR_RX = re.compile(
    r"<h2>\s*Beschreibung\s*</h2>\s*((?:<p>.*?</p>\s*)+)", re.DOTALL,
)
_PORTFOLIO_RX = re.compile(
    r"<h2>\s*Portfolio\s*</h2>\s*((?:<p>.*?</p>\s*)+)", re.DOTALL,
)
_TAG_RX = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    return unescape(_TAG_RX.sub(" ", html or "")).strip()


def list_member_ids(session: requests.Session) -> list[int]:
    r = session.get(LISTING_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    ids = sorted({int(m) for m in _ID_RX.findall(r.text)})
    return ids


def parse_detail(html: str) -> dict:
    out: dict[str, str] = {}
    if (m := _H1_RX.search(html)):
        out["name"] = unescape(m.group(1)).strip()
    # Address — first <p> in the col-md-8 block. The capture above grabs
    # the inner text containing <br/> separators.
    if (m := _ADDR_RX.search(html)):
        addr_html = m.group(1)
        bits = [
            unescape(b).strip()
            for b in re.split(r"<br\s*/?>", addr_html)
            if b.strip()
        ]
        out["address"] = " | ".join(bits)
    # dl.facts
    if (m := _FACTS_RX.search(html)):
        for dt, dd in _DT_DD_RX.findall(m.group(1)):
            label = unescape(dt).strip()
            value = unescape(dd).strip()
            out[f"fact_{label}"] = value
    if (m := _BESCHR_RX.search(html)):
        out["beschreibung"] = html_to_text(m.group(1))[:3000]
    if (m := _PORTFOLIO_RX.search(html)):
        out["portfolio"] = html_to_text(m.group(1))[:3000]
    return out


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def canonical(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").lower().strip())
    n = re.sub(
        r"\s+(gmbh|gmbh\s*&\s*co\.?\s*kg|ag|kg|ohg|se|mbh|gbr|ug|e\.v\.|ev)\.?$",
        "", n,
    )
    return n.strip()[:80]


def existing_canonicals(con: sqlite3.Connection) -> set[str]:
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT canonical_company_name "
        "FROM attendance_signals WHERE source_platform='bdsv-de'"
    )
    return {r[0] for r in cur.fetchall() if r[0]}


def insert_signal(con: sqlite3.Connection, mid: int, data: dict) -> bool:
    name = data.get("name") or ""
    if not name:
        return False
    canon = canonical(name)
    if not canon:
        return False
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM attendance_signals WHERE source_platform='bdsv-de' "
        "AND canonical_company_name = ? LIMIT 1",
        (canon,),
    )
    if cur.fetchone():
        return False  # idempotent

    person = data.get("fact_Ansprechpartner") or ""
    phone = data.get("fact_Telefon") or ""
    email = data.get("fact_E-Mail") or ""
    website = data.get("fact_Web") or ""
    linkedin = data.get("fact_LinkedIn") or ""
    address = data.get("address") or ""
    descr = data.get("beschreibung") or ""
    portfolio = data.get("portfolio") or ""

    notes_lines: list[str] = []
    if email:
        notes_lines.append(f"[email] {email}")
    if phone:
        notes_lines.append(f"[phone] {phone}")
    if linkedin:
        notes_lines.append(f"[linkedin] {linkedin}")
    if website:
        notes_lines.append(f"[website] {website}")
    if address:
        notes_lines.append(f"[address] {address}")
    if portfolio:
        notes_lines.append(f"[portfolio] {portfolio[:1500]}")
    notes = "\n".join(notes_lines)[:8000]

    entity_type = "person" if person else "company"
    signal_text = f"Member of BDSV (German Defence Industry Association)"
    if person:
        signal_text += f" — Ansprechpartner: {person}"

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
            2026, ?, ?, NULL,
            ?, ?, 'Germany',
            'bdsv-de', ?, ?, ?,
            'trade_association_member', ?,
            'Listed in BDSV (Bundesverband der Deutschen Sicherheits- und Verteidigungsindustrie) — full company contact published.',
            1, 0, 0, 0, 0,
            'high', 'pending', ?
        )""",
        (
            entity_type,
            person[:255] if person else None,
            name[:400], canon,
            DETAIL_URL.format(mid)[:900],
            f"BDSV member: {name}"[:400],
            descr[:1500],
            signal_text[:500],
            notes,
        ),
    )
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    sess = requests.Session()
    headers = {"User-Agent": USER_AGENT}

    print("Fetching BDSV directory…")
    ids = list_member_ids(sess)
    print(f"Found {len(ids)} member IDs.")
    if args.limit:
        ids = ids[: args.limit]

    con = sqlite3.connect(DB_PATH)
    already = existing_canonicals(con)
    print(f"Existing BDSV rows in DB: {len(already)}")

    n_ok = 0
    n_skip = 0
    n_err = 0
    t0 = time.time()
    for i, mid in enumerate(ids, 1):
        try:
            r = sess.get(DETAIL_URL.format(mid), headers=headers, timeout=30)
            if r.status_code != 200:
                n_err += 1
                continue
            data = parse_detail(r.text)
            if not data.get("name"):
                n_err += 1
                continue
            inserted = insert_signal(con, mid, data)
            if inserted:
                n_ok += 1
            else:
                n_skip += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ id={mid}: {e!r}")
            n_err += 1
        if i % 25 == 0 or i == len(ids):
            con.commit()
            dt = time.time() - t0
            print(
                f"  [{i:>4}/{len(ids)}] ok={n_ok} skipped={n_skip} "
                f"err={n_err} ({i/dt:.2f}/s)"
            )
        time.sleep(PER_HOST_DELAY)

    con.commit()
    con.close()
    print(
        f"\nDone in {time.time()-t0:.1f}s : ok={n_ok}, "
        f"skipped={n_skip}, err={n_err}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
