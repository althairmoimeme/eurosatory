"""Apply ADS-taxonomy mapping to every scraped detail row and persist the
derived product categories + supply-chain tier alongside the raw JSON-LD.

Reads:
    attendance_signals.notes  contains a ``[ads-detail] {…}`` line with
                              the schema.org block (knowsAbout list).

Writes (back into the same column):
    [ads-categories] cat1 · cat2 · cat3
    [ads-tier] OEM | Tier 1 | Tier 2 | Tier 3 | Tier 4 | N/A

These are easy to grep in code and parse in the Streamlit dataframe loader
without changing the SQL schema.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.processors.ads_taxonomy_mapping import map_tags_to_categories  # noqa: E402
from app.processors.supply_chain_tier import compute_tier  # noqa: E402

DB_PATH = ROOT / "data" / "eurosatory.db"


_BLOB_RX = re.compile(r"\[ads-detail\]\s*(\{.*?\})\s*(?:\n|$)", re.DOTALL)


def parse_blob(notes: str) -> dict:
    if not notes:
        return {}
    m = _BLOB_RX.search(notes)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def replace_or_add_line(notes: str, prefix: str, value: str) -> str:
    """Replace the line starting with ``prefix`` if it exists, else append."""
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
        "SELECT id, notes FROM attendance_signals "
        "WHERE source_platform='ads-group-uk' "
        "  AND notes LIKE '%[ads-detail]%' "
        "ORDER BY id"
    )
    rows = cur.fetchall()
    print(f"Processing {len(rows)} ADS rows…")

    n_with_cats = 0
    from collections import Counter
    tier_dist: Counter[str] = Counter()
    cat_dist: Counter[str] = Counter()

    for sid, notes in rows:
        blob = parse_blob(notes or "")
        knows = blob.get("knowsAbout") or []
        cats = map_tags_to_categories(knows)
        tier = compute_tier(cats) if cats else "N/A"

        new_notes = notes or ""
        new_notes = replace_or_add_line(
            new_notes,
            "[ads-categories]",
            " · ".join(cats) if cats else "(aucune)",
        )
        new_notes = replace_or_add_line(
            new_notes,
            "[ads-tier]",
            tier,
        )
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
    print("\nTop 20 product categories :")
    for c, n in cat_dist.most_common(20):
        print(f"  {n:>4}  {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
