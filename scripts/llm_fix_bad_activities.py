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
    r"Sous[- ]traite|Forge|Usine|Anime|Approvisionne|Organise|Mutualise|"
    r"Coordonne|Produit|G[èe]re|Imprime|Assemble|Installe|Certifie|Test[e]?|"
    r"Calibre|Construit|Soude|Met|Transforme|D[ée]ploie|Promeut|F[ée]d[èe]re|"
    r"Effectue|Propose|Soutient|Accompagne|Programme|Surveille|Investit|"
    r"Finance|Modernise|D[ée]mant[èe]le|Refurbit|Identifie|Restructure|"
    r"Applique|Recycle|Brute)",
    re.I | re.U,
)


# Strong signature of rule-based-template hallucinations : the scraper
# stitches 2-3 generic products from a fixed list, often unrelated to
# what the company actually does. The smoking gun = at least TWO of
# these slot phrases comma-separated.
_TEMPLATE_SLOT = (
    r"(véhicules militaires|véhicules tactiques|équipement logistique|"
    r"équipement du fantassin|batteries / sources d['’]énergie|"
    r"batteries / sources d energie|réseaux de communication"
    r"(?:\s+militaires)?|capteurs embarqués|moteurs / propulsion|"
    r"infrastructure RF durcie|stations de commandement|"
    r"systèmes optroniques|protection balistique|drones aériens|"
    r"systèmes anti-drone|robots terrestres \(UGV\)|"
    r"logiciels métier défense|systèmes navals|simulateurs d['’]entraînement|"
    r"munitions|armes|radars)"
)
_RULE_TEMPLATE_2SLOTS_RX = re.compile(
    rf"{_TEMPLATE_SLOT}\s*,\s*{_TEMPLATE_SLOT}",
    re.I | re.U,
)
_GENERIC_TAIL_RX = re.compile(
    r"pour (les |la |des |l['’])?"
    r"(armées(\s+et\s+forces\s+de\s+sécurité)?|"
    r"forces de sécurité|primes défense|"
    r"défense et (la )?sécurité|défense et (l['’])?industrie|"
    r"industriels défense)\s*\.?\s*$",
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
    if len(s) < 50 or len(s) > 220:
        return True
    # Rule-based template hallucinations — 2+ slot phrases comma-joined,
    # OR slot phrase + generic buyer suffix.
    if _RULE_TEMPLATE_2SLOTS_RX.search(s):
        return True
    if _GENERIC_TAIL_RX.search(s) and re.search(_TEMPLATE_SLOT, s, re.I | re.U):
        return True
    return False


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """\
Tu es un analyste B2B défense / sécurité européenne senior. Tu produis \
des fiches de ciblage commercial pour une équipe de business developers \
qui prospectent au salon Eurosatory. Précision FACTUELLE absolue \
exigée — pas de remplissage marketing.

⚠️ EXIGENCE ABSOLUE — ``activity_1liner`` :
  • UNE phrase précise, 80-160 caractères, en français.
  • COMMENCE par un verbe d'action 3ᵉ personne : Conçoit · Fabrique · \
Édite · Distribue · Intègre · Maintient · Forme · Conseille · \
Représente · Fournit · Développe · Audite · Sous-traite · Forge · \
Usine · Pilote · Conduit · Réalise · Opère · Anime · Modernise · \
Promeut · Investit · Finance · Démantèle.

  • CITE LES PRODUITS / SERVICES SPÉCIFIQUES VRAIMENT VENDUS — pas une \
liste générique. Si la source mentionne un produit phare par nom, \
utilise-le. Précise la NICHE (ex: "viseurs jour/nuit pour fusil \
d'assaut", pas "systèmes optroniques").
  • Mentionne le SECTEUR client précis (gendarmerie, aéronautique \
militaire, sous-marins, NRBC…) plutôt que "défense et sécurité".
  • PROSCRIT formel :
    ❌ Marketing : "leader", "innovant", "expert", "world-class", \
"with X employees", "Founded in 1985".
    ❌ Listes templates génériques : "véhicules militaires, équipement \
logistique, batteries / sources d'énergie".
    ❌ Suffixes vagues : "pour la défense et la sécurité", "pour primes \
défense", "pour forces de sécurité".
  • Si vraiment pas assez de matière : "(données publiques trop pauvres \
pour qualification fiable)".

EXEMPLES BONS
  ✅ "Conçoit et fabrique des viseurs jour/nuit (LUVOR, MiniSight) \
pour fusils d'assaut des forces spéciales et police."
  ✅ "Sous-traite l'usinage CNC 5-axes de pièces critiques en \
superalliages pour moteurs d'hélicoptères Safran et Rolls-Royce."
  ✅ "Édite IRIS, plateforme IA de fusion ISR temps réel pour images \
satellite, drones et radio HF."
  ✅ "Fabrique des aciers blindés (Armox, Ramor) et tôles balistiques \
pour véhicules KMW, GDELS et chantiers navals."

EXEMPLES MAUVAIS (à éviter)
  ❌ "Conçoit et fabrique des véhicules militaires, équipement \
logistique, batteries / sources d'énergie pour forces de sécurité."
  ❌ "Provides comprehensive solutions for the defense industry."
  ❌ "Leader européen avec 40 ans d'expertise dans la défense."

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
Renvoie un JSON ``Fix`` strict. ABSOLU :
  • activity_1liner : 80-160 chars, FR, démarre par verbe d'action 3ᵉ \
personne. NOMME les produits / niches / clients SPÉCIFIQUES (pas \
de listes génériques type "véhicules militaires, équipement \
logistique"). Si la source mentionne un nom de produit phare \
(ex: "FightLite MCR", "ASTER", "Skydio X10"), inclus-le. \
PAS de "leader", "innovant", "with X employees".
  • Si vraiment pas assez de matière (< 30 mots utilisables dans les \
sources) : "(données publiques trop pauvres pour qualification \
fiable — pays X, à requalifier)".
  • why_target : 1 phrase concrète explicitant l'angle commercial \
(acheteur potentiel / concurrent / partenaire / sous-traitant / \
canal d'accès marché).
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
            # Sanity check : action verb OR explicit "données pauvres"
            # marker (the prompt allows this as the fallback for fiches
            # with insufficient public data).
            act = fix.activity_1liner.strip()
            if not (
                GOOD_VERB_RX.match(act)
                or "données publiques trop pauvres" in act.lower()
            ):
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
