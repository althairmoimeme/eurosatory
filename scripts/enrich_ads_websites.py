"""Re-enrich ADS Group UK members by scraping their CORPORATE websites.

ADS Group's own member pages do not expose emails (only a contact form).
To get emails + extra contacts we go to each member's own website
(which we already have from JSON-LD ``website`` field) and harvest the
``/contact`` / ``/about`` / ``/team`` pages.

Pipeline
--------
For each ADS UK row without email :
    1. Read website URL from the ``[ads-detail]`` JSON-LD blob in notes.
    2. Fetch homepage + try /contact /about /team /our-team /imprint.
    3. Send all rendered text to claude-haiku for structured extraction.
    4. Persist [email] / [phone] in notes, fill person_name+role on the
       parent row if a key contact is found, insert extras as new rows.

Idempotent: skips rows that already have ``[corporate-extracted]``.

Usage:
    python -m scripts.enrich_ads_websites              # full
    python -m scripts.enrich_ads_websites --limit 5    # smoke
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

DB_PATH = ROOT / "data" / "eurosatory.db"
USER_AGENT = (
    "Mozilla/5.0 (compatible; eurosatory-scraper/1.0; "
    "+contact: a.bertantoine@gmail.com)"
)


_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")
_BLOB_RX = re.compile(r"\[ads-detail\]\s*(\{.*?\})\s*(?:\n|$)", re.DOTALL)


def html_to_text(html: str) -> str:
    s = re.sub(r"<script[^>]*>.*?</script>", " ", html or "", flags=re.DOTALL)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL)
    s = unescape(_TAG_RX.sub(" ", s))
    return _WS_RX.sub(" ", s).strip()


# Common UK contact pages to probe
CONTACT_PATHS = [
    "/contact-us", "/contact-us/",
    "/contact", "/contact/",
    "/about-us", "/about-us/",
    "/about", "/about/",
    "/our-team", "/our-team/",
    "/team", "/team/",
    "/leadership", "/leadership/",
    "/management", "/management/",
    "/imprint",   # rare but some EU brand UK sites
]
_LINK_RX = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>(?:[^<]|<(?!/?a))*?(?:'
    r"contact\s*us|contact|about\s*us|about|leadership|"
    r"our\s*team|management|the\s*team"
    r")(?:[^<]|<(?!/?a))*?</a>",
    re.IGNORECASE,
)


def find_contact_url(html: str, base_url: str) -> Optional[str]:
    for m in _LINK_RX.finditer(html or ""):
        href = m.group(1).strip()
        if not href or href.startswith("#") or "javascript" in href.lower():
            continue
        return urljoin(base_url, href)
    return None


def domain_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        p = urlparse(url if "://" in url else "http://" + url)
        d = (p.netloc or p.path).lower()
        d = re.sub(r"^www\.", "", d).split("/")[0].split(":")[0]
        return d if "." in d else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# LLM schema (UK / English orientation)
# ---------------------------------------------------------------------------


class Contact(BaseModel):
    name: str = Field(
        description=(
            "Full name of the person (First name + LAST NAME). "
            "Allowed prefixes: Dr., Mr., Mrs., Ms., Prof. NEVER company names."
        ),
    )
    role: str = Field(
        description=(
            "Job title (CEO, Managing Director, CTO, Founder, Director, "
            "Head of Sales, etc.). Max 80 chars."
        ),
    )
    email: Optional[str] = Field(
        default=None,
        description=(
            "NOMINATIVE email of the person if it appears literally in "
            "the text (e.g. firstname.lastname@company.com). NULL if "
            "only a generic email (info@, sales@) is present. NEVER invent."
        ),
    )


class CorporateExtract(BaseModel):
    contacts: list[Contact] = Field(
        default_factory=list,
        description=(
            "Named persons explicitly identified on the corporate site. "
            "Skip team-card placeholders like 'Customer Service' / 'Support' "
            "if they are not actual people."
        ),
    )
    company_email: Optional[str] = Field(
        default=None,
        description=(
            "Generic company email (info@, contact@, sales@) if visible. "
            "NULL if absent."
        ),
    )
    company_phone: Optional[str] = Field(
        default=None,
        description="Main company phone with country code. NULL if absent.",
    )


SYSTEM_PROMPT = """\
You extract structured contact data from English-language corporate \
websites (UK SMEs and corporate groups).

RULES:
1. Reply STRICTLY to the JSON ``CorporateExtract`` schema.
2. Never invent. Empty when missing → null / [].
3. ``contacts`` includes ONLY named individuals. Skip generic labels \
("Sales Team", "Support", "Customer Service") and any team-card \
placeholder. Skip company names like "ACME Defence Ltd".
4. ``email`` inside Contact: nominative only (e.g. \
``john.doe@company.com``). NEVER infer / guess. NULL if a generic email \
(info@, sales@) is the only thing visible.
5. ``company_email``: generic catch-all if visible.
6. ``company_phone``: main company line, with country code if shown.

EXAMPLE
-------
Page contains:
   "About us
    Founded by John Smith, our CEO, the company...
    Contact: info@example.com
    Tel +44 20 1234 5678"

→ {
    "contacts": [{"name":"John Smith", "role":"CEO", "email":null}],
    "company_email": "info@example.com",
    "company_phone": "+44 20 1234 5678"
}
"""


USER_TEMPLATE = """\
COMPANY: {company}
URL: {url}

PAGE TEXT (first ~6000 chars of homepage + contact / about / team page):
{text}

Return strict ``CorporateExtract`` JSON.
"""


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def fetch_targets(con: sqlite3.Connection,
                  limit: Optional[int]) -> list[dict]:
    cur = con.cursor()
    cur.execute(
        "SELECT id, company_name, canonical_company_name, source_url, notes "
        "FROM attendance_signals "
        "WHERE source_platform = 'ads-group-uk' "
        "  AND (notes NOT LIKE '%[corporate-extracted]%') "
        "  AND notes LIKE '%[ads-detail]%' "
        "ORDER BY id"
    )
    out = []
    for r in cur.fetchall():
        notes = r[4] or ""
        m = _BLOB_RX.search(notes)
        if not m:
            continue
        try:
            blob = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        web = (blob.get("website") or "").strip()
        if not web:
            continue
        if not web.startswith("http"):
            web = "http://" + web
        out.append({
            "id": r[0], "company": r[1], "canonical": r[2],
            "ads_url": r[3], "website": web, "notes": notes,
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


def update_signal(con, sid: int, notes: str, ext: CorporateExtract) -> None:
    upd = {}
    new_notes = notes
    primary = ext.contacts[0] if ext.contacts else None
    if primary:
        upd["person_name"] = primary.name[:255]
        upd["person_role"] = primary.role[:255]
        upd["entity_type"] = "person"
    chosen_email = None
    if primary and primary.email:
        chosen_email = primary.email.strip().lower()
    elif ext.company_email:
        chosen_email = ext.company_email.strip().lower()
    if chosen_email and "@" in chosen_email:
        new_notes = replace_or_add(new_notes, "[email]", chosen_email)
    if ext.company_phone:
        new_notes = replace_or_add(
            new_notes, "[phone]", ext.company_phone.strip(),
        )
    new_notes = replace_or_add(new_notes, "[corporate-extracted]", "1")
    upd["notes"] = new_notes[:8000]
    cur = con.cursor()
    sets = ", ".join(f"{k} = ?" for k in upd)
    cur.execute(
        f"UPDATE attendance_signals SET {sets} WHERE id = ?",
        list(upd.values()) + [sid],
    )


def insert_extra_contacts(con, parent: dict,
                           extras: list[Contact]) -> int:
    if not extras:
        return 0
    cur = con.cursor()
    cur.execute(
        "SELECT LOWER(person_name) FROM attendance_signals "
        "WHERE source_platform IN "
        "  ('ads-group-uk-2nd-contact', 'enrich-so-ads-employee', 'ads-group-uk-corporate') "
        "  AND canonical_company_name = ?",
        (parent["canonical"],),
    )
    already = {r[0] for r in cur.fetchall() if r[0]}
    n = 0
    for c in extras:
        nm_lc = c.name.strip().lower()
        if nm_lc in already:
            continue
        notes_lines = []
        if c.email:
            notes_lines.append(f"[email] {c.email.strip().lower()}")
        notes2 = "\n".join(notes_lines)[:2000]
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
                'ads-group-uk-corporate', ?, ?, ?,
                'leadership_lookup', ?,
                'Additional named contact extracted via LLM from the company website.',
                0, 0, 0, 0, 0,
                'medium', 'pending', ?
            )""",
            (
                c.name[:255], c.role[:255],
                parent["company"][:400], parent["canonical"][:80],
                parent["website"][:900],
                f"{c.name} ({c.role}) @ {parent['company']}"[:400],
                "",
                f"{c.name} ({c.role}) chez {parent['company']}"[:500],
                notes2,
            ),
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# Async fetch + LLM
# ---------------------------------------------------------------------------


async def fetch(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("content-type", "")
        if "text/html" not in ctype.lower() and "html" not in ctype.lower():
            return None
        return r.text
    except Exception:  # noqa: BLE001
        return None


async def harvest_pages(client: httpx.AsyncClient, base: str) -> str:
    """Fetch homepage + try one contact/about page. Combine text."""
    homepage = await fetch(client, base)
    if not homepage:
        return ""
    contact_url = find_contact_url(homepage, base)
    contact_html = ""
    if contact_url:
        contact_html = await fetch(client, contact_url) or ""
    if not contact_html:
        # Try common paths
        base_clean = base.rstrip("/")
        for path in CONTACT_PATHS[:5]:
            url = base_clean + path
            if url == base.rstrip("/"):
                continue
            contact_html = await fetch(client, url) or ""
            if contact_html and len(contact_html) > 500:
                break
    full_text = html_to_text(homepage + "\n" + contact_html)
    return full_text[:6000]


def make_anthropic():
    from anthropic import AsyncAnthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY missing")
    return AsyncAnthropic(api_key=api_key, max_retries=4)


async def extract_via_llm(
    aclient, model: str, company: str, url: str, text: str,
) -> Optional[CorporateExtract]:
    if len(text) < 100:
        return None
    msg = USER_TEMPLATE.format(company=company, url=url, text=text)
    try:
        resp = await aclient.messages.parse(
            model=model,
            max_tokens=1500,
            output_format=CorporateExtract,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": msg}],
        )
    except Exception as e:  # noqa: BLE001
        # silent — log only first few
        return None
    return resp.parsed_output


async def process_one(sem, http_client, ant_client, model, target):
    async with sem:
        text = await harvest_pages(http_client, target["website"])
        if not text:
            return target, None, "fetch_failed"
        ext = await extract_via_llm(
            ant_client, model, target["company"], target["website"], text,
        )
        return target, ext, ("ok" if ext else "llm_failed")


async def main_async(args) -> int:
    con = sqlite3.connect(DB_PATH)
    targets = fetch_targets(con, args.limit)
    print(f"Targets: {len(targets)} ADS UK members to enrich via corporate site")
    if not targets:
        return 0

    ant_client = make_anthropic()
    model = "claude-haiku-4-5"
    print(f"Model: {model}, concurrency: {args.concurrency}\n")

    sem = asyncio.Semaphore(args.concurrency)

    n_done = 0
    n_ok = 0
    n_named = 0
    n_email = 0
    n_extra = 0
    t0 = time.time()
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True, timeout=20,
    ) as http_client:
        coros = [
            process_one(sem, http_client, ant_client, model, t)
            for t in targets
        ]
        for coro in asyncio.as_completed(coros):
            target, ext, status = await coro
            n_done += 1
            if ext is None:
                # Mark attempted to skip on re-runs
                cur = con.cursor()
                cur.execute(
                    "UPDATE attendance_signals SET notes = ? WHERE id = ?",
                    (
                        replace_or_add(target["notes"],
                                       "[corporate-extracted]", "0")[:8000],
                        target["id"],
                    ),
                )
            else:
                n_ok += 1
                if ext.contacts:
                    n_named += 1
                update_signal(con, target["id"], target["notes"], ext)
                if len(ext.contacts) > 1:
                    n_extra += insert_extra_contacts(
                        con, target, ext.contacts[1:]
                    )
                if ext.company_email or (ext.contacts and ext.contacts[0].email):
                    n_email += 1
            if n_done % 50 == 0 or n_done == len(targets):
                con.commit()
                dt = time.time() - t0
                print(
                    f"  [{n_done:>4}/{len(targets)}] "
                    f"ok={n_ok} +named={n_named} +email={n_email} "
                    f"+extra={n_extra}  ({n_done/dt:.2f}/s)"
                )
    con.commit()
    con.close()
    print(
        f"\nDone in {time.time()-t0:.1f}s : ok={n_ok}, "
        f"named={n_named}, email={n_email}, extra={n_extra}"
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
