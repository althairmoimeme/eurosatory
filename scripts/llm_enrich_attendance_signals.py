"""LLM-powered enrichment of ``attendance_signals`` rows.

Fills the missing values of these 5 fields :
- ``person_name``
- ``person_role``
- ``company_name``
- ``country``
- email   (stored in ``notes`` since the schema has no email column)

Phones are intentionally NOT requested.

The model is allowed to:
- read the existing ``source_title``, ``source_snippet``, ``signal_text``,
  ``source_url`` and any current values,
- use general world knowledge for *public figures* (e.g. a known minister's
  nationality, a well-known company's HQ country),
- return ``null`` when the source text does not support a value.

It must NEVER invent emails — emails must be literally present in the
provided text fields.

Usage
-----
    python -m scripts.llm_enrich_attendance_signals               # all gaps
    python -m scripts.llm_enrich_attendance_signals --limit 5     # smoke
    python -m scripts.llm_enrich_attendance_signals --signal-id 130
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
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

DB_PATH = ROOT / "data" / "eurosatory.db"


# ---------------------------------------------------------------------------
# Output schema — exactly 5 fields, all optional (null if unsupported)
# ---------------------------------------------------------------------------


class SignalEnrichment(BaseModel):
    """Structured extraction of missing attendance-signal fields."""

    person_name: Optional[str] = Field(
        default=None,
        description=(
            "Nom complet (Prénom + NOM) de la personne mentionnée dans le "
            "signal. Format 'Prénom NOM'. NULL si le texte n'identifie "
            "personne (ex: post de société sans personne nommée)."
        ),
    )
    person_role: Optional[str] = Field(
        default=None,
        description=(
            "Fonction / titre de la personne (ex: 'Directeur des Achats', "
            "'CEO', 'Chef de département acquisition', 'Minister of Defence'). "
            "Maximum 80 caractères. NULL si non précisé dans le texte."
        ),
    )
    company_name: Optional[str] = Field(
        default=None,
        description=(
            "Société d'appartenance de la personne au moment du signal. "
            "Forme officielle (ex: 'Thales SA', 'BAE Systems plc'). "
            "NULL si la personne agit à titre personnel ou pour un État."
        ),
    )
    country: Optional[str] = Field(
        default=None,
        description=(
            "Pays de la personne ou de la société. Nom français complet "
            "(ex: 'France', 'Royaume-Uni', 'États-Unis', 'Allemagne'). "
            "Tu peux utiliser la connaissance générale pour les figures "
            "publiques (ex: un ministre français = 'France'). NULL sinon."
        ),
    )
    email: Optional[str] = Field(
        default=None,
        description=(
            "ADRESSE EMAIL LITTÉRALEMENT présente dans les textes fournis "
            "(source_snippet, signal_text, source_title, source_url). "
            "JAMAIS d'email inventé ou pattern-guessed. Préfère un email "
            "nominatif s'il y en a un, sinon générique (info@, sales@). "
            "NULL si aucune adresse n'apparaît dans le texte."
        ),
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """\
Tu es un analyste OSINT pour une base d'intelligence commerciale du salon \
Eurosatory 2026. Pour chaque signal de présence (post LinkedIn, page \
corporate, article presse, communiqué officiel), ton rôle est d'extraire \
de manière structurée 5 champs : nom de personne, rôle, société, pays, \
email.

RÈGLES DE TYPAGE
================
1. Tu réponds STRICTEMENT au schéma JSON fourni — 5 champs, aucun autre.
2. Tu retournes ``null`` plutôt que d'inventer. Mieux vaut null que faux.
3. Pour les figures publiques (ministres, chefs d'état-major, dirigeants \
de grands groupes), tu peux utiliser ta connaissance générale pour \
inférer ``country`` et ``company_name`` (ex: Florence Parly → France, \
Ministère des Armées). Pour les personnes inconnues : seulement ce que \
le texte dit.
4. Pour ``email`` : NEVER invent. L'email DOIT apparaître textuellement \
dans le source_snippet / signal_text / source_title / source_url. \
Pas de pattern-guess (firstname.lastname@…). Pas d'email inféré du \
domaine de la société. Null si aucune adresse n'est présente.
5. Pour ``country`` : nom français complet en priorité ('France', \
'Royaume-Uni', 'États-Unis', 'Allemagne', 'Italie', 'Espagne', 'Pays-Bas', \
'Belgique', 'Pologne', 'Suède', 'Finlande', 'Norvège', 'Danemark', \
'Suisse', 'Israël', 'Turquie', 'Émirats arabes unis', 'Arabie saoudite', \
'Inde', 'Japon', 'Corée du Sud', 'Australie', 'Canada', 'Brésil'…).
6. Pour ``person_role`` : rôle FONCTIONNEL et concis (≤80 char). \
'CEO' / 'Directeur Achats' / 'Chef de département acquisition' / \
'Minister of Defence' / 'Director of Procurement'. Pas de blabla \
marketing.
7. Si la personne agit à titre purement personnel (auteur d'un post \
LinkedIn perso sans mention d'entreprise), ``company_name`` = null. \
Si elle agit au nom d'un État (ministre, fonctionnaire), tu peux mettre \
le ministère / l'organisation publique (ex: 'Ministère des Armées', \
'UK Ministry of Defence', 'DGA').

EXEMPLES
========

EXEMPLE A — post LinkedIn : « Heureux d'avoir représenté Thales à \
Eurosatory 2024 — Jean-Marc Dupont, Directeur Commercial Défense »
  → person_name: "Jean-Marc Dupont"
  → person_role: "Directeur Commercial Défense"
  → company_name: "Thales"
  → country: null   (le post ne précise pas — on n'invente pas)
  → email: null

EXEMPLE B — article : « Florence Parly, alors ministre des Armées, a \
inauguré Eurosatory 2018… »
  → person_name: "Florence Parly"
  → person_role: "Ministre des Armées"
  → company_name: "Ministère des Armées"
  → country: "France"
  → email: null

EXEMPLE C — page corporate avec bandeau : « Click Bond, Inc. will be \
exhibiting at Eurosatory 2026 — contact us at info@clickbond.com »
  → person_name: null    (pas de personne nommée)
  → person_role: null
  → company_name: "Click Bond, Inc."
  → country: "États-Unis"
  → email: "info@clickbond.com"

EXEMPLE D — signal très bruité (cookies, mentions légales, navigation) :
  → tous les champs : null

Tu réponds en JSON strict, sans aucun autre commentaire.
"""


USER_TEMPLATE = """\
SIGNAL #{sid} — entity_type={entity_type}, source={source_platform}

VALEURS ACTUELLES (à compléter / corriger uniquement si elles sont \
manifestement incorrectes ; sinon laisse null pour ne pas écraser)
==========================================================
- person_name    : {cur_name}
- person_role    : {cur_role}
- company_name   : {cur_company}
- country        : {cur_country}
- email actuel   : {cur_email}

URL SOURCE
==========
{url}

TITRE
=====
{title}

EXTRAIT TEXTE (source_snippet)
==============================
{snippet}

SIGNAL TEXT (parfois enrichi)
=============================
{signal_text}

CONSIGNE
========
Retourne le JSON ``SignalEnrichment``. Renseigne UNIQUEMENT les champs \
que tu peux justifier par le texte fourni ou par une connaissance \
factuelle solide d'une figure publique. Retourne null pour les autres.
"""


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------


def fetch_signals_with_gaps(limit: Optional[int] = None,
                            signal_id: Optional[int] = None) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    if signal_id is not None:
        cur.execute(
            "SELECT * FROM attendance_signals WHERE id = ?", (signal_id,)
        )
    else:
        cur.execute("""
            SELECT * FROM attendance_signals
            WHERE is_duplicate = 0
              AND (
                person_name IS NULL OR person_name = ''
                OR person_role IS NULL OR person_role = ''
                OR company_name IS NULL OR company_name = ''
                OR country IS NULL OR country = ''
                OR (notes IS NULL OR notes NOT LIKE '%@%')
              )
            ORDER BY id
        """)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    if limit:
        rows = rows[:limit]
    return rows


def update_signal(sid: int, ext: SignalEnrichment, current: dict) -> dict:
    """Apply non-null extracted fields to the DB. Returns updated stat."""
    updates: dict[str, str] = {}
    if ext.person_name and not (current.get("person_name") or "").strip():
        updates["person_name"] = ext.person_name.strip()[:255]
    if ext.person_role and not (current.get("person_role") or "").strip():
        updates["person_role"] = ext.person_role.strip()[:255]
    if ext.company_name and not (current.get("company_name") or "").strip():
        updates["company_name"] = ext.company_name.strip()[:400]
    if ext.country and not (current.get("country") or "").strip():
        updates["country"] = ext.country.strip()[:120]

    # Email goes into ``notes`` (existing convention from
    # link_company_emails_to_signals.py).
    if ext.email and "@" in ext.email:
        existing_notes = current.get("notes") or ""
        if "@" not in existing_notes:
            email_clean = ext.email.strip().lower()
            new_notes = (
                f"{existing_notes}\n[email] {email_clean}".strip()
                if existing_notes
                else f"[email] {email_clean}"
            )
            updates["notes"] = new_notes[:2000]

    if not updates:
        return updates

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    sets = ", ".join(f"{k} = ?" for k in updates)
    cur.execute(
        f"UPDATE attendance_signals SET {sets} WHERE id = ?",
        list(updates.values()) + [sid],
    )
    con.commit()
    con.close()
    return updates


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


def make_client():
    from anthropic import AsyncAnthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set in .env")
    return AsyncAnthropic(api_key=api_key, max_retries=4)


def build_user_msg(row: dict) -> str:
    return USER_TEMPLATE.format(
        sid=row["id"],
        entity_type=row.get("entity_type") or "?",
        source_platform=row.get("source_platform") or "?",
        cur_name=(row.get("person_name") or "null")[:120],
        cur_role=(row.get("person_role") or "null")[:120],
        cur_company=(row.get("company_name") or "null")[:200],
        cur_country=(row.get("country") or "null")[:80],
        cur_email=("yes (in notes)"
                   if "@" in (row.get("notes") or "") else "null"),
        url=(row.get("source_url") or "(aucune)")[:400],
        title=(row.get("source_title") or "(aucun titre)")[:300],
        snippet=(row.get("source_snippet") or "(aucun extrait)")[:3000],
        signal_text=(row.get("signal_text") or "(aucun signal_text)")[:1500],
    )


async def call_llm(client, model: str, msg: str) -> Optional[SignalEnrichment]:
    try:
        resp = await client.messages.parse(
            model=model,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            output_format=SignalEnrichment,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": msg}],
        )
    except Exception as e:  # noqa: BLE001
        print(f"   ✗ LLM error: {e!r}")
        return None
    return resp.parsed_output


async def process_one(sem, client, model, row):
    async with sem:
        msg = build_user_msg(row)
        ext = await call_llm(client, model, msg)
        if ext is None:
            return row["id"], None, None
        upd = update_signal(row["id"], ext, row)
        return row["id"], ext, upd


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async(args) -> int:
    rows = fetch_signals_with_gaps(limit=args.limit, signal_id=args.signal_id)
    if not rows:
        print("Aucun signal à enrichir.")
        return 0
    print(f"Targets: {len(rows)} signaux")

    client = make_client()
    model = os.getenv("LLM_INTELLIGENCE_MODEL", "claude-opus-4-7")
    print(f"Model: {model}, concurrency: {args.concurrency}")

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [process_one(sem, client, model, r) for r in rows]

    n_ok = 0
    n_err = 0
    n_changed = 0
    field_counts = {"person_name": 0, "person_role": 0,
                    "company_name": 0, "country": 0, "notes": 0}
    n_done = 0
    t0 = time.time()
    for coro in asyncio.as_completed(tasks):
        sid, ext, upd = await coro
        n_done += 1
        if ext is None:
            n_err += 1
        else:
            n_ok += 1
            if upd:
                n_changed += 1
                for k in upd:
                    if k in field_counts:
                        field_counts[k] += 1
        if n_done % 10 == 0 or n_done == len(rows):
            dt = time.time() - t0
            rate = n_done / dt if dt > 0 else 0
            print(
                f"  [{n_done:>4}/{len(rows)}] ok={n_ok} err={n_err} "
                f"updated={n_changed}  fields={field_counts} "
                f"({rate:.2f}/s)"
            )

    print(
        f"\nDone in {time.time()-t0:.1f}s : "
        f"ok={n_ok}, err={n_err}, updated={n_changed}\n"
        f"  +person_name : {field_counts['person_name']}\n"
        f"  +person_role : {field_counts['person_role']}\n"
        f"  +company_name: {field_counts['company_name']}\n"
        f"  +country     : {field_counts['country']}\n"
        f"  +email (notes): {field_counts['notes']}"
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--signal-id", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=2)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
