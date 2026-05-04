"""LLM-extract Geschäftsführer / Contacts from BDLI member Impressum pages.

The regex-based extraction in ``enrich_bdli_websites.py`` had a poor hit
rate (6%) because German Impressum text concatenates names with company
identifiers (HRB number, Steuernummer, etc) without clean separators.
We use Claude haiku for structured extraction — much more reliable.

Reads BDLI rows with ``[impressum] URL`` in notes, re-fetches the page,
sends to claude-haiku with a structured-output schema, and persists :
    person_name + person_role on the parent row (best contact)
    extra contacts as separate ``bdli-de-2nd`` rows
    [email] line in notes if a nominative email was found

Cost : ~$0.001 per call × 222 = ~$0.30.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sqlite3
import sys
import time
from html import unescape
from pathlib import Path
from typing import Optional

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


def html_to_text(html: str) -> str:
    # Strip scripts/styles first
    s = re.sub(r"<script[^>]*>.*?</script>", " ", html or "", flags=re.DOTALL)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL)
    s = unescape(_TAG_RX.sub(" ", s))
    return _WS_RX.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------


class Contact(BaseModel):
    name: str = Field(
        description=(
            "Nom complet de la personne (Prénom + NOM). Pas de titre "
            "professionnel ici. Préfixes 'Dr.' / 'Prof.' acceptés. "
            "Pas de noms de société, pas de mentions légales."
        ),
    )
    role: str = Field(
        description=(
            "Fonction de la personne (Geschäftsführer, Managing Director, "
            "CEO, Vorstand, Inhaber, Vertretungsberechtigter, etc.). "
            "Maximum 60 caractères."
        ),
    )
    email: Optional[str] = Field(
        default=None,
        description=(
            "Email NOMINATIF de la personne s'il apparaît dans la page "
            "(ex: 'firstname.lastname@company.com'). NULL si pas d'email "
            "nominatif. Ne pas inventer ni utiliser un email générique "
            "comme info@ ici."
        ),
    )


class ImprintExtraction(BaseModel):
    """Structured extraction of a German B2B Impressum page."""

    contacts: list[Contact] = Field(
        default_factory=list,
        description=(
            "Liste des PERSONNES physiques nommées dans l'Impressum. "
            "Typiquement les Geschäftsführer / Managing Directors. "
            "Liste vide si l'Impressum ne nomme aucune personne."
        ),
    )
    company_email: Optional[str] = Field(
        default=None,
        description=(
            "Email de contact GÉNÉRIQUE de la société (info@, contact@, "
            "sales@, etc.) s'il apparaît dans la page. NULL si absent."
        ),
    )
    company_phone: Optional[str] = Field(
        default=None,
        description=(
            "Numéro de téléphone principal de la société tel qu'affiché "
            "dans la page (avec indicatif). NULL si absent."
        ),
    )


SYSTEM_PROMPT = """\
Tu es un assistant d'extraction de données structurées depuis des pages \
Impressum (mentions légales) de sites web allemands B2B.

RÈGLES :
1. Tu réponds STRICTEMENT au schéma JSON ``ImprintExtraction``.
2. Tu n'inventes JAMAIS d'information. Champs vides → null / liste vide.
3. Pour ``contacts`` :
   - Tu n'inclus QUE des personnes physiques nommées (Prénom + NOM).
   - Tu skip les noms de société (XYZ GmbH), les rôles génériques ("News \
Contact", "Customer Service"), et tout ce qui ressemble à du bruit légal \
(numéros de registre, adresse, etc.).
   - Pour le champ ``role``, utilise la fonction telle qu'elle apparaît \
en allemand ou anglais dans la page (Geschäftsführer, Managing Director, \
Vorstand, CEO, Inhaber, …).
4. Pour ``email`` dans Contact : SEUL un email nominatif (au format \
prenom.nom@société ou prenom@société) est accepté. JAMAIS info@, sales@, \
contact@. NULL si pas d'email nominatif.
5. Pour ``company_email`` : un email générique (info@, contact@, etc.).
6. Pour ``company_phone`` : numéro principal au format international.

EXEMPLE
-------
Page contient :
   "Geschäftsführer: Dr. Andreas Müller, Stefan Klein
    HRB 12345 Amtsgericht München
    USt-IdNr.: DE123456789
    E-Mail: info@example.de
    Telefon: +49 89 1234567"

→ {
    "contacts": [
        {"name": "Dr. Andreas Müller", "role": "Geschäftsführer", "email": null},
        {"name": "Stefan Klein", "role": "Geschäftsführer", "email": null}
    ],
    "company_email": "info@example.de",
    "company_phone": "+49 89 1234567"
}
"""


USER_TEMPLATE = """\
SOCIÉTÉ : {company}
URL Impressum : {url}

CONTENU DE LA PAGE (texte brut, max ~6000 chars) :
{text}

Renvoie le JSON ImprintExtraction strict.
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
        "WHERE source_platform='bdli-de' "
        "  AND notes LIKE '%[impressum]%' "
        "  AND (notes NOT LIKE '%[llm-extracted]%') "
        "ORDER BY id"
    )
    out = []
    for r in cur.fetchall():
        notes = r[4] or ""
        m = re.search(r"\[impressum\]\s*([^\s\n]+)", notes)
        if not m:
            continue
        out.append({
            "id": r[0], "company": r[1], "canonical": r[2],
            "ads_url": r[3], "imprint_url": m.group(1).strip(),
            "notes": notes,
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
    con: sqlite3.Connection, sid: int, notes: str,
    extraction: ImprintExtraction,
) -> None:
    upd = {}
    new_notes = notes
    primary = extraction.contacts[0] if extraction.contacts else None
    if primary:
        upd["person_name"] = primary.name[:255]
        upd["person_role"] = primary.role[:255]
        upd["entity_type"] = "person"
    # Prefer nominative email if available, else company email
    chosen_email = None
    if primary and primary.email:
        chosen_email = primary.email.strip().lower()
    elif extraction.company_email:
        chosen_email = extraction.company_email.strip().lower()
    if chosen_email and "@" in chosen_email:
        new_notes = replace_or_add(new_notes, "[email]", chosen_email)
    if extraction.company_phone:
        new_notes = replace_or_add(
            new_notes, "[phone]", extraction.company_phone.strip(),
        )
    new_notes = replace_or_add(new_notes, "[llm-extracted]", "1")
    new_notes = new_notes[:8000]
    upd["notes"] = new_notes
    cur = con.cursor()
    sets = ", ".join(f"{k} = ?" for k in upd)
    cur.execute(
        f"UPDATE attendance_signals SET {sets} WHERE id = ?",
        list(upd.values()) + [sid],
    )


def insert_extra_contacts(
    con: sqlite3.Connection,
    parent: dict, extras: list[Contact],
) -> int:
    if not extras:
        return 0
    cur = con.cursor()
    n = 0
    # Don't recreate if already done
    cur.execute(
        "SELECT person_name FROM attendance_signals "
        "WHERE source_platform='bdli-de-2nd' "
        "  AND canonical_company_name = ?",
        (parent["canonical"],),
    )
    already = {(r[0] or "").lower() for r in cur.fetchall()}
    for c in extras:
        if c.name.lower() in already:
            continue
        notes2 = []
        if c.email:
            notes2.append(f"[email] {c.email.strip().lower()}")
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
                ?, ?, 'Germany',
                'bdli-de-2nd', ?, ?, ?,
                'leadership_lookup', ?,
                'Additional Geschäftsführer extracted via LLM from Impressum.',
                0, 0, 0, 0, 0,
                'high', 'pending', ?
            )""",
            (
                c.name[:255], c.role[:255],
                parent["company"][:400], parent["canonical"][:80],
                parent["ads_url"][:900],
                f"{c.name} ({c.role}) @ {parent['company']}"[:400],
                "",
                f"{c.name} ({c.role}) chez {parent['company']}"[:500],
                notes2_s,
            ),
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


def make_client():
    from anthropic import AsyncAnthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY missing")
    return AsyncAnthropic(api_key=api_key, max_retries=4)


async def fetch_imprint(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:  # noqa: BLE001
        return None


async def extract_via_llm(
    aclient, model: str, company: str, url: str, html: str,
) -> Optional[ImprintExtraction]:
    text = html_to_text(html)[:6000]
    if len(text) < 100:
        return None
    msg = USER_TEMPLATE.format(company=company, url=url, text=text)
    try:
        resp = await aclient.messages.parse(
            model=model,
            max_tokens=1500,
            output_format=ImprintExtraction,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": msg}],
        )
    except Exception as e:  # noqa: BLE001
        print(f"   ✗ LLM error: {e!r}")
        return None
    return resp.parsed_output


async def process_one(
    sem, http_client, ant_client, model, target,
):
    async with sem:
        html = await fetch_imprint(http_client, target["imprint_url"])
        if not html:
            return target, None
        ext = await extract_via_llm(
            ant_client, model,
            target["company"], target["imprint_url"], html,
        )
        return target, ext


async def main_async(args) -> int:
    con = sqlite3.connect(DB_PATH)
    targets = fetch_targets(con, args.limit)
    if not targets:
        print("No targets")
        return 0
    print(f"Targets: {len(targets)} BDLI Impressum to LLM-extract")

    ant_client = make_client()
    model = "claude-haiku-4-5"
    print(f"Model: {model}, concurrency: {args.concurrency}")

    sem = asyncio.Semaphore(args.concurrency)

    n_ok = 0
    n_named = 0
    n_email = 0
    n_extra = 0
    n_done = 0
    t0 = time.time()
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=20,
    ) as http_client:
        coros = [
            process_one(sem, http_client, ant_client, model, t)
            for t in targets
        ]
        for coro in asyncio.as_completed(coros):
            target, ext = await coro
            n_done += 1
            if ext is None:
                continue
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
            if n_done % 25 == 0 or n_done == len(targets):
                con.commit()
                dt = time.time() - t0
                print(
                    f"  [{n_done:>4}/{len(targets)}] "
                    f"ok={n_ok} +named={n_named} +email={n_email} "
                    f"+extra={n_extra} ({n_done/dt:.2f}/s)"
                )
    con.commit()
    con.close()
    print(
        f"\nDone in {time.time()-t0:.1f}s : ok={n_ok}, "
        f"with named contact={n_named}, with email={n_email}, "
        f"extra contacts inserted={n_extra}"
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
