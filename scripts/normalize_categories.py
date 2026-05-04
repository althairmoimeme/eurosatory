"""Apply canonical categories to ``targeting_profiles_final.json``.

Reads the merged file, runs ``add_categories()`` on every record, writes
back to disk and to the CSV. Reports coverage of the rule library.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.processors.product_inference import infer_product_categories  # noqa: E402
from app.processors.supply_chain_tier import (  # noqa: E402
    TIER_ORDER, add_supply_chain_tier,
)
from app.processors.taxonomy_normalize import (  # noqa: E402
    add_categories,
    PRODUCT_CATEGORIES, SERVICE_CATEGORIES, TECHNOLOGY_CATEGORIES,
)

EXPORT_DIR = ROOT / "data" / "exports"


def main() -> int:
    src = EXPORT_DIR / "targeting_profiles_final.json"
    if not src.exists():
        print(f"ERR: missing {src}")
        return 1

    # Load every text source from DB the inferenceur could possibly use:
    #   - short_presentation + presentation (long)
    #   - headline + activity_summary
    #   - keywords (very precise, hand-crafted by Finderr)
    #   - up to 4000 chars of crawled homepage / about-us text
    import json as _json
    import sqlite3
    db_path = ROOT / "data" / "eurosatory.db"
    sp_by_id: dict[int, dict] = {}
    if db_path.exists():
        con = sqlite3.connect(db_path)
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
                kw_list = _json.loads(kw_raw) if kw_raw else []
            except Exception:  # noqa: BLE001
                kw_list = []
            sp_by_id[int(row["id"])] = {
                "sp": row["short_presentation"] or "",
                "presentation": row["presentation"] or "",
                "hl": row["headline"] or "",
                "activity_summary": row["activity_summary"] or "",
                "keywords": " ".join(kw_list) if kw_list else "",
            }
        # Add up to 3 crawl-page excerpts per exhibitor (homepage + about + service)
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
            if eid in sp_by_id:
                # Concatenate up to 3 pages, max 4000 chars total
                blob = " ".join(snippets[:3])[:4000]
                sp_by_id[eid]["crawl"] = blob
        con.close()

    records = json.loads(src.read_text(encoding="utf-8"))
    n_inferred = 0
    n_enriched = 0
    for r in records:
        add_categories(r)
        # Second-chance inference : when products_categories ends up empty
        # (or only "Autre — à qualifier"), scan the activity + SP +
        # headline for product keywords. Fills the Excel filter for the
        # ~500 fiches that previously had no bucket.
        cats = r.get("products_categories") or []
        only_autre = (
            len(cats) == 1 and cats[0].startswith("Autre")
        )
        eid = r.get("exhibitor_id")
        sources = sp_by_id.get(int(eid), {}) if eid else {}

        if not cats or only_autre:
            inferred = infer_product_categories(
                activity_1liner=r.get("activity_1liner"),
                short_presentation=sources.get("sp"),
                presentation=sources.get("presentation"),
                headline=sources.get("hl"),
                activity_summary=sources.get("activity_summary"),
                keywords=sources.get("keywords"),
                crawl=sources.get("crawl"),
            )
            if inferred:
                r["products_categories"] = inferred
                n_inferred += 1
        elif len(cats) < 2:
            # Sparse coverage — try to enrich with additional categories
            # (don't replace, just append).
            extra = infer_product_categories(
                activity_1liner=r.get("activity_1liner"),
                short_presentation=sources.get("sp"),
                presentation=sources.get("presentation"),
                headline=sources.get("hl"),
                activity_summary=sources.get("activity_summary"),
                keywords=sources.get("keywords"),
                crawl=sources.get("crawl"),
                max_cats=4,
            )
            for c in extra:
                if c not in cats and not c.startswith("Autre"):
                    cats.append(c)
                    n_enriched += 1
            r["products_categories"] = cats[:5]
    if n_inferred or n_enriched:
        print(f"  ↳ Inferred categories on {n_inferred} fiches "
              f"+ enriched {n_enriched} sparse fiches")

    # ---- LLM categories overrides (data/llm_test/llm_categories_overrides.json) ----
    # The optional ``llm_enrich_remaining.py`` script writes per-exhibitor
    # category triples chosen from the closed taxonomies. They are the
    # ground-truth fallback for fiches that the regex pipeline couldn't
    # categorise. We apply them last so they always win.
    cat_override_path = ROOT / "data" / "llm_test" / "llm_categories_overrides.json"
    if cat_override_path.exists():
        try:
            cat_overrides = json.loads(cat_override_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cat_overrides = {}
        n_llm_applied = 0
        for r in records:
            ov = cat_overrides.get(str(r.get("exhibitor_id")))
            if not ov:
                continue
            for fld in (
                "products_categories",
                "services_categories",
                "technologies_categories",
            ):
                vals = ov.get(fld) or []
                if vals:
                    r[fld] = vals
            n_llm_applied += 1
        if n_llm_applied:
            print(
                f"  ↳ Applied LLM category overrides on {n_llm_applied} fiches"
            )

    # ---- Supply-chain tier (run AFTER LLM overrides so categories are final) ----
    for r in records:
        add_supply_chain_tier(r)
    from collections import Counter as _Counter
    tier_dist = _Counter(r.get("supply_chain_tier", "N/A") for r in records)
    print(
        "  ↳ Supply-chain tier : "
        + " · ".join(f"{tier_dist.get(t, 0)} {t}"
                     for t in (*TIER_ORDER, "N/A"))
    )

    src.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {src}")

    csv_path = EXPORT_DIR / "targeting_profiles_final.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow([
            "exhibitor_id", "company_name", "country", "website", "pavilion",
            "activity_1liner",
            "supply_chain_tier",
            "products", "products_categories",
            "services", "services_categories",
            "target_buyers",
            "technologies", "technologies_categories",
            "why_target",
            "completeness_score", "data_source_strength",
        ])
        for r in records:
            w.writerow([
                r["exhibitor_id"], r["company_name"], r["country"] or "",
                r["website"] or "", r["pavilion"] or "",
                r["activity_1liner"],
                r.get("supply_chain_tier", "N/A"),
                " · ".join(r["products"]),
                " · ".join(r["products_categories"]),
                " · ".join(r["services"]),
                " · ".join(r["services_categories"]),
                ", ".join(r["target_buyers"]),
                " · ".join(r["technologies"]),
                " · ".join(r["technologies_categories"]),
                r["why_target"],
                r["completeness_score"],
                r["data_source_strength"],
            ])
    print(f"Wrote {csv_path}")

    # ---- coverage report ----
    print()
    print("==== CATEGORIES COVERAGE ====")

    for label, key, allowed in [
        ("Products",    "products_categories",    PRODUCT_CATEGORIES),
        ("Services",    "services_categories",    SERVICE_CATEGORIES),
        ("Technologies","technologies_categories", TECHNOLOGY_CATEGORIES),
    ]:
        c = Counter()
        n_records_with_cat = 0
        n_records_with_data = 0
        n_unmatched = 0
        for r in records:
            specifics = r.get(key.replace("_categories",""), []) or []
            cats = r.get(key, []) or []
            if specifics:
                n_records_with_data += 1
            if cats and not (len(cats) == 1 and cats[0].startswith("Autre")):
                n_records_with_cat += 1
            if cats == ["Autre — à qualifier"]:
                n_unmatched += 1
            for cat in cats:
                c[cat] += 1
        print(f"\n--- {label} ---")
        print(f"  records with raw labels  : {n_records_with_data}")
        print(f"  records with ≥1 category : {n_records_with_cat}")
        print(f"  records ONLY 'Autre'     : {n_unmatched}")
        print(f"  unique categories used   : {len([k for k in c if not k.startswith('Autre')])}/{len(allowed)-1}")
        print(f"  Top categories:")
        for cat, n in c.most_common(15):
            print(f"    {n:>4}  {cat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
