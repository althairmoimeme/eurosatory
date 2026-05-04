"""Run the rule-based targeting profile transformer over all exhibitors.

Outputs
-------
- ``data/exports/targeting_profiles_all.json``
    One JSON object per exhibitor with all 6 fields + score + meta.
- ``data/exports/targeting_profiles_all.csv``
    Flat CSV — one row per exhibitor, list-fields joined with " · ".
- ``data/llm_test/profiles_all_scored.json``
    Scoring summary used to drive the manual-review queue.

Usage
-----
    .venv/bin/python scripts/run_targeting_profiles.py
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path
from collections import Counter

# Make app importable when running this script directly
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.processors.targeting_profile_rules import build_profile  # noqa: E402

DB_PATH = ROOT / "data" / "eurosatory.db"
EXPORT_DIR = ROOT / "data" / "exports"
LLM_TEST_DIR = ROOT / "data" / "llm_test"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
LLM_TEST_DIR.mkdir(parents=True, exist_ok=True)


def _safe_json(v):
    if not v:
        return []
    if isinstance(v, list):
        return v
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute(
        """
        SELECT
            e.id, e.company_name, e.country_name, e.website_url, e.pavilion,
            e.short_presentation, e.business_areas,
            i.headline, i.built_products, i.sold_offerings, i.services,
            i.technologies, i.target_clients, i.markets_served,
            i.business_model
        FROM exhibitors e
        LEFT JOIN exhibitor_intelligence i ON i.exhibitor_id = e.id
        ORDER BY e.id
        """
    )
    rows = cur.fetchall()
    con.close()

    print(f"Loaded {len(rows)} exhibitors from {DB_PATH}")

    out_records: list[dict] = []
    score_buckets = Counter()
    strength_buckets = Counter()
    for r in rows:
        profile = build_profile(
            company_name=r["company_name"] or "",
            country=r["country_name"],
            headline=r["headline"],
            short_presentation=r["short_presentation"],
            business_areas=_safe_json(r["business_areas"]),
            built_products=_safe_json(r["built_products"]),
            sold_offerings=_safe_json(r["sold_offerings"]),
            services=_safe_json(r["services"]),
            technologies=_safe_json(r["technologies"]),
            target_clients=_safe_json(r["target_clients"]),
            markets_served=_safe_json(r["markets_served"]),
            business_model=r["business_model"],
        )
        rec = {
            "exhibitor_id": r["id"],
            "company_name": r["company_name"],
            "country": r["country_name"],
            "website": r["website_url"],
            "pavilion": r["pavilion"],
            **profile,
        }
        out_records.append(rec)
        # bucket: 0-19, 20-39, 40-59, 60-79, 80-100
        score_buckets[(profile["completeness_score"] // 20) * 20] += 1
        strength_buckets[profile["data_source_strength"]] += 1

    # ---- write JSON ----
    json_path = EXPORT_DIR / "targeting_profiles_all.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(out_records, f, ensure_ascii=False, indent=2)
    print(f"Wrote {json_path}  ({json_path.stat().st_size:,} bytes)")

    # ---- write CSV ----
    csv_path = EXPORT_DIR / "targeting_profiles_all.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow([
            "exhibitor_id", "company_name", "country", "website", "pavilion",
            "activity_1liner", "products", "services", "target_buyers",
            "technologies", "why_target", "completeness_score",
            "data_source_strength",
        ])
        for r in out_records:
            w.writerow([
                r["exhibitor_id"], r["company_name"], r["country"] or "",
                r["website"] or "", r["pavilion"] or "",
                r["activity_1liner"],
                " · ".join(r["products"]),
                " · ".join(r["services"]),
                ", ".join(r["target_buyers"]),
                " · ".join(r["technologies"]),
                r["why_target"],
                r["completeness_score"],
                r["data_source_strength"],
            ])
    print(f"Wrote {csv_path}  ({csv_path.stat().st_size:,} bytes)")

    # ---- write summary stats ----
    summary = {
        "n": len(out_records),
        "avg_score": round(
            sum(r["completeness_score"] for r in out_records) / max(len(out_records), 1),
            1,
        ),
        "score_buckets": dict(sorted(score_buckets.items())),
        "strength_buckets": dict(strength_buckets),
        "n_score_100": sum(1 for r in out_records if r["completeness_score"] == 100),
        "n_score_under_50": sum(1 for r in out_records if r["completeness_score"] < 50),
        "n_score_under_30": sum(1 for r in out_records if r["completeness_score"] < 30),
    }
    summary_path = LLM_TEST_DIR / "profiles_all_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"Wrote {summary_path}")
    print()
    print("==== SUMMARY ====")
    print(f"  N total           : {summary['n']}")
    print(f"  Avg score         : {summary['avg_score']}/100")
    print(f"  Score = 100       : {summary['n_score_100']}")
    print(f"  Score < 50        : {summary['n_score_under_50']}")
    print(f"  Score < 30        : {summary['n_score_under_30']}")
    print(f"  Score buckets     : {summary['score_buckets']}")
    print(f"  Strength buckets  : {summary['strength_buckets']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
