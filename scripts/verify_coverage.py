"""Quick coverage report on the final targeting profiles JSON.

Prints :
- Total fiches
- Fiches with at least one product category (non-Autre)
- Fiches with empty / Autre-only category
- Coverage % overall + per data_source_strength
- Top 10 categories
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXPORT_DIR = ROOT / "data" / "exports"


def main() -> int:
    src = EXPORT_DIR / "targeting_profiles_final.json"
    recs = json.loads(src.read_text(encoding="utf-8"))
    n = len(recs)

    def has_real_cat(r):
        cats = r.get("products_categories") or []
        if not cats:
            return False
        if len(cats) == 1 and cats[0].startswith("Autre"):
            return False
        return True

    n_with = sum(1 for r in recs if has_real_cat(r))
    n_without = n - n_with

    print(f"Total fiches : {n}")
    print(f"With ≥1 real category : {n_with} ({n_with*100/n:.1f}%)")
    print(f"Without (empty/Autre) : {n_without} ({n_without*100/n:.1f}%)")
    print()

    by_source: dict[str, list[bool]] = {}
    for r in recs:
        src_strength = r.get("data_source_strength", "?")
        by_source.setdefault(src_strength, []).append(has_real_cat(r))

    print("Coverage by data_source_strength:")
    for s, vals in sorted(by_source.items()):
        ok = sum(vals)
        tot = len(vals)
        print(f"  {s:>10} : {ok}/{tot} ({ok*100/tot:.1f}%)")
    print()

    c = Counter()
    for r in recs:
        for cat in r.get("products_categories") or []:
            c[cat] += 1
    print("Top 15 product categories:")
    for cat, k in c.most_common(15):
        print(f"  {k:>4}  {cat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
