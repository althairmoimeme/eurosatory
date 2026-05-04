"""LLM-driven enrichment for the long-tail fiches with no product category.

Why this exists
---------------
After running ``run_targeting_profiles → merge_manual_overrides →
normalize_categories``, ~290 fiches still have an empty / "Autre" bucket
in ``products_categories``. They split in two groups:

1. ~285 already in ``profiles_manual_overrides.json`` but whose products are
   too specific / too niche / institutional → no regex hits in
   ``taxonomy_normalize.py``. Genuine gap that hand-writing 285 more
   patterns wouldn't scale.
2. ~8 not yet enriched at all (institutional, very thin sources).

For both groups we ask Claude Opus 4.7 to produce:
- the 6 standard targeting fields (overrides existing entries, if any),
- the *categories* fields (chosen from the CLOSED 75 / 23 / 31 lists in
  ``taxonomy_normalize.py``) so they show up in Excel filters.

We persist:
- 6 fields → ``data/llm_test/profiles_manual_overrides.json`` (for next
  pipeline run, merged via ``merge_manual_overrides.py``).
- 3 *categories* fields → ``data/llm_test/llm_categories_overrides.json``
  (read at the end of ``normalize_categories.py`` and used as ground-truth
  override for the listed exhibitor_ids).

Cost
----
Opus 4.7 with adaptive thinking + ephemeral cache on the system prompt:
~6500 chars of system prompt cached (~2000 tokens), per-fiche user message
~2000-4000 tokens. Roughly $0.02 per fiche → ~$6 total for 293 fiches.

Usage
-----
    python -m scripts.llm_enrich_remaining            # process every empty fiche
    python -m scripts.llm_enrich_remaining --limit 5  # smoke test
    python -m scripts.llm_enrich_remaining --eid 39   # single fiche
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

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
from app.processors.targeting_profile import (  # noqa: E402
    TARGET_BUYER_LABELS,
    SYSTEM_PROMPT as BASE_SYSTEM_PROMPT,
)

EXPORT_DIR = ROOT / "data" / "exports"
LLM_DIR = ROOT / "data" / "llm_test"
DB_PATH = ROOT / "data" / "eurosatory.db"
OVERRIDES_PATH = LLM_DIR / "profiles_manual_overrides.json"
CATEGORIES_OVERRIDES_PATH = LLM_DIR / "llm_categories_overrides.json"


# ---------------------------------------------------------------------------
# Output schema — 6 standard fields + 3 categorisation fields
# ---------------------------------------------------------------------------


_PRODUCT_LIST_TXT = "\n".join(f"  • {c}" for c in PRODUCT_CATEGORIES)
_SERVICE_LIST_TXT = "\n".join(f"  • {c}" for c in SERVICE_CATEGORIES)
_TECH_LIST_TXT = "\n".join(f"  • {c}" for c in TECHNOLOGY_CATEGORIES)


SYSTEM_PROMPT = (
    BASE_SYSTEM_PROMPT
    + f"""

CHAMPS *_categories — TAXONOMIES FERMÉES
=========================================
Les 3 champs ci-dessous DOIVENT être renseignés en choisissant des labels \
exclusivement parmi les listes fermées suivantes. N'invente AUCUN label. \
Si rien ne s'applique, retourne ["Autre — à qualifier"].

products_categories — choisis 1 à 4 labels parmi :
{_PRODUCT_LIST_TXT}

services_categories — choisis 0 à 3 labels parmi :
{_SERVICE_LIST_TXT}

technologies_categories — choisis 0 à 3 labels parmi :
{_TECH_LIST_TXT}

RÈGLE D'OR pour ces 3 champs : préfère 1 label JUSTE plutôt que 4 labels \
discutables. Mieux vaut peu de catégories solides qu'un saupoudrage.
"""
)


class EnrichedProfile(BaseModel):
    """6-field commercial profile + 3 closed-taxonomy category fields."""

    activity_1liner: str = Field(
        description=(
            "Une phrase, ≤120 caractères, en français, commençant par un "
            "verbe d'action conjugué à la 3e personne du singulier."
        ),
    )
    products: list[str] = Field(
        default_factory=list,
        description="3-7 produits CONCRETS (ou [] si pure prestation).",
    )
    services: list[str] = Field(
        default_factory=list,
        description="0-5 services COMMERCIAUX (ou [] si pur fabricant).",
    )
    target_buyers: list[str] = Field(
        default_factory=list,
        description=(
            "1-4 valeurs parmi : 'MoD / Armées', 'Primes défense', "
            "'Sécurité civile', 'Industriels défense', 'Export / international'."
        ),
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="0-5 tags techniques démontrablement maîtrisés.",
    )
    why_target: str = Field(
        description=(
            "Une phrase contextuelle FR : pourquoi un commercial défense "
            "devrait s'y intéresser (achat / compétiteur / partenaire / canal)."
        ),
    )
    products_categories: list[str] = Field(
        default_factory=list,
        description=(
            "1-4 labels EXCLUSIVEMENT depuis la liste fermée fournie dans "
            "le system prompt. Aucune invention de label."
        ),
    )
    services_categories: list[str] = Field(
        default_factory=list,
        description=(
            "0-3 labels EXCLUSIVEMENT depuis la liste fermée services."
        ),
    )
    technologies_categories: list[str] = Field(
        default_factory=list,
        description=(
            "0-3 labels EXCLUSIVEMENT depuis la liste fermée technologies."
        ),
    )


# ---------------------------------------------------------------------------
# DB context loader (the same 7 sources used by infer_product_categories)
# ---------------------------------------------------------------------------


def load_db_sources() -> dict[int, dict[str, str]]:
    """Return ``{exhibitor_id: {sp, presentation, hl, activity_summary,
    keywords, crawl}}`` ready to be substituted in the prompt template."""
    if not DB_PATH.exists():
        print(f"WARN: missing {DB_PATH} — falling back to empty sources")
        return {}

    out: dict[int, dict[str, str]] = {}
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
            out[eid]["crawl"] = " ".join(snippets[:3])[:6000]
    con.close()
    return out


# ---------------------------------------------------------------------------
# Prompt building — one user message per fiche
# ---------------------------------------------------------------------------


USER_TEMPLATE = """\
EXPOSANT À PROFILER
===================
Société : {company_name}
Pays    : {country}
Site    : {website}
Pavillon Eurosatory : {pavilion}

DESCRIPTION OFFICIELLE FINDERR
==============================
{sp}

PRESENTATION LONGUE
===================
{presentation}

HEADLINE / META DU SITE
=======================
{hl}

ACTIVITY SUMMARY (ancienne extraction)
======================================
{activity_summary}

MOTS-CLÉS DÉCLARÉS PAR L'EXPOSANT
=================================
{keywords}

EXTRAITS DE CRAWL (homepage + pages clés)
=========================================
{crawl}

FICHE EN COURS (à remplacer si besoin)
======================================
- activity_1liner : {prior_activity}
- products        : {prior_products}
- target_buyers   : {prior_targets}
- technologies    : {prior_techs}
- why_target      : {prior_why}

CONSIGNE
========
Renvoie un JSON conforme au schéma ``EnrichedProfile`` (9 champs).
Tous les *_categories DOIVENT venir des listes fermées du system prompt.
Si l'exposant n'a aucun produit physique propre (institutionnel pur, média, \
financier, événementiel, université…) → ``products`` = [] mais \
``products_categories`` doit quand même être un label institutionnel \
pertinent (ex : 'Représentation institutionnelle (cluster, fédération, \
chambre)', 'Médias & publications défense', etc.) — JAMAIS [] vide.
"""


def build_user_message(rec: dict, srcs: dict[str, str]) -> str:
    return USER_TEMPLATE.format(
        company_name=rec["company_name"],
        country=rec.get("country") or "?",
        website=rec.get("website") or "?",
        pavilion=rec.get("pavilion") or "?",
        sp=(srcs.get("sp") or "(non renseigné)")[:2000],
        presentation=(srcs.get("presentation") or "(non renseigné)")[:3000],
        hl=(srcs.get("hl") or "(aucun)")[:500],
        activity_summary=(srcs.get("activity_summary") or "(aucun)")[:1500],
        keywords=(srcs.get("keywords") or "(aucun)")[:1000],
        crawl=(srcs.get("crawl") or "(aucun)")[:6000],
        prior_activity=(rec.get("activity_1liner") or "(vide)")[:200],
        prior_products=", ".join(rec.get("products") or []) or "(vide)",
        prior_targets=", ".join(rec.get("target_buyers") or []) or "(vide)",
        prior_techs=", ".join(rec.get("technologies") or []) or "(vide)",
        prior_why=(rec.get("why_target") or "(vide)")[:200],
    )


# ---------------------------------------------------------------------------
# LLM client (async with concurrency limit)
# ---------------------------------------------------------------------------


def make_client():
    from anthropic import AsyncAnthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set in .env")
    return AsyncAnthropic(api_key=api_key, max_retries=4)


def _validate_categories(
    cats: list[str],
    allowed: tuple[str, ...],
) -> list[str]:
    """Drop any label not in the closed list."""
    allowed_set = set(allowed)
    return [c for c in cats if c in allowed_set]


async def call_llm(client, model: str, user_msg: str) -> Optional[EnrichedProfile]:
    try:
        resp = await client.messages.parse(
            model=model,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_format=EnrichedProfile,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:  # noqa: BLE001
        print(f"   ✗ LLM error: {e!r}")
        return None
    return resp.parsed_output


async def process_one(
    sem: asyncio.Semaphore,
    client,
    model: str,
    rec: dict,
    srcs: dict[str, str],
) -> tuple[int, Optional[EnrichedProfile]]:
    async with sem:
        eid = int(rec["exhibitor_id"])
        user_msg = build_user_message(rec, srcs)
        prof = await call_llm(client, model, user_msg)
        if prof is None:
            return eid, None
        # Filter categories to closed list (defensive)
        prof.products_categories = (
            _validate_categories(prof.products_categories, PRODUCT_CATEGORIES)
            or ["Autre — à qualifier"]
        )
        prof.services_categories = _validate_categories(
            prof.services_categories, SERVICE_CATEGORIES
        )
        prof.technologies_categories = _validate_categories(
            prof.technologies_categories, TECHNOLOGY_CATEGORIES
        )
        # Filter target_buyers to closed list
        allowed_t = set(TARGET_BUYER_LABELS)
        prof.target_buyers = [t for t in prof.target_buyers if t in allowed_t]
        return eid, prof


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def is_empty_cats(rec: dict) -> bool:
    cats = rec.get("products_categories") or []
    if not cats:
        return True
    if len(cats) == 1 and cats[0].startswith("Autre"):
        return True
    return False


def load_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main_async(args) -> int:
    src = EXPORT_DIR / "targeting_profiles_final.json"
    if not src.exists():
        print(f"ERR: missing {src}")
        return 1

    recs = json.loads(src.read_text(encoding="utf-8"))

    if args.eid is not None:
        targets = [r for r in recs if int(r["exhibitor_id"]) == args.eid]
    else:
        targets = [r for r in recs if is_empty_cats(r)]

    # Skip eids already present in the LLM categories side file (avoid
    # re-querying fiches the LLM already honestly tagged as 'Autre').
    if args.skip_processed:
        already = set(load_existing(CATEGORIES_OVERRIDES_PATH).keys())
        before = len(targets)
        targets = [r for r in targets if str(r["exhibitor_id"]) not in already]
        print(f"  --skip-processed: {before} → {len(targets)} (skipped {before - len(targets)})")

    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        print("Nothing to process.")
        return 0

    print(f"Targets: {len(targets)} fiches")

    db_sources = load_db_sources()
    overrides = load_existing(OVERRIDES_PATH)
    cat_overrides = load_existing(CATEGORIES_OVERRIDES_PATH)

    client = make_client()
    model = os.getenv("LLM_INTELLIGENCE_MODEL", "claude-opus-4-7")
    print(f"Model: {model}, concurrency: {args.concurrency}")

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [
        process_one(sem, client, model, r, db_sources.get(int(r["exhibitor_id"]), {}))
        for r in targets
    ]

    n_done = 0
    n_ok = 0
    n_err = 0
    t0 = time.time()
    last_save = time.time()

    for coro in asyncio.as_completed(tasks):
        eid, prof = await coro
        n_done += 1
        if prof is None:
            n_err += 1
        else:
            n_ok += 1
            # Persist the 6 standard fields into manual_overrides.json
            overrides[str(eid)] = {
                "activity_1liner": prof.activity_1liner,
                "products": prof.products,
                "services": prof.services,
                "target_buyers": prof.target_buyers,
                "technologies": prof.technologies,
                "why_target": prof.why_target,
            }
            # Persist categories into the side file
            cat_overrides[str(eid)] = {
                "products_categories": prof.products_categories,
                "services_categories": prof.services_categories,
                "technologies_categories": prof.technologies_categories,
            }

        if n_done % 5 == 0 or n_done == len(targets):
            dt = time.time() - t0
            rate = n_done / dt if dt > 0 else 0
            print(
                f"  [{n_done:>3}/{len(targets)}]  ok={n_ok}  err={n_err}  "
                f"({rate:.2f} fiches/s)"
            )

        # Save every 30s to avoid losing progress
        if time.time() - last_save > 30:
            save_json(OVERRIDES_PATH, overrides)
            save_json(CATEGORIES_OVERRIDES_PATH, cat_overrides)
            last_save = time.time()

    save_json(OVERRIDES_PATH, overrides)
    save_json(CATEGORIES_OVERRIDES_PATH, cat_overrides)
    dt = time.time() - t0
    print(
        f"\nDone in {dt:.1f}s : ok={n_ok}, err={n_err} "
        f"(overrides → {OVERRIDES_PATH.name}, categories → "
        f"{CATEGORIES_OVERRIDES_PATH.name})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N fiches (smoke test).",
    )
    parser.add_argument(
        "--eid", type=int, default=None,
        help="Process only the given exhibitor_id (debug).",
    )
    parser.add_argument(
        "--concurrency", type=int, default=6,
        help="Number of concurrent LLM calls (default 6).",
    )
    parser.add_argument(
        "--skip-processed", action="store_true",
        help="Skip exhibitor_ids already in llm_categories_overrides.json.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
