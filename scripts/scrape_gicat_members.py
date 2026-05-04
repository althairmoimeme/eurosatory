"""Scrape the GICAT (French defense industry association) member directory.

Source : https://gicat.com/annuaire/  (iframe → hubj2c.com)

Pipeline
--------
1. POST https://new-liste-exposants.hubj2c.com/gicat/main/fr/getSettings
       returns full JSON list with 512 members (idSociete + name + …).
2. For each idSociete, POST
       https://new-api.hubj2c.com/form/gicat/main/getView/{id}/-/fr/0
       returns the HTML detail card with :
            - long description
            - address + country flag
            - email + website + LinkedIn
            - multiple "PRINCIPAUX DIRIGEANTS" (nom + fonction)
            - SECTEURS D'INTERVENTIONS / DOMAINES D'ACTIVITÉ (3-level taxonomy)
            - PRODUITS NOUVEAUX

Persist
-------
Per company (1 row, ``entity_type='company'``):
    company_name, country='France'
    notes lines: [email] [website] [linkedin] [phone if any] [address]
                 [secteurs] [domaines] [produits]
    source_platform = 'gicat-fr'

Plus 1 extra row per dirigeant beyond the 1st (entity_type='person',
source_platform='gicat-fr-dirigeant').

Idempotent.
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
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "eurosatory.db"

LISTING_URL = "https://new-liste-exposants.hubj2c.com/gicat/main/fr/getSettings"
DETAIL_URL_TPL = "https://new-api.hubj2c.com/form/gicat/main/getView/{id}/-/fr/0"
INDEX_URL = "https://new-liste-exposants.hubj2c.com/gicat/main/fr"  # for cookie

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Detail HTML parser
# ---------------------------------------------------------------------------


_TAG_RX = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    return unescape(_TAG_RX.sub(" ", html or "")).strip()


# Use the next ``<div class="card "`` opener (start of next section card) as
# the closing boundary — much more reliable than counting closing divs.
_NEXT_CARD = r'(?=<div class="card mt-2"|<div class="card "|<div class="container">|</div>\s*$)'

_ADDRESS_BLOCK_RX = re.compile(
    r'<div class="card-header">ADRESSE</div>(?P<body>.*?)' + _NEXT_CARD,
    re.DOTALL,
)
_DIRIGEANTS_BLOCK_RX = re.compile(
    r'<div class="card-header">PRINCIPAUX DIRIGEANTS</div>(?P<body>.*?)' + _NEXT_CARD,
    re.DOTALL,
)
_SECTEURS_BLOCK_RX = re.compile(
    r"<div class=\"card-header\">SECTEURS D[’']INTERVENTIONS</div>(?P<body>.*?)" + _NEXT_CARD,
    re.DOTALL,
)
_DOMAINES_BLOCK_RX = re.compile(
    r"<div class=\"card-header\">DOMAINES D[’']ACTIVITÉ</div>(?P<body>.*?)" + _NEXT_CARD,
    re.DOTALL,
)
_PRODUITS_BLOCK_RX = re.compile(
    r'<div class="card-header">PRODUITS NOUVEAUX</div>(?P<body>.*?)' + _NEXT_CARD,
    re.DOTALL,
)
_CHIFFRES_BLOCK_RX = re.compile(
    r'<div class="card-header">CHIFFRES CLÉ</div>(?P<body>.*?)' + _NEXT_CARD,
    re.DOTALL,
)
_CORRESPONDANT_BLOCK_RX = re.compile(
    r'<div class="card-header">CORRESPONDANT GICAT</div>(?P<body>.*?)' + _NEXT_CARD,
    re.DOTALL,
)
_DESC_BLOCK_RX = re.compile(
    r'<div class="col-xl-8 mb-2">.*?<p>(?P<desc>.*?)</p>',
    re.DOTALL,
)

_EMAIL_RX = re.compile(r'href="mailto:([^"]+)"')
_WEB_RX = re.compile(r'<a[^>]+href=[\'"]([^\'"]+)[\'"][^>]*>(https?://[^<]+)</a>')
_LINKEDIN_RX = re.compile(
    r'href=[\'"](https?://[^\'"]*linkedin\.com/[^\'"]+)[\'"]'
)
_TWITTER_RX = re.compile(
    r'href=[\'"](https?://[^\'"]*(?:twitter|x)\.com/[^\'"]+)[\'"]'
)
_PHONE_RX = re.compile(r"<a[^>]+href=\"tel:([^\"]+)\">")

_DIRIGEANT_BLOCK_RX = re.compile(
    r'<div class="fe-contact-infos">\s*'
    r'<div class="fe-gras">\s*(?P<name>[^<]+?)\s*</div>\s*'
    r"<div>(?P<role>[^<]*)</div>",
    re.DOTALL,
)


def parse_detail(html: str) -> dict:
    out: dict = {
        "description": "",
        "address": "",
        "country": "",
        "email": "",
        "website": "",
        "linkedin": "",
        "phone": "",
        "dirigeants": [],
        "secteurs": [],
        "domaines": [],
        "produits": [],
        "chiffres": "",
        "correspondant": None,
    }
    if (m := _DESC_BLOCK_RX.search(html)):
        out["description"] = html_to_text(m.group("desc"))[:3000]
    if (m := _ADDRESS_BLOCK_RX.search(html)):
        body = m.group("body")
        # Extract address lines
        addr_lines = []
        for em in re.finditer(r'<div class="(?:fe-adresse|)"[^>]*>([^<]+)</div>', body):
            v = unescape(em.group(1)).strip()
            if v:
                addr_lines.append(v)
        out["address"] = " | ".join(addr_lines)
        # Extract country from drapeau title
        if (cm := re.search(r'class="fe-drapeau"\s+title="([^"]+)"', body)):
            out["country"] = cm.group(1)
        elif (cm := re.search(r'<img[^>]+title="([^"]+)"[^>]+class="fe-drapeau"', body)):
            out["country"] = cm.group(1)
        # email
        if (em := _EMAIL_RX.search(body)):
            out["email"] = em.group(1).strip()
        # website
        if (wm := _WEB_RX.search(body)):
            out["website"] = wm.group(1).strip()
        # linkedin
        if (lm := _LINKEDIN_RX.search(body)):
            out["linkedin"] = lm.group(1).strip()
        # phone
        if (pm := _PHONE_RX.search(body)):
            out["phone"] = pm.group(1).strip()
    if (m := _DIRIGEANTS_BLOCK_RX.search(html)):
        for em in _DIRIGEANT_BLOCK_RX.finditer(m.group("body")):
            name = unescape(em.group("name")).strip()
            role = unescape(em.group("role")).strip()
            if name:
                out["dirigeants"].append({"name": name, "role": role})
    if (m := _SECTEURS_BLOCK_RX.search(html)):
        for em in re.finditer(r'<div class="fe-ligne-secteur">[^<]*(?:<img[^>]*>)?\s*([^<]+)</div>', m.group("body")):
            v = unescape(em.group(1)).strip()
            if v:
                out["secteurs"].append(v)
    if (m := _DOMAINES_BLOCK_RX.search(html)):
        # 3-level hierarchy n1/n2/n3
        for em in re.finditer(r'<div class="fe-main-activities n[123]">([^<]+)</div>', m.group("body")):
            v = unescape(em.group(1)).strip()
            if v:
                out["domaines"].append(v)
    if (m := _PRODUITS_BLOCK_RX.search(html)):
        for em in re.finditer(r'<div class="fe-titre-produit">([^<]+)</div>', m.group("body")):
            v = unescape(em.group(1)).strip()
            if v:
                out["produits"].append(v)
    if (m := _CHIFFRES_BLOCK_RX.search(html)):
        # Get the inner text — usually "Chiffre d'affaire total : N M €<br>Effectif : N<br>..."
        body = m.group("body")
        # Replace <br> with " · "
        body = re.sub(r"<br\s*/?>", " · ", body)
        out["chiffres"] = html_to_text(body)[:500]
    if (m := _CORRESPONDANT_BLOCK_RX.search(html)):
        body = m.group("body")
        em_name = re.search(r'<div class="fe-gras">\s*([^<]+?)\s*</div>', body)
        em_role = re.search(r'<div class="fe-gras">\s*[^<]+?\s*</div>\s*<div>([^<]*)</div>', body)
        em_email = _EMAIL_RX.search(body)
        em_phone = _PHONE_RX.search(body)
        if em_name:
            out["correspondant"] = {
                "name": unescape(em_name.group(1)).strip(),
                "role": unescape(em_role.group(1)).strip() if em_role else "",
                "email": em_email.group(1).strip() if em_email else "",
                "phone": em_phone.group(1).strip() if em_phone else "",
            }
    return out


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def canonical(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").lower().strip())
    n = re.sub(
        r"\s+(sas|sa|sarl|eurl|sasu|snc|gie|ltd|gmbh|ag)\.?$", "", n,
    )
    return n.strip()[:80]


def existing_canonicals(con: sqlite3.Connection, platform: str) -> set[str]:
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT canonical_company_name FROM attendance_signals "
        "WHERE source_platform = ?",
        (platform,),
    )
    return {r[0] for r in cur.fetchall() if r[0]}


def insert_company(
    con: sqlite3.Connection, name: str, idSociete: str, data: dict,
) -> bool:
    canon = canonical(name)
    if not canon:
        return False
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM attendance_signals "
        "WHERE source_platform='gicat-fr' AND canonical_company_name=? LIMIT 1",
        (canon,),
    )
    if cur.fetchone():
        return False  # idempotent

    primary = data["dirigeants"][0] if data["dirigeants"] else None
    notes_lines: list[str] = []
    if data.get("email"):
        notes_lines.append(f"[email] {data['email']}")
    if data.get("phone"):
        notes_lines.append(f"[phone] {data['phone']}")
    if data.get("linkedin"):
        notes_lines.append(f"[linkedin] {data['linkedin']}")
    if data.get("website"):
        notes_lines.append(f"[website] {data['website']}")
    if data.get("address"):
        notes_lines.append(f"[address] {data['address']}")
    if data["secteurs"]:
        notes_lines.append("[secteurs] " + " · ".join(data["secteurs"][:10]))
    if data["domaines"]:
        notes_lines.append("[domaines] " + " · ".join(data["domaines"][:25]))
    if data["produits"]:
        notes_lines.append("[produits] " + " · ".join(data["produits"][:10]))
    if data.get("chiffres"):
        notes_lines.append(f"[chiffres] {data['chiffres']}")
    corresp = data.get("correspondant")
    if corresp:
        notes_lines.append(
            f"[correspondant] {corresp['name']}"
            + (f" ({corresp['role']})" if corresp.get("role") else "")
            + (f" — {corresp['email']}" if corresp.get("email") else "")
            + (f" — {corresp['phone']}" if corresp.get("phone") else "")
        )
    notes = "\n".join(notes_lines)[:8000]

    entity_type = "person" if primary else "company"
    person_name = primary["name"][:255] if primary else None
    person_role = primary["role"][:255] if primary else None

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
            ?, ?, 'France',
            'gicat-fr', ?, ?, ?,
            'trade_association_member', ?,
            'Listed in GICAT — Groupement des Industries de Défense et de Sécurité Terrestres et Aéroterrestres.',
            1, 0, 0, 0, 0,
            'high', 'pending', ?
        )""",
        (
            entity_type, person_name, person_role,
            name[:400], canon,
            f"https://gicat.com/annuaire/?id={idSociete}"[:900],
            f"GICAT member: {name}"[:400],
            (data.get("description") or "")[:1500],
            f"Member of GICAT (FR)" + (
                f" — Dirigeant: {primary['name']}" if primary else ""
            ),
            notes,
        ),
    )
    parent_id = cur.lastrowid

    # Insert additional dirigeants beyond the 1st
    for d in data["dirigeants"][1:]:
        notes2 = []
        if data.get("email"):
            notes2.append(f"[email] {data['email']}")
        if data.get("phone"):
            notes2.append(f"[phone] {data['phone']}")
        notes2_s = "\n".join(notes2)[:2000]
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
                ?, ?, 'France',
                'gicat-fr-dirigeant', ?, ?, ?,
                'leadership_lookup', ?,
                'Additional dirigeant on the GICAT member page.',
                0, 0, 0, 0, 0,
                'high', 'pending', ?
            )""",
            (
                d["name"][:255], (d.get("role") or "")[:255],
                name[:400], canon,
                f"https://gicat.com/annuaire/?id={idSociete}"[:900],
                f"{d['name']} — {d.get('role') or ''} @ {name}"[:400],
                "",
                f"{d['name']} ({d.get('role') or '?'}) chez {name}"[:500],
                notes2_s,
            ),
        )
    return True


# ---------------------------------------------------------------------------
# Async fetch
# ---------------------------------------------------------------------------


async def fetch_detail(client: httpx.AsyncClient, idSociete: str) -> Optional[str]:
    try:
        r = await client.post(
            DETAIL_URL_TPL.format(id=idSociete),
            timeout=30,
            headers={
                "Origin": "https://new-liste-exposants.hubj2c.com",
                "Referer": "https://new-liste-exposants.hubj2c.com/gicat/main/fr",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if r.status_code != 200:
            return None
        return r.text
    except Exception:  # noqa: BLE001
        return None


async def main_async(args) -> int:
    # Step 1: get full member list
    sess = requests.Session()
    sess.get(INDEX_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    r = sess.post(
        LISTING_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Origin": "https://new-liste-exposants.hubj2c.com",
            "Referer": "https://new-liste-exposants.hubj2c.com/gicat/main/fr",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
    )
    r.raise_for_status()
    members = r.json().get("list", [])
    print(f"Listing: {len(members)} GICAT members")
    if args.limit:
        members = members[: args.limit]

    con = sqlite3.connect(DB_PATH)
    already = existing_canonicals(con, "gicat-fr")
    print(f"Existing GICAT rows: {len(already)}")

    n_inserted = 0
    n_skipped = 0
    n_err = 0
    n_dirig = 0
    t0 = time.time()
    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
    ) as client:

        async def process(m):
            nonlocal n_inserted, n_skipped, n_err, n_dirig
            async with sem:
                idSociete = m.get("idSociete")
                name = m.get("exposant") or ""
                if not idSociete or not name:
                    return
                if canonical(name) in already:
                    return
                html = await fetch_detail(client, idSociete)
                if not html:
                    return ("err",)
                data = parse_detail(html)
                # Track via thread-safe-ish (single thread anyway)
                return ("ok", idSociete, name, data)

        tasks = [process(m) for m in members]
        n_done = 0
        for coro in asyncio.as_completed(tasks):
            res = await coro
            n_done += 1
            if res is None:
                n_skipped += 1
            elif res[0] == "err":
                n_err += 1
            else:
                _, idSociete, name, data = res
                if insert_company(con, name, idSociete, data):
                    n_inserted += 1
                    n_dirig += max(0, len(data["dirigeants"]) - 1)
                else:
                    n_skipped += 1
            if n_done % 25 == 0 or n_done == len(members):
                con.commit()
                dt = time.time() - t0
                rate = n_done / dt if dt > 0 else 0
                print(
                    f"  [{n_done:>4}/{len(members)}] "
                    f"inserted={n_inserted} extra_dirig={n_dirig} "
                    f"skipped={n_skipped} err={n_err} ({rate:.2f}/s)"
                )

    con.commit()
    con.close()
    dt = time.time() - t0
    print(
        f"\nDone in {dt:.1f}s : inserted={n_inserted}, "
        f"extra_dirigeants={n_dirig}, skipped={n_skipped}, err={n_err}"
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
