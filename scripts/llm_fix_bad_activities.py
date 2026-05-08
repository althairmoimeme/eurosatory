"""Re-enrich the ~742 fiches whose ``activity_1liner`` doesn't start with
a canonical action verb (Conçoit / Fabrique / Édite / etc.) — these
slipped through the rule-based extractor and grabbed marketing fluff
instead of the real business activity.

Strategy
--------
For each broken fiche:
  1. Pull all DB context : short_presentation, presentation, headline,
     activity_summary, keywords, crawled-page excerpts.
  2. Send to Claude Haiku with a strict French prompt requiring an
     action-verb sentence ≤ 140 chars + 6-field profile.
  3. Persist into ``data/llm_test/profiles_manual_overrides.json``
     (merging — never overwrites a clean field).
  4. After all fiches : re-run the pipeline (merge → normalize → export).

Usage
-----
    python -m scripts.llm_fix_bad_activities --limit 5      # smoke
    python -m scripts.llm_fix_bad_activities --concurrency 4
    python -m scripts.llm_fix_bad_activities --apply        # full + repipeline
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

from app.processors.taxonomy_normalize import (  # noqa: E402
    PRODUCT_CATEGORIES,
    SERVICE_CATEGORIES,
    TECHNOLOGY_CATEGORIES,
)

DB_PATH = ROOT / "data" / "eurosatory.db"
EXPORT_PATH = ROOT / "data" / "exports" / "targeting_profiles_final.json"
OVERRIDES_PATH = ROOT / "data" / "llm_test" / "profiles_manual_overrides.json"
CAT_OVERRIDES_PATH = ROOT / "data" / "llm_test" / "llm_categories_overrides.json"


GOOD_VERB_RX = re.compile(
    r"^(Con[çc]oit|Fabrique|[ÉE]dite|Distribue|Int[èe]gre|Forme|Maintient|"
    r"Conseille|Loue|Exploite|Op[èe]re|Repr[ée]sente|Fournit|D[ée]veloppe|"
    r"R[ée]alise|Audite|Pilote|Conduit|Exporte|Vend|Assure|Pr[ée]pare|"
    r"Manufactures?|Designs?|Produces?|Provides?|Distributes?|Operates?|"
    r"Sous[- ]traite|Forge|Usine|Brute)",
    re.I | re.U,
)


def is_bad(activity: str) -> bool:
    """Return True when activity_1liner needs re-enrichment."""
    if not activity:
        return True
    s = activity.strip()
    if "données publiques trop pauvres" in s.lower():
        return False  # accepted as-is
    if not GOOD_VERB_RX.match(s):
        return True
    if len(s) < 50 or len(s) > 200:
        return True
    return False


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """\
Tu es un analyste B2B défense / sécurité européenne. Ton rôle : à \
partir des sources publiques fournies, produire une fiche de ciblage \
commercial compacte.

⚠️ EXIGENCE — ``activity_1liner`` :
  • UNE phrase, ≤ 140 caractères, en français.
  • COMMENCE par un verbe d'action 3ᵉ personne : Conçoit · Fabrique · \
Édite · Distribue · Intègre · Maintient · Forme · Conseille · \
Représente · Fournit · Développe · Audite · Sous-traite · Forge · \
Usine · Pilote · Conduit · Réalise · Opère · Anime.
  • Décrit le CŒUR d'activité (ce qu'ils font), PAS le marketing \
("leader", "innovant", "We are", "Our company"…).
  • Données réellement pauvres → "(données publiques trop pauvres \
pour qualification fiable)".

EXEMPLES
  ✅ "Conçoit et fabrique des viseurs optroniques pour fantassins."
  ✅ "Sous-traite l'usinage CNC de précision pour l'aéronautique défense."
  ✅ "Édite des plateformes IA d'analyse d'imagerie satellite ISR."
  ❌ "We are your strong partner for drive and control technology…"
  ❌ "L'entreprise offre des services de haute qualité…"

CATÉGORIES (utilise les noms canoniques français les plus représentatifs):
- products_categories : 1-4 catégories de produits/plateformes (ex: \
"Drones aériens (UAV)", "Pièces mécaniques & usinage", \
"Cybersécurité (logiciels & appliances)", "Aciers & métallurgie spéciale", \
"Optronique & viseurs (EO/IR)", etc.). Si purement institutionnel: \
"Représentation institutionnelle (cluster, fédération, chambre)" / \
"Médias & publications défense" / "R&D académique & laboratoires".
- services_categories : 0-3 (ex: "MCO / MRO", "Intégration de systèmes", \
"Ingénierie & conseil", "Formation & entraînement", "Distribution & \
représentation", "Sous-traitance industrielle", "Certification & tests").
- technologies_categories : 0-3 (ex: "IA / machine learning", \
"Cybersécurité", "Matériaux composites", "Radar (AESA, monopulse, …)", \
"Imagerie infrarouge").

target_buyers : 1-4 dans ["MoD / Armées", "Primes défense", \
"Sécurité civile", "Industriels défense", "Export / international"].

why_target : 1 phrase FR concrète sur l'angle commercial \
(ex: "Sous-traitant mécanique éligible EN9100 — fournisseur potentiel \
pour pièces usinées séries courtes.").
"""


class Fix(BaseModel):
    activity_1liner: str = Field(
        description=(
            "UNE phrase ≤ 140 char, en français, commençant OBLIGATOIREMENT "
            "par un verbe d'action 3ᵉ personne (Conçoit, Fabrique, Édite, "
            "Distribue, Intègre, Forme, Maintient, Sous-traite, Forge, …). "
            "Décrit le cœur d'activité réel, pas le marketing."
        ),
    )
    products: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    target_buyers: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    why_target: str = Field(description="1 phrase contextuelle FR.")
    products_categories: list[str] = Field(
        default_factory=list,
        description="1 à 4 labels EXCLUSIVEMENT dans la closed list produits.",
    )
    services_categories: list[str] = Field(default_factory=list)
    technologies_categories: list[str] = Field(default_factory=list)


USER_TEMPLATE = """\
EXPOSANT À CORRIGER
===================
Société : {company_name}
Pays    : {country}
Site    : {website}
Pavillon Eurosatory : {pavilion}

ACTIVITÉ ACTUELLE (incorrecte ou floue)
======================================
{current_activity}

DESCRIPTION OFFICIELLE FINDERR (short)
======================================
{sp}

PRESENTATION LONGUE
===================
{presentation}

HEADLINE / META DU SITE
=======================
{headline}

ACTIVITY SUMMARY
================
{activity_summary}

MOTS-CLÉS
=========
{keywords}

EXTRAITS DE CRAWL
=================
{crawl}

CONSIGNE
========
Renvoie un JSON ``Fix`` strict. Le champ activity_1liner DOIT commencer \
par un verbe d'action (Conçoit / Fabrique / etc.).
"""


# ---------------------------------------------------------------------------
# DB sources
# ---------------------------------------------------------------------------


def load_db_sources() -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    if not DB_PATH.exists():
        return out
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        "SELECT e.id, e.short_presentation, e.presentation, e.keywords, "
        "i.headline, i.activity_summary "
        "FROM exhibitors e LEFT JOIN exhibitor_intelligence i "
        "ON i.exhibitor_id = e.id"
    )
    for row in cur.fetchall():
        kw_raw = row["keywords"] or ""
        try:
            kw_list = json.loads(kw_raw) if kw_raw else []
        except Exception:  # noqa: BLE001
            kw_list = []
        out[int(row["id"])] = {
            "sp": row["short_presentation"] or "",
            "presentation": row["presentation"] or "",
            "hl": row["headline"] or "",
            "activity_summary": row["activity_summary"] or "",
            "keywords": " ".join(kw_list) if kw_list else "",
            "crawl": "",
        }
    cur.execute(
        "SELECT exhibitor_id, text_excerpt FROM crawled_pages "
        "WHERE text_excerpt IS NOT NULL AND length(text_excerpt) > 100 "
        "ORDER BY (kind = 'homepage') DESC, id"
    )
    crawl_by_id: dict[int, list[str]] = {}
    for row in cur.fetchall():
        crawl_by_id.setdefault(int(row["exhibitor_id"]), []).append(
            row["text_excerpt"] or ""
        )
    for eid, snippets in crawl_by_id.items():
        if eid in out:
            out[eid]["crawl"] = " ".join(snippets[:3])[:5000]
    con.close()
    return out


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


def make_client():
    from anthropic import AsyncAnthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY missing")
    return AsyncAnthropic(api_key=api_key, max_retries=4)


def build_msg(rec: dict, srcs: dict[str, str]) -> str:
    return USER_TEMPLATE.format(
        company_name=rec["company_name"],
        country=rec.get("country") or "?",
        website=rec.get("website") or "?",
        pavilion=rec.get("pavilion") or "?",
        current_activity=(rec.get("activity_1liner") or "(vide)")[:200],
        sp=(srcs.get("sp") or "(non renseigné)")[:800],
        presentation=(srcs.get("presentation") or "(non renseigné)")[:1500],
        headline=(srcs.get("hl") or "(aucun)")[:300],
        activity_summary=(srcs.get("activity_summary") or "(aucun)")[:800],
        keywords=(srcs.get("keywords") or "(aucun)")[:400],
        crawl=(srcs.get("crawl") or "(aucun)")[:2500],
    )


async def call_llm(client, model: str, msg: str) -> Optional[Fix]:
    """Call with up to 3 retries on rate limit (exp backoff 5s/15s/45s)."""
    delays = [5, 15, 45]
    for attempt in range(len(delays) + 1):
        try:
            resp = await client.messages.parse(
                model=model,
                max_tokens=2500,
                output_format=Fix,
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": msg}],
            )
            return resp.parsed_output
        except Exception as e:  # noqa: BLE001
            err_str = repr(e)
            is_rate_limit = "RateLimitError" in err_str or "429" in err_str
            if is_rate_limit and attempt < len(delays):
                await asyncio.sleep(delays[attempt])
                continue
            print(f"   ✗ LLM error: {e!r}")
            return None
    return None


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------


def filter_cats(cats: list[str], allowed: tuple[str, ...]) -> list[str]:
    s = set(allowed)
    return [c for c in cats if c in s]


def save_overrides(overrides: dict) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_cat_overrides(cat_overrides: dict) -> None:
    CAT_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAT_OVERRIDES_PATH.write_text(
        json.dumps(cat_overrides, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async(args) -> int:
    recs = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    targets = [r for r in recs if is_bad(r.get("activity_1liner") or "")]
    print(f"Bad activity fiches : {len(targets)} / {len(recs)} ({len(targets)*100/len(recs):.1f}%)")
    if args.limit:
        targets = targets[: args.limit]

    db_sources = load_db_sources()

    # Existing overrides
    overrides = {}
    if OVERRIDES_PATH.exists():
        overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    cat_overrides = {}
    if CAT_OVERRIDES_PATH.exists():
        cat_overrides = json.loads(CAT_OVERRIDES_PATH.read_text(encoding="utf-8"))

    client = make_client()
    model = "claude-haiku-4-5"
    print(f"Model: {model}, concurrency: {args.concurrency}")
    sem = asyncio.Semaphore(args.concurrency)

    async def process(rec):
        async with sem:
            eid = int(rec["exhibitor_id"])
            srcs = db_sources.get(eid, {})
            msg = build_msg(rec, srcs)
            fix = await call_llm(client, model, msg)
            if fix is None:
                return eid, None
            # Sanity check : action verb
            if not GOOD_VERB_RX.match(fix.activity_1liner.strip()):
                # Mark as bad — retry skipped
                return eid, None
            # Filter categories
            fix.products_categories = filter_cats(
                fix.products_categories, PRODUCT_CATEGORIES
            ) or ["Autre — à qualifier"]
            fix.services_categories = filter_cats(
                fix.services_categories, SERVICE_CATEGORIES
            )
            fix.technologies_categories = filter_cats(
                fix.technologies_categories, TECHNOLOGY_CATEGORIES
            )
            return eid, fix

    n_done = 0
    n_ok = 0
    n_err = 0
    t0 = time.time()
    last_save = time.time()
    tasks = [process(r) for r in targets]
    for coro in asyncio.as_completed(tasks):
        eid, fix = await coro
        n_done += 1
        if fix is None:
            n_err += 1
        else:
            n_ok += 1
            overrides[str(eid)] = {
                "activity_1liner": fix.activity_1liner,
                "products": fix.products,
                "services": fix.services,
                "target_buyers": fix.target_buyers,
                "technologies": fix.technologies,
                "why_target": fix.why_target,
            }
            cat_overrides[str(eid)] = {
                "products_categories": fix.products_categories,
                "services_categories": fix.services_categories,
                "technologies_categories": fix.technologies_categories,
            }
        if n_done % 10 == 0 or n_done == len(targets):
            dt = time.time() - t0
            rate = n_done / dt if dt > 0 else 0
            print(f"  [{n_done:>4}/{len(targets)}] ok={n_ok} err={n_err} ({rate:.2f}/s)")
        if time.time() - last_save > 30:
            save_overrides(overrides)
            save_cat_overrides(cat_overrides)
            last_save = time.time()

    save_overrides(overrides)
    save_cat_overrides(cat_overrides)
    dt = time.time() - t0
    print(f"\nDone in {dt:.1f}s : ok={n_ok}, err={n_err}")
    print(f"Overrides → {OVERRIDES_PATH.name} (total {len(overrides)})")
    print(f"Categories → {CAT_OVERRIDES_PATH.name} (total {len(cat_overrides)})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--apply", action="store_true",
                   help="After LLM, run merge → normalize → export")
    args = p.parse_args()
    rc = asyncio.run(main_async(args))
    if args.apply and rc == 0:
        import subprocess
        for cmd in (
            ["python", "-m", "scripts.merge_manual_overrides"],
            ["python", "-m", "scripts.normalize_categories"],
            ["python", "-m", "scripts.export_xlsx"],
        ):
            print(f"\n→ {' '.join(cmd)}")
            subprocess.run(cmd, check=False)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
