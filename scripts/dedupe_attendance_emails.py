"""Dedupe attendance_signals on email.

When the same email address appears on 2+ rows (typical fan-out of a
generic company email across all dirigeants of that company), we keep
ONE row — the most senior person — and delete the others.

Arbitration policy
------------------
Within each (email, canonical_company) group, score each row:

    +20  source_platform is the "primary" variant
         (no -2nd-contact, no -dirigeant, no enrich-so suffix)
    +12  role mentions Président / PDG / Founder / CEO
    +10  role mentions Directeur Général / Managing Director /
                       Chief Executive
    + 8  role mentions Vice-Président / VP / Deputy CEO
    + 6  role starts with Directeur / Director / Chief
    + 4  role mentions Manager / Chef / Head
    + 2  person_name has at least 2 words (proper full name)
    + 1  rows with role at all
    -10  rows with no person_name

Keep the row with the highest score (ties broken by lowest id).
All other rows in the group are deleted.

Cross-company duplicates (same email at different companies — rare)
are treated independently: dedup is *within* each canonical_company.

Usage :
    python -m scripts.dedupe_attendance_emails               # dry-run
    python -m scripts.dedupe_attendance_emails --execute     # delete
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

_EMAIL_RX = re.compile(r"\[email\]\s*([^\s\n]+)")


def normalise_email(em: str) -> str:
    return em.strip().lower().rstrip(".,;:)")


def role_rank(role: str) -> int:
    """Lower rank wins. 0 = top exec, 9 = no role.

    Ranking, in priority order :
      0  Président / PDG / Founder / CEO / Managing Director
      1  Directeur Général / Directrice Générale / Director General
      2  Vice-Président / VP / Deputy CEO
      3  Chief X Officer (CFO, CTO, COO …)
      4  Directeur / Director (other than DG)
      5  Manager / Chef / Head of / Responsable
      6  any other declared role
      9  empty / NULL role
    """
    if not role:
        return 9
    r = role.lower()
    if any(k in r for k in ("président", "presidente", "pdg",
                             "founder", "fondateur", "founding",
                             "ceo", "managing director",
                             "président directeur général",
                             "owner", "propriétaire")):
        return 0
    if any(k in r for k in ("directeur général", "directrice générale",
                             "general manager", "director general",
                             "chief executive")):
        return 1
    if any(k in r for k in ("vice-président", "vice présidente",
                             "vice president", " vp ", "deputy ceo")):
        return 2
    if "chief " in r:  # Chief X Officer
        return 3
    if r.startswith("directeur") or r.startswith("director"):
        return 4
    if any(k in r for k in ("manager", "chef", "head of",
                             "responsable")):
        return 5
    return 6


def is_primary_platform(plat: str) -> bool:
    if not plat:
        return False
    if plat.endswith("-2nd-contact") or plat.endswith("-dirigeant"):
        return False
    if plat.startswith("enrich-so"):
        return False
    return True


def sort_key(row: sqlite3.Row) -> tuple:
    """Smallest tuple wins → best row to keep."""
    role = (row["person_role"] or "").strip()
    name = (row["person_name"] or "").strip()
    return (
        role_rank(role),                  # primary signal: seniority
        0 if is_primary_platform(row["source_platform"]) else 1,
        0 if name and len(name.split()) >= 2 else 1,
        row["id"],
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true",
                   help="Actually delete duplicate rows (otherwise dry-run).")
    args = p.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        "SELECT id, person_name, person_role, company_name, "
        "       canonical_company_name, source_platform, notes "
        "FROM attendance_signals WHERE notes LIKE '%[email]%'"
    )
    rows = cur.fetchall()
    print(f"Total signals with email: {len(rows)}")

    # Group by (email, canonical) — the email may match multiple in notes,
    # but typically a row has at most 1 email tag.
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        for em in _EMAIL_RX.findall(r["notes"] or ""):
            em_n = normalise_email(em)
            if "@" not in em_n or "." not in em_n.split("@")[-1]:
                continue
            canon = r["canonical_company_name"] or r["company_name"] or ""
            groups[(em_n, canon.lower())].append(r)

    # Find groups with > 1 row
    n_groups = 0
    n_keep = 0
    n_delete = 0
    delete_ids: set[int] = set()
    samples: list[str] = []
    for (em, canon), rs in groups.items():
        if len(rs) <= 1:
            continue
        n_groups += 1
        # Smallest sort key wins (best seniority / primary / id)
        rs_sorted = sorted(rs, key=sort_key)
        keeper = rs_sorted[0]
        n_keep += 1
        for r in rs_sorted[1:]:
            delete_ids.add(r["id"])
            n_delete += 1
        if len(samples) < 8:
            samples.append(
                f"\nEmail {em} @ {canon}:\n"
                f"  KEEP id={keeper['id']:>5}  "
                f"{(keeper['person_name'] or '?'):<35} | "
                f"{(keeper['person_role'] or '')[:40]:<40} | "
                f"{keeper['source_platform']}\n"
                + "\n".join(
                    f"  drop id={r['id']:>5}  "
                    f"{(r['person_name'] or '?'):<35} | "
                    f"{(r['person_role'] or '')[:40]:<40} | "
                    f"{r['source_platform']}"
                    for r in rs_sorted[1:]
                )
            )

    print(f"Duplicate groups: {n_groups}")
    print(f"Rows to keep:    {n_keep}")
    print(f"Rows to delete:  {n_delete}")
    print()
    print("Sample arbitrage:")
    for s in samples:
        print(s)

    if args.execute and delete_ids:
        cur.execute("BEGIN")
        for chunk_start in range(0, len(delete_ids), 500):
            chunk = list(delete_ids)[chunk_start:chunk_start + 500]
            cur.execute(
                f"DELETE FROM attendance_signals "
                f"WHERE id IN ({','.join('?' * len(chunk))})",
                chunk,
            )
        con.commit()
        print(f"\n✅ Deleted {len(delete_ids)} duplicate rows.")
    elif delete_ids:
        print(f"\n[dry-run] Re-run with --execute to delete "
              f"{len(delete_ids)} rows.")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
