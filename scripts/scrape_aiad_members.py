"""Scrape the AIAD member directory (Italian defense industry assoc).

Source : https://aiad.it/aziende-federate/  (~260 member companies)

Per-member detail page (e.g.
https://aiad.it/aziende-federate/mpg-instruments-2026/?lang=en) exposes :

    Company name, Company Dimension (Small/Medium/Large), Share capital
    CEO, CFO, COO (multi-named officers)
    Headquarter address (street + postal + locality + region)
    Phone, Fax, ROT13-encoded Email, Website
    Other Locations
    Products and activities (description)

Emails are ROT13-encoded inside ``data-enc-email="local[at]domain.tld"``
attributes — we decode them.

Persist into attendance_signals as ``source_platform='aiad-it'`` (CEO row)
and ``aiad-it-officer`` rows for CFO/COO.
"""
from __future__ import annotations

import argparse
import codecs
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
LIST_URL = "https://aiad.it/aziende-federate/page/{}/"
LIST_URL_FIRST = "https://aiad.it/aziende-federate/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; eurosatory-scraper/1.0; "
    "+contact: a.bertantoine@gmail.com)"
)
PER_HOST_DELAY = 0.4


# ---------------------------------------------------------------------------
# Listing parser
# ---------------------------------------------------------------------------


_MEMBER_LINK_RX = re.compile(
    r'href="(https?://aiad\.it/aziende-federate/[a-z0-9-]+/?)"'
)


def list_member_urls(session: requests.Session, max_pages: int = 30) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for page in range(1, max_pages + 1):
        url = LIST_URL_FIRST if page == 1 else LIST_URL.format(page)
        r = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code != 200:
            break
        members_on_page = []
        for m in _MEMBER_LINK_RX.findall(r.text):
            m = m.rstrip("/") + "/"
            if "/feed" in m or "/page/" in m or m.endswith("/aziende-federate/"):
                continue
            if m not in seen_set:
                seen_set.add(m)
                members_on_page.append(m)
        if not members_on_page:
            break
        seen.extend(members_on_page)
        print(f"  page {page}: {len(members_on_page)} new (total={len(seen)})")
        time.sleep(PER_HOST_DELAY)
    return seen


# ---------------------------------------------------------------------------
# Detail parser
# ---------------------------------------------------------------------------


_TAG_RX = re.compile(r"<[^>]+>")


def html_to_text(s: str) -> str:
    return unescape(_TAG_RX.sub(" ", s or "")).strip()


def decode_rot13_email(s: str) -> Optional[str]:
    """Decode strings like 'nvnq[at]nvnq.vg' → 'aiad@aiad.it'."""
    if not s or "[at]" not in s:
        return None
    try:
        decoded = codecs.decode(s.replace("[at]", "@"), "rot_13")
        return decoded if "@" in decoded and "." in decoded.split("@")[-1] else None
    except Exception:  # noqa: BLE001
        return None


# Scope all company-data extraction to the company <article> body so we
# never accidentally pick up the AIAD federation's own footer (which has
# its own `data-enc-email`, telephone, etc).
_ARTICLE_RX = re.compile(
    r'<article[^>]+id="post-\d+"[^>]*>(.*?)</article>',
    re.DOTALL,
)

_TITLE_RX = re.compile(
    r'<strong class="breadcrumb_last"[^>]*>([^<]+)</strong>'
)
_DIMENSION_RX = re.compile(
    r'<span>Company Dimension</span>\s*<p>([^<]+)</p>', re.I,
)
_SHARE_CAP_RX = re.compile(
    r'<span>Share capital</span>\s*<p>([^<]+)</p>', re.I,
)
_OFFICER_RX = re.compile(
    r'<span>(CEO|CFO|COO|Chairman|President|VP|MD|Director General|'
    r'General Manager)</span>\s*<p>([^<]+)</p>',
    re.I,
)
_HEADQUARTER_RX = re.compile(
    r'<span>Headquarter</span>\s*<p>([^<]+)</p>', re.I,
)
# Phone / Fax / Email scoped to the company info__item icon blocks
_PHONE_ITEM_RX = re.compile(
    r'data-icon="tel"[^>]*>.*?<a[^>]+href="tel:([^"]+)"',
    re.DOTALL,
)
_FAX_ITEM_RX = re.compile(
    r'data-icon="fax"[^>]*>.*?<a[^>]+href="(?:fax|tel):([^"]+)"',
    re.DOTALL,
)
_EMAIL_ITEM_RX = re.compile(
    r'data-icon="mail"[^>]*>.*?data-enc-email="([^"]+)"',
    re.DOTALL,
)
_URL_RX = re.compile(
    r'<span>Url</span>\s*<a[^>]+href="(https?://[^"]+)"', re.I,
)
_OTHER_LOC_RX = re.compile(
    r'<span class="info__itemTitle">Other Locations</span>(.*?)(?=<li class="info__item icon|</ul>)',
    re.DOTALL,
)
_DESC_BLOCK_RX = re.compile(
    r"Products and activities.*?"
    r'<div class="post-content">(?P<body>.*?)</div>',
    re.DOTALL,
)


def parse_detail(html: str) -> dict:
    out: dict = {
        "name": "", "dimension": "", "share_capital": "",
        "officers": [],
        "hq_address": "",
        "phone": "", "fax": "", "email": "", "website": "",
        "other_locations": "",
        "description": "",
    }
    # Title is in the breadcrumb (whole document); rest scoped to article.
    if (m := _TITLE_RX.search(html)):
        out["name"] = unescape(m.group(1)).strip()

    a = _ARTICLE_RX.search(html)
    body = a.group(1) if a else html

    if (m := _DIMENSION_RX.search(body)):
        out["dimension"] = unescape(m.group(1)).strip()
    if (m := _SHARE_CAP_RX.search(body)):
        out["share_capital"] = unescape(m.group(1)).strip()
    for em in _OFFICER_RX.finditer(body):
        role = em.group(1).strip()
        name = unescape(em.group(2)).strip()
        if name:
            out["officers"].append({"role": role, "name": name})
    if (m := _HEADQUARTER_RX.search(body)):
        out["hq_address"] = unescape(m.group(1)).strip()
    if (m := _PHONE_ITEM_RX.search(body)):
        out["phone"] = re.sub(r"\s+", " ", m.group(1).strip())
    if (m := _FAX_ITEM_RX.search(body)):
        out["fax"] = re.sub(r"\s+", " ", m.group(1).strip())
    if (m := _EMAIL_ITEM_RX.search(body)):
        em_decoded = decode_rot13_email(m.group(1))
        if em_decoded:
            out["email"] = em_decoded
    if (m := _URL_RX.search(body)):
        out["website"] = m.group(1).strip()
    if (m := _OTHER_LOC_RX.search(body)):
        out["other_locations"] = html_to_text(m.group(1))[:500]
    if (m := _DESC_BLOCK_RX.search(body)):
        out["description"] = html_to_text(m.group(1))[:3000]
    return out


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def canonical(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").lower().strip())
    n = re.sub(r"\s+(srl|spa|s\.p\.a\.|s\.r\.l\.|sas|gmbh|ltd)\.?$", "", n)
    n = re.sub(r"\s+\d{4}$", "", n)  # drop trailing year e.g. "MPG INSTRUMENTS 2026"
    return n.strip()[:80]


def existing_canonicals(con: sqlite3.Connection) -> set[str]:
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT canonical_company_name FROM attendance_signals "
        "WHERE source_platform='aiad-it'"
    )
    return {r[0] for r in cur.fetchall() if r[0]}


def insert_aiad(con: sqlite3.Connection, url: str, data: dict) -> tuple[int, int]:
    name = data.get("name") or ""
    if not name:
        return 0, 0
    canon = canonical(name)
    if not canon:
        return 0, 0
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM attendance_signals WHERE source_platform='aiad-it' "
        "AND canonical_company_name = ? LIMIT 1",
        (canon,),
    )
    if cur.fetchone():
        return 0, 0

    primary = data["officers"][0] if data["officers"] else None
    address = (data.get("hq_address") or "").strip()

    notes_lines = []
    if data.get("email"):
        notes_lines.append(f"[email] {data['email']}")
    if data.get("phone"):
        notes_lines.append(f"[phone] {data['phone']}")
    if data.get("fax"):
        notes_lines.append(f"[fax] {data['fax']}")
    if data.get("website"):
        notes_lines.append(f"[website] {data['website']}")
    if address:
        notes_lines.append(f"[address] {address}")
    if data.get("other_locations"):
        notes_lines.append(f"[other_locations] {data['other_locations']}")
    if data.get("dimension"):
        notes_lines.append(f"[dimension] {data['dimension']}")
    if data.get("share_capital"):
        notes_lines.append(f"[share_capital] {data['share_capital']}")
    notes = "\n".join(notes_lines)[:8000]

    entity_type = "person" if primary else "company"
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
            2026, ?, ?, ?,
            ?, ?, 'Italy',
            'aiad-it', ?, ?, ?,
            'trade_association_member', ?,
            'Listed in AIAD — Italian defense industry association.',
            1, 0, 0, 0, 0,
            'high', 'pending', ?
        )""",
        (
            entity_type,
            primary["name"][:255] if primary else None,
            primary["role"][:255] if primary else None,
            name[:400], canon,
            url[:900],
            f"AIAD member: {name}"[:400],
            (data.get("description") or "")[:1500],
            f"Member of AIAD (Italian defense industry)" + (
                f" — {primary['role']}: {primary['name']}" if primary else ""
            ),
            notes,
        ),
    )
    n_extra = 0
    for off in data["officers"][1:]:
        notes2_lines = []
        if data.get("email"):
            notes2_lines.append(f"[email] {data['email']}")
        if data.get("phone"):
            notes2_lines.append(f"[phone] {data['phone']}")
        notes2 = "\n".join(notes2_lines)[:2000]
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
                ?, ?, 'Italy',
                'aiad-it-officer', ?, ?, ?,
                'leadership_lookup', ?,
                'Additional officer (CFO/COO/etc.) on the AIAD member page.',
                0, 0, 0, 0, 0,
                'high', 'pending', ?
            )""",
            (
                off["name"][:255], off["role"][:255],
                name[:400], canon,
                url[:900],
                f"{off['name']} ({off['role']}) @ {name}"[:400],
                "",
                f"{off['name']} ({off['role']}) chez {name}"[:500],
                notes2,
            ),
        )
        n_extra += 1
    return 1, n_extra


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    sess = requests.Session()
    print("Crawling listing pages…")
    urls = list_member_urls(sess)
    print(f"\nTotal AIAD members found: {len(urls)}")
    if args.limit:
        urls = urls[: args.limit]

    con = sqlite3.connect(DB_PATH)
    already = existing_canonicals(con)
    print(f"Existing AIAD rows: {len(already)}\n")

    n_inserted = 0
    n_extra_total = 0
    n_skip = 0
    n_err = 0
    t0 = time.time()
    for i, url in enumerate(urls, 1):
        try:
            r = sess.get(
                url + "?lang=en",
                headers={"User-Agent": USER_AGENT}, timeout=30,
            )
            if r.status_code != 200:
                n_err += 1
                continue
            data = parse_detail(r.text)
            if not data["name"]:
                n_err += 1
                continue
            inserted, extra = insert_aiad(con, url, data)
            if inserted:
                n_inserted += 1
                n_extra_total += extra
            else:
                n_skip += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {url}: {e!r}")
            n_err += 1
        if i % 20 == 0 or i == len(urls):
            con.commit()
            dt = time.time() - t0
            print(
                f"  [{i:>4}/{len(urls)}] inserted={n_inserted} "
                f"officers+={n_extra_total} skipped={n_skip} err={n_err} "
                f"({i/dt:.2f}/s)"
            )
        time.sleep(PER_HOST_DELAY)

    con.commit()
    con.close()
    print(f"\nDone in {time.time()-t0:.1f}s : {n_inserted} companies + "
          f"{n_extra_total} officers, {n_skip} skipped, {n_err} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
