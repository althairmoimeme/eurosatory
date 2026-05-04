"""Apply BDSV German-text taxonomy mapping and persist derived
categories + supply-chain tier on every BDSV row.

Reads ``source_snippet`` (Beschreibung) + ``[portfolio] …`` line
from notes, runs the regex rule book in
``app.processors.bdsv_taxonomy_mapping``, and writes back two new
notes lines :

    [bdsv-categories]  cat1 · cat2 · cat3
    [bdsv-tier]        OEM | Tier 1-4 | N/A

These mirror the ``[ads-categories]`` / ``[ads-tier]`` convention so
the Streamlit dataframe loader can derive them generically.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.processors.bdsv_taxonomy_mapping import map_text_to_categories  # noqa: E402
from app.processors.supply_chain_tier import compute_tier  # noqa: E402

DB_PATH = ROOT / "data" / "eurosatory.db"


_PORT_RX = re.compile(r"\[portfolio\]\s*(.*?)(?=\n\[|\Z)", re.DOTALL)


def replace_or_add(notes: str, prefix: str, value: str) -> str:
    line = f"{prefix} {value}"
    lines = (notes or "").splitlines()
    out: list[str] = []
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
        "FROM attendance_signals WHERE source_platform='bdsv-de'"
    )
    rows = cur.fetchall()
    print(f"Processing {len(rows)} BDSV rows…")

    n_with_cats = 0
    cat_dist: Counter[str] = Counter()
    tier_dist: Counter[str] = Counter()

    for sid, snippet, notes in rows:
        text = snippet or ""
        m = _PORT_RX.search(notes or "")
        if m:
            text += " " + m.group(1)
        cats = map_text_to_categories(text, max_cats=5)
        tier = compute_tier(cats) if cats else "N/A"

        new_notes = notes or ""
        new_notes = replace_or_add(
            new_notes, "[bdsv-categories]",
            " · ".join(cats) if cats else "(aucune)",
        )
        new_notes = replace_or_add(new_notes, "[bdsv-tier]", tier)
        new_notes = new_notes[:8000]
        cur.execute(
            "UPDATE attendance_signals SET notes = ? WHERE id = ?",
            (new_notes, sid),
        )
        if cats:
            n_with_cats += 1
            for c in cats:
                cat_dist[c] += 1
        tier_dist[tier] += 1

    con.commit()
    con.close()

    print(f"\nDone. {n_with_cats}/{len(rows)} fiches with ≥1 product category.")
    print("\nTier distribution :")
    for t in ("OEM", "Tier 1", "Tier 2", "Tier 3", "Tier 4", "N/A"):
        print(f"  {tier_dist.get(t, 0):>4}  {t}")
    print("\nTop 15 product categories :")
    for c, n in cat_dist.most_common(15):
        print(f"  {n:>4}  {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
