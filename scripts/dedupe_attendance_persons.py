"""Dedupe attendance_signals on (person_name, canonical_company_name).

The same person can be present several times when scraped from
multiple sources (initial OSINT + later trade-association). We keep
the row with the **richest data** and delete the others.

Scoring
-------
For each row in a (name, canonical_company) group :

    +1  for each of: email, phone, linkedin, website, address, portfolio
        (markers in notes)
    +5  if person_role is set
    +3  if source_platform is a trade-association source
        (gicat-fr / bdsv-de / ads-group-uk / their -*-contact / -*-dirigeant
         variants) — these have higher signal density
    -1  if entity_type is "person" but person_name is missing/placeholder

Tie-breaker: lowest id wins (oldest = first inserted).

Run :
    python -m scripts.dedupe_attendance_persons             # dry-run
    python -m scripts.dedupe_attendance_persons --execute   # delete
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "eurosatory.db"

_TAGS_TO_COUNT = ("[email]", "[phone]", "[linkedin]", "[website]",
                  "[address]", "[portfolio]")

_TRADE_ASSOC_PLATFORMS = (
    "gicat-fr", "gicat-fr-dirigeant",
    "bdsv-de",
    "ads-group-uk", "ads-group-uk-2nd-contact",
    "enrich-so-ads-employee",
)


def normalise_name(name: str) -> str:
    if not name:
        return ""
    n = re.sub(r"\s+", " ", name.strip().lower())
    # Drop civility prefixes
    n = re.sub(r"^(m\.|mme|mlle|mr|mrs|ms|dr|prof)\.?\s+", "", n)
    # Drop placeholder periods
    if not re.sub(r"[.\s]", "", n):
        return ""
    return n


def quality_score(row: sqlite3.Row) -> int:
    score = 0
    notes = row["notes"] or ""
    for tag in _TAGS_TO_COUNT:
        if tag in notes:
            score += 1
    if (row["person_role"] or "").strip():
        score += 5
    if row["source_platform"] in _TRADE_ASSOC_PLATFORMS:
        score += 3
    name = (row["person_name"] or "").strip()
    if not name or not re.sub(r"[.\s]", "", name):
        score -= 1
    return score


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        "SELECT id, person_name, person_role, company_name, "
        "       canonical_company_name, source_platform, notes "
        "FROM attendance_signals "
        "WHERE person_name IS NOT NULL AND person_name != ''"
    )
    rows = cur.fetchall()

    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        n = normalise_name(r["person_name"])
        if not n:
            continue
        canon = (r["canonical_company_name"] or
                 r["company_name"] or "").lower().strip()
        if not canon:
            continue
        groups[(n, canon)].append(r)

    n_dup_groups = 0
    delete_ids: set[int] = set()
    samples: list[str] = []

    for (n, canon), rs in groups.items():
        if len(rs) <= 1:
            continue
        n_dup_groups += 1
        rs_sorted = sorted(
            rs,
            key=lambda r: (-quality_score(r), r["id"]),
        )
        keeper = rs_sorted[0]
        for r in rs_sorted[1:]:
            delete_ids.add(r["id"])
        if len(samples) < 5:
            samples.append(
                f"\n{n} @ {canon}:\n"
                f"  KEEP id={keeper['id']:>5}  "
                f"role={(keeper['person_role'] or '')[:30]:<30}  "
                f"plat={keeper['source_platform']}  "
                f"score={quality_score(keeper)}\n"
                + "\n".join(
                    f"  drop id={r['id']:>5}  "
                    f"role={(r['person_role'] or '')[:30]:<30}  "
                    f"plat={r['source_platform']}  "
                    f"score={quality_score(r)}"
                    for r in rs_sorted[1:]
                )
            )

    print(f"Person+Company duplicate groups : {n_dup_groups}")
    print(f"Rows to delete                  : {len(delete_ids)}")
    print()
    print("Samples:")
    for s in samples:
        print(s)

    if args.execute and delete_ids:
        for chunk_start in range(0, len(delete_ids), 500):
            chunk = list(delete_ids)[chunk_start:chunk_start + 500]
            cur.execute(
                f"DELETE FROM attendance_signals "
                f"WHERE id IN ({','.join('?' * len(chunk))})",
                chunk,
            )
        con.commit()
        print(f"\n✅ Deleted {len(delete_ids)} duplicate person rows.")
    elif delete_ids:
        print(f"\n[dry-run] Re-run with --execute to delete.")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
