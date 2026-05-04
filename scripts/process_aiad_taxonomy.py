"""Apply AIAD IT/EN taxonomy mapping and persist categories + tier."""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.processors.aiad_taxonomy_mapping import map_text_to_categories  # noqa: E402
from app.processors.supply_chain_tier import compute_tier  # noqa: E402

DB_PATH = ROOT / "data" / "eurosatory.db"


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


def main() -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT id, source_snippet, notes FROM attendance_signals "
        "WHERE source_platform='aiad-it'"
    )
    rows = cur.fetchall()
    print(f"Processing {len(rows)} AIAD rows…")
    n_ok = 0
    cat_dist: Counter[str] = Counter()
    tier_dist: Counter[str] = Counter()
    for sid, snippet, notes in rows:
        text = snippet or ""
        cats = map_text_to_categories(text, max_cats=5)
        tier = compute_tier(cats) if cats else "N/A"
        new_notes = notes or ""
        new_notes = replace_or_add(
            new_notes, "[aiad-categories]",
            " · ".join(cats) if cats else "(aucune)",
        )
        new_notes = replace_or_add(new_notes, "[aiad-tier]", tier)
        new_notes = new_notes[:8000]
        cur.execute(
            "UPDATE attendance_signals SET notes = ? WHERE id = ?",
            (new_notes, sid),
        )
        if cats:
            n_ok += 1
            for c in cats:
                cat_dist[c] += 1
        tier_dist[tier] += 1
    con.commit()
    con.close()
    print(f"\n{n_ok}/{len(rows)} fiches with ≥1 category.")
    print("\nTier distribution :")
    for t in ("OEM", "Tier 1", "Tier 2", "Tier 3", "Tier 4", "N/A"):
        print(f"  {tier_dist.get(t, 0):>4}  {t}")
    print("\nTop 15 categories :")
    for c, n in cat_dist.most_common(15):
        print(f"  {n:>4}  {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
