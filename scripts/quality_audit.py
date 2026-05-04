"""Quality audit — sample 100 records, surface issues, write a report.

The audit checks for the issues the user is most likely to spot when
opening the XLSX :

1. Activity 1-liner missing or obvious junk (cookie banners, HTML).
2. Products list completely generic ("équipement logistique" alone).
3. ``why_target`` wholly templated ("À qualifier — données …").
4. Categories all collapsed to "Autre".
5. Specific labels that should have categorised but did not.
6. Score / data-strength / category coherence (e.g. a 100/100 with
   only "Autre" categories signals a categorisation gap).

Outputs
-------
- ``data/exports/quality_audit_100.json``     — raw findings per row
- ``data/exports/quality_audit_summary.txt``  — human-readable report
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXPORT_DIR = ROOT / "data" / "exports"
SAMPLE_SIZE = 100
SEED = 2026


def main() -> int:
    src = EXPORT_DIR / "targeting_profiles_final.json"
    records = json.loads(src.read_text(encoding="utf-8"))

    rng = random.Random(SEED)
    sample = rng.sample(records, k=SAMPLE_SIZE)

    issues_per_row: list[dict] = []
    issue_counter: Counter[str] = Counter()

    for r in sample:
        row_issues: list[str] = []

        a = (r.get("activity_1liner") or "").strip()
        if not a:
            row_issues.append("activity_empty")
        elif a.startswith("(données publiques trop pauvres"):
            row_issues.append("activity_thin_data")
        elif "<" in a or "cloudflare" in a.lower():
            row_issues.append("activity_junk_html")
        elif len(a) > 140:
            row_issues.append(f"activity_too_long({len(a)}c)")

        prods = r.get("products") or []
        cats = r.get("products_categories") or []
        if not prods and r.get("completeness_score", 0) >= 60:
            row_issues.append("score>=60_but_no_products")
        if cats and all(c.startswith("Autre") for c in cats):
            row_issues.append("categories_only_autre")
        if prods and len(prods) == 1 and prods[0] in (
            "équipement logistique", "véhicules militaires",
            "réseaux de communication", "logiciels métier défense",
        ):
            row_issues.append("only_generic_product")

        wt = r.get("why_target") or ""
        if wt.startswith("À qualifier"):
            row_issues.append("why_target_templated")

        targets = r.get("target_buyers") or []
        if not targets and r.get("completeness_score", 0) >= 60:
            row_issues.append("score>=60_but_no_targets")

        if row_issues:
            for i in row_issues:
                issue_counter[i.split("(")[0]] += 1
            issues_per_row.append({
                "exhibitor_id": r["exhibitor_id"],
                "company_name": r["company_name"],
                "country": r["country"],
                "score": r["completeness_score"],
                "src": r["data_source_strength"],
                "issues": row_issues,
            })

    out_json = EXPORT_DIR / "quality_audit_100.json"
    out_json.write_text(
        json.dumps(issues_per_row, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {out_json}")

    # ---- text summary ----
    n_with_issues = len(issues_per_row)
    n_clean = SAMPLE_SIZE - n_with_issues

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"QUALITY AUDIT  ({SAMPLE_SIZE} fiches aléatoires, seed={SEED})")
    lines.append("=" * 72)
    lines.append(f"Fiches sans aucun issue       : {n_clean} ({100*n_clean/SAMPLE_SIZE:.0f}%)")
    lines.append(f"Fiches avec ≥1 issue          : {n_with_issues} ({100*n_with_issues/SAMPLE_SIZE:.0f}%)")
    lines.append("")
    lines.append("Distribution par type d'issue (toutes lignes):")
    for issue, n in issue_counter.most_common():
        lines.append(f"  {n:>3}  {issue}")
    lines.append("")
    lines.append("Détail des fiches problématiques:")
    lines.append("-" * 72)
    for r in issues_per_row[:50]:
        lines.append(
            f"#{r['exhibitor_id']:>5}  {r['company_name'][:34]:<34}  "
            f"({r['country'] or '?':<10})  score={r['score']:>3}  "
            f"src={r['src']}  → {', '.join(r['issues'])}"
        )

    out_txt = EXPORT_DIR / "quality_audit_summary.txt"
    out_txt.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_txt}")
    print()
    print("\n".join(lines[:25]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
