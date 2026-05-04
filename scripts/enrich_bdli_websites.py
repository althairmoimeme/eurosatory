"""Scrape each BDLI member's own website to extract contact data.

BDLI publishes only website + address (no email, phone, contact). But
every German B2B site has a legally-required ``/impressum`` page with :

    - Geschäftsführer (Managing Director) — name(s)
    - Email
    - Phone / Fax
    - Full address

We fetch the homepage, find the Impressum link, fetch it, then extract :
    - emails        (regex)
    - phones        (tel: links + regex)
    - Geschäftsführer / contact names (regex on common patterns)

Persist back into the BDLI ``attendance_signals`` row :
    - person_name + person_role  (from Geschäftsführer)
    - notes:  [email] / [phone] / [impressum]

Idempotent: skips rows that already have ``[impressum]`` in notes.

Usage:
    python -m scripts.enrich_bdli_websites              # all 310 sites
    python -m scripts.enrich_bdli_websites --limit 5    # smoke
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
from urllib.parse import urljoin, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "eurosatory.db"
USER_AGENT = (
    "Mozilla/5.0 (compatible; eurosatory-scraper/1.0; "
    "+contact: a.bertantoine@gmail.com)"
)

# Common paths for legal contact / imprint
IMPRINT_HINTS = [
    "/impressum", "/impressum/", "/imprint", "/imprint/",
    "/legal", "/legal-notice", "/legal/",
    "/kontakt", "/kontakt/", "/contact", "/contact/",
    "/about", "/about-us", "/ueber-uns",
]

EMAIL_RX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
PHONE_RX = re.compile(
    r"(\+?\d{1,3}[\s.\-]?\(?\d{1,5}\)?[\s.\-]?\d{1,5}[\s.\-]?\d{1,5}[\s.\-]?\d{0,5})"
)
TEL_HREF_RX = re.compile(r'href="tel:([^"]+)"')

# Geschäftsführer / Managing Director patterns. Names capture is bounded
# to AT MOST 5 word-like tokens and stops on legal boilerplate words.
# We deliberately use a token regex with whitespace separators so the
# `\b` boundary stops on any non-letter (Vereinsregister etc.).
_NAME_TOK = r"(?:Dr\.|Prof\.|Dipl\.|Ing\.|-Ing\.|von|der|den|van|zu)?\s*[A-ZÄÖÜ][a-zA-ZäöüßÄÖÜ\-]{1,30}"
_NAME_PAT = rf"({_NAME_TOK}(?:\s+{_NAME_TOK}){{1,4}})"
# Words that should never be inside a captured name — common legal text
# that follows the name without a proper separator on bad German sites.
_NAME_STOP_RX = re.compile(
    r"\b(Vereinsregister|Registergericht|Registernummer|Handelsregister|"
    r"Sitz\s+der\s+Gesellschaft|USt|Umsatzsteuer|Steuernummer|"
    r"HRB|HRA|Inhalt|verantwortlich|gemäß|gemaess|"
    r"\d)\b",
    re.IGNORECASE,
)
ROLE_PATTERNS = [
    (re.compile(rf"Geschäftsführ(?:er|erin|ung|er/in)\s*[:\-]?\s*{_NAME_PAT}"),
     "Geschäftsführer"),
    (re.compile(rf"Managing\s+Director\s*[:\-]?\s*{_NAME_PAT}"),
     "Managing Director"),
    (re.compile(rf"Vertretungsberechtigt(?:er\s+Gesellschafter|er|e)?\s*[:\-]?\s*{_NAME_PAT}"),
     "Vertretungsberechtigt"),
    (re.compile(rf"\bCEO\s*[:\-]?\s*{_NAME_PAT}"),
     "CEO"),
    (re.compile(rf"Vorstand(?:svorsitzende[r]?)?\s*[:\-]?\s*{_NAME_PAT}"),
     "Vorstand"),
    (re.compile(rf"Inhaber(?:in)?\s*[:\-]?\s*{_NAME_PAT}"),
     "Inhaber"),
]

_TAG_RX = re.compile(r"<[^>]+>")
_LINK_IMPRINT_RX = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>\s*(?:'
    r"impressum|imprint|legal\s*notice|mentions?\s*l[ée]gales|"
    r"kontakt|contact"
    r')\s*</a>',
    re.IGNORECASE,
)


def html_to_text(html: str) -> str:
    return unescape(_TAG_RX.sub(" ", html or "")).strip()


def find_impressum_url(html: str, base_url: str) -> Optional[str]:
    """Return absolute URL of the impressum/contact page, if linked."""
    for m in _LINK_IMPRINT_RX.finditer(html or ""):
        href = m.group(1)
        # Skip self-referential anchors
        if href.startswith("#") or "javascript" in href.lower():
            continue
        return urljoin(base_url, href)
    return None


def extract_emails(text: str, html: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    # mailto: links first (most reliable)
    for em in re.findall(r'href="mailto:([^"?]+)', html or ""):
        em_clean = em.strip().lower()
        if "@" in em_clean and em_clean not in seen:
            seen.add(em_clean)
            out.append(em_clean)
    # Then plain regex on rendered text
    for em in EMAIL_RX.findall(text or ""):
        em_clean = em.strip().lower()
        if em_clean in seen:
            continue
        # Skip image / asset filenames
        if any(em_clean.endswith(s) for s in (".png", ".jpg", ".gif", ".svg",
                                                ".webp")):
            continue
        # Skip obvious noise
        if any(s in em_clean for s in ("@2x.", "@3x.", "wixpress.com",
                                        "sentry.io", "@sentry")):
            continue
        seen.add(em_clean)
        out.append(em_clean)
    return out[:10]


def extract_phones(text: str, html: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ph in TEL_HREF_RX.findall(html or ""):
        ph_clean = re.sub(r"\s+", " ", ph.strip())
        if ph_clean and ph_clean not in seen:
            seen.add(ph_clean)
            out.append(ph_clean)
    return out[:5]


def extract_contacts(text: str) -> list[dict]:
    """Find Geschäftsführer / Managing Director / etc in the text."""
    out: list[dict] = []
    seen: set[str] = set()
    for pat, role in ROLE_PATTERNS:
        for m in pat.finditer(text):
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            # Trim trailing dot/comma
            name = name.rstrip(".,;:")
            # Cut at first stop-word that slipped in
            stop = _NAME_STOP_RX.search(name)
            if stop:
                name = name[:stop.start()].strip().rstrip(",.;:")
            # Reject if too short / too long / contains digits
            if not (4 <= len(name) <= 60):
                continue
            if re.search(r"\d", name):
                continue
            if name.lower() in seen:
                continue
            # Reject obvious noise / generic words that begin with capital
            tokens = name.split()
            if not tokens:
                continue
            # Need at least one non-honorific token
            non_honor = [
                t for t in tokens
                if t.lower().rstrip(".") not in (
                    "dr", "prof", "dipl", "ing", "-ing", "herr", "frau",
                    "von", "der", "den", "van", "zu", "und", "&",
                )
            ]
            if len(non_honor) < 2:
                continue
            seen.add(name.lower())
            out.append({"name": name, "role": role})
    return out[:5]


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------


def load_targets(con: sqlite3.Connection,
                 limit: Optional[int]) -> list[dict]:
    cur = con.cursor()
    cur.execute(
        "SELECT id, company_name, canonical_company_name, notes "
        "FROM attendance_signals "
        "WHERE source_platform = 'bdli-de' "
        "  AND notes LIKE '%[website]%' "
        "  AND (notes IS NULL OR notes NOT LIKE '%[impressum]%') "
        "ORDER BY id"
    )
    out = []
    for r in cur.fetchall():
        notes = r[3] or ""
        m = re.search(r"\[website\]\s*([^\s\n]+)", notes)
        if not m:
            continue
        url = m.group(1).strip()
        if not url.startswith("http"):
            url = "http://" + url
        out.append({
            "id": r[0], "company": r[1],
            "canonical": r[2], "url": url, "notes": notes,
        })
    if limit:
        out = out[:limit]
    return out


def replace_or_add(notes: str, prefix: str, value: str) -> str:
    line = f"{prefix} {value}"
    lines = (notes or "").splitlines()
    out = []
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


def update_signal(
    con: sqlite3.Connection,
    sid: int, current_notes: str,
    emails: list[str], phones: list[str],
    contacts: list[dict],
    impressum_url: Optional[str],
) -> dict:
    upd: dict[str, str] = {}
    new_notes = current_notes
    if emails:
        new_notes = replace_or_add(new_notes, "[email]", emails[0])
    if phones:
        new_notes = replace_or_add(new_notes, "[phone]", phones[0])
    if impressum_url:
        new_notes = replace_or_add(new_notes, "[impressum]", impressum_url)
    if contacts:
        upd["person_name"] = contacts[0]["name"][:255]
        upd["person_role"] = contacts[0]["role"][:255]
        upd["entity_type"] = "person"
    if new_notes != current_notes:
        upd["notes"] = new_notes[:8000]
    if not upd:
        return upd
    cur = con.cursor()
    sets = ", ".join(f"{k} = ?" for k in upd)
    cur.execute(
        f"UPDATE attendance_signals SET {sets} WHERE id = ?",
        list(upd.values()) + [sid],
    )
    return upd


def insert_extra_contacts(
    con: sqlite3.Connection,
    parent_id: int, parent_company: str, parent_canonical: str,
    parent_url: str,
    contacts: list[dict],
) -> int:
    """Insert 2nd, 3rd…, named contacts as separate person rows."""
    if len(contacts) <= 1:
        return 0
    cur = con.cursor()
    n = 0
    for c in contacts[1:]:
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
                ?, ?, 'Germany',
                'bdli-de-2nd', ?, ?, ?,
                'leadership_lookup', ?,
                'Additional Geschäftsführer / contact extracted from the company Impressum.',
                0, 0, 0, 0, 0,
                'medium', 'pending', ''
            )""",
            (
                c["name"][:255], c["role"][:255],
                parent_company[:400], parent_canonical[:80],
                parent_url[:900],
                f"{c['name']} ({c['role']}) @ {parent_company}"[:400],
                "",
                f"{c['name']} ({c['role']}) chez {parent_company}"[:500],
            ),
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


async def fetch(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:  # noqa: BLE001
        return None


async def harvest_one(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    target: dict,
) -> dict:
    """Fetch homepage + impressum, extract data."""
    async with sem:
        result = {
            "id": target["id"],
            "emails": [], "phones": [], "contacts": [],
            "impressum_url": None,
        }
        homepage = await fetch(client, target["url"])
        if not homepage:
            return result
        # Try discovered impressum link first
        impr_url = find_impressum_url(homepage, target["url"])
        impr_html = ""
        if impr_url:
            impr_html = await fetch(client, impr_url) or ""
        # Fallback: try common paths
        if not impr_html:
            base = target["url"].rstrip("/")
            for path in IMPRINT_HINTS[:6]:
                url = base + path if not base.endswith(path.rstrip("/")) else base
                if url == target["url"]:
                    continue
                impr_html = await fetch(client, url) or ""
                if impr_html:
                    impr_url = url
                    break
        # Combine homepage + impressum text for extraction
        full_html = (homepage or "") + "\n" + (impr_html or "")
        full_text = html_to_text(full_html)
        result["emails"] = extract_emails(full_text, full_html)
        result["phones"] = extract_phones(full_text, full_html)
        result["contacts"] = extract_contacts(impr_html or full_text)
        result["impressum_url"] = impr_url
        return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async(args) -> int:
    con = sqlite3.connect(DB_PATH)
    targets = load_targets(con, args.limit)
    if not targets:
        print("Aucune cible.")
        return 0
    print(f"Targets : {len(targets)} sites BDLI à enrichir")

    sem = asyncio.Semaphore(args.concurrency)
    headers = {"User-Agent": USER_AGENT}

    n_done = 0
    n_emails = 0
    n_phones = 0
    n_contacts = 0
    n_extra_contacts = 0
    t0 = time.time()
    async with httpx.AsyncClient(headers=headers,
                                  follow_redirects=True,
                                  timeout=20) as client:
        coros = [harvest_one(sem, client, t) for t in targets]
        for coro in asyncio.as_completed(coros):
            res = await coro
            n_done += 1
            target = next(t for t in targets if t["id"] == res["id"])
            upd = update_signal(
                con, res["id"], target["notes"],
                res["emails"], res["phones"],
                res["contacts"], res["impressum_url"],
            )
            if "[email]" in (upd.get("notes") or ""):
                n_emails += 1
            if "[phone]" in (upd.get("notes") or ""):
                n_phones += 1
            if "person_name" in upd:
                n_contacts += 1
                # Add extra contacts as new rows
                extra = insert_extra_contacts(
                    con, res["id"], target["company"], target["canonical"],
                    target["url"], res["contacts"],
                )
                n_extra_contacts += extra
            if n_done % 25 == 0 or n_done == len(targets):
                con.commit()
                dt = time.time() - t0
                print(
                    f"  [{n_done:>4}/{len(targets)}] "
                    f"+email={n_emails} +phone={n_phones} "
                    f"+contact={n_contacts} +extra={n_extra_contacts} "
                    f"({n_done/dt:.2f}/s)"
                )
    con.commit()
    con.close()
    print(
        f"\nDone in {time.time()-t0:.1f}s : "
        f"+emails={n_emails}, +phones={n_phones}, "
        f"+main contacts={n_contacts}, +extra={n_extra_contacts}"
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
