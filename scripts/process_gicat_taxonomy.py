"""Apply GICAT FR-text taxonomy mapping and persist derived categories +
supply-chain tier on every GICAT row.

Mirrors ``process_bdsv_taxonomy.py`` but for GICAT.
Markers persisted in notes: ``[gicat-categories]`` / ``[gicat-tier]``.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.processors.gicat_taxonomy_mapping import map_text_to_categories  # noqa: E402
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
        "SELECT id, source_snippet, notes "
        "FROM attendance_signals WHERE source_platform='gicat-fr'"
    )
    rows = cur.fetchall()
    print(f"Processing {len(rows)} GICAT rows…")

    n_ok = 0
    cat_dist: Counter[str] = Counter()
    tier_dist: Counter[str] = Counter()

    for sid, snippet, notes in rows:
        text = snippet or ""
        for marker in ("domaines", "secteurs", "produits"):
            m = re.search(rf"\[{marker}\]\s*(.+?)(?=\n\[|\Z)",
                          notes or "", re.DOTALL)
            if m:
                text += " " + m.group(1)
        cats = map_text_to_categories(text, max_cats=5)
        tier = compute_tier(cats) if cats else "N/A"

        new_notes = notes or ""
        new_notes = replace_or_add(
            new_notes, "[gicat-categories]",
            " · ".join(cats) if cats else "(aucune)",
        )
        new_notes = replace_or_add(new_notes, "[gicat-tier]", tier)
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
    print(f"\nDone. {n_ok}/{len(rows)} fiches with ≥1 product category.")
    print("\nTier distribution :")
    for t in ("OEM", "Tier 1", "Tier 2", "Tier 3", "Tier 4", "N/A"):
        print(f"  {tier_dist.get(t, 0):>4}  {t}")
    print("\nTop 15 product categories :")
    for c, n in cat_dist.most_common(15):
        print(f"  {n:>4}  {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
