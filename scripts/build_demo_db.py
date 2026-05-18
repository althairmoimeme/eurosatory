"""Build a slim 50×50 demo DB for the public LeadForges demo.

Quality-first strategy
──────────────────────
  The demo must look PROFESSIONAL. A row with only a name (no role, no
  email, no phone, no LinkedIn) hurts conversion — it suggests the full
  product is mostly empty too.

  This script computes a completeness score for every signal :

    +1  if person_name  is non-empty
    +1  if person_role  is non-empty (and not "Staff"/"Employee" filler)
    +2  if notes contains "Direct email: …"   (the most valuable field)
    +1  if notes contains a phone "+…"
    +1  if source_url is a linkedin.com URL

  Max score = 6. We only ship signals with score ≥ 4 (= at least
  name + role + (email or phone or linkedin)).

  For each exhibitor :
    - REQUIRE at least 2 signals with completeness ≥ 4
    - Keep their top 2-3 signals (sorted by completeness desc)
    - Drop the empty ones (a buyer trying the demo never sees the
      "Marie Claire Dupont — None — None — None" rows)

  For the 50 exhibitors :
    - Diversify by country (top 8 EU/NA/IL/etc.)
    - Diversify by company_type when possible

The demo DB is loaded by Streamlit when the URL has ``?demo=1``.

Usage
─────
    python -m scripts.build_demo_db
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys

SRC = "data/eurosatory_deploy.db"
DST = "data/eurosatory_demo.db"
N_EXHIBITORS = 50
SIGNALS_PER_EXHIBITOR = 2  # cap : 2 best signals per company
MIN_COMPLETENESS = 4       # name + role + (email or linkedin)


# SQL expression that scores a signal's completeness (0-6)
_COMPLETENESS_SQL = """
  (CASE WHEN s.person_name IS NOT NULL AND s.person_name != '' THEN 1 ELSE 0 END) +
  (CASE WHEN s.person_role IS NOT NULL AND s.person_role != ''
        AND LOWER(s.person_role) NOT IN ('staff','employee','—','-') THEN 1 ELSE 0 END) +
  (CASE WHEN s.notes LIKE '%Direct email:%' THEN 2 ELSE 0 END) +
  (CASE WHEN s.notes LIKE '%Phone:%' OR s.notes LIKE '%+%' THEN 1 ELSE 0 END) +
  (CASE WHEN s.source_url LIKE '%linkedin.com/%' THEN 1 ELSE 0 END)
"""


def main() -> int:
    if not os.path.exists(SRC):
        print(f"❌ source DB missing : {SRC}", file=sys.stderr)
        return 1

    # Fresh copy → trim
    shutil.copy(SRC, DST)
    con = sqlite3.connect(DST)
    con.execute("PRAGMA foreign_keys = OFF")

    # 1. Find exhibitors that have at least 2 "complete" signals
    #    (completeness >= 4). Pick top N_EXHIBITORS, diversifying by
    #    country.
    print("Selecting exhibitors with at least 2 high-completeness signals...")
    cur = con.execute(f"""
        WITH signal_scores AS (
          SELECT
            s.id AS sid,
            LOWER(TRIM(COALESCE(s.canonical_company_name, s.company_name))) AS company_key,
            {_COMPLETENESS_SQL} AS completeness
          FROM attendance_signals s
          WHERE s.is_duplicate = 0
        ),
        company_quality AS (
          SELECT
            company_key,
            COUNT(*) AS total_signals,
            SUM(CASE WHEN completeness >= {MIN_COMPLETENESS} THEN 1 ELSE 0 END) AS n_complete,
            MAX(completeness) AS best_score
          FROM signal_scores
          GROUP BY company_key
          HAVING n_complete >= 2
        ),
        ranked_exhibitors AS (
          SELECT
            e.id,
            e.company_name,
            e.country_iso2,
            cq.n_complete,
            cq.best_score,
            ROW_NUMBER() OVER (
              PARTITION BY e.country_iso2
              ORDER BY cq.n_complete DESC, cq.best_score DESC, e.company_name
            ) AS rn_in_country
          FROM exhibitors e
          INNER JOIN company_quality cq
            ON cq.company_key = LOWER(TRIM(e.company_name))
          WHERE e.country_iso2 IS NOT NULL
            AND e.company_name IS NOT NULL
        )
        SELECT id FROM ranked_exhibitors
        WHERE rn_in_country <= 8   -- max 8 per country for diversity
        ORDER BY n_complete DESC, best_score DESC
        LIMIT ?
    """, (N_EXHIBITORS,))
    keep_exhibitor_ids = [r[0] for r in cur.fetchall()]
    print(f"  Kept {len(keep_exhibitor_ids)} exhibitors "
          f"(each has ≥ 2 high-quality signals)")

    if not keep_exhibitor_ids:
        print("❌ No exhibitors meet the quality criteria. Aborting.")
        con.close()
        return 1

    # 2. For each chosen exhibitor, keep their top SIGNALS_PER_EXHIBITOR
    #    signals (by completeness DESC). Only signals with completeness
    #    >= MIN_COMPLETENESS survive — buyers never see empty rows.
    placeholders = ",".join(["?"] * len(keep_exhibitor_ids))
    cur = con.execute(f"""
        WITH exhib_names AS (
          SELECT LOWER(TRIM(company_name)) AS k
          FROM exhibitors WHERE id IN ({placeholders})
        ),
        scored AS (
          SELECT
            s.id,
            {_COMPLETENESS_SQL} AS completeness,
            LOWER(TRIM(COALESCE(s.canonical_company_name, s.company_name))) AS company_key,
            ROW_NUMBER() OVER (
              PARTITION BY LOWER(TRIM(COALESCE(s.canonical_company_name, s.company_name)))
              ORDER BY {_COMPLETENESS_SQL} DESC, s.id DESC
            ) AS rn_in_company
          FROM attendance_signals s
          INNER JOIN exhib_names en
            ON LOWER(TRIM(COALESCE(s.canonical_company_name, s.company_name))) = en.k
          WHERE s.is_duplicate = 0
        )
        SELECT id FROM scored
        WHERE completeness >= {MIN_COMPLETENESS}
          AND rn_in_company <= {SIGNALS_PER_EXHIBITOR}
    """, keep_exhibitor_ids)
    keep_signal_ids = [r[0] for r in cur.fetchall()]
    print(f"  Kept {len(keep_signal_ids)} signals (only completeness ≥ "
          f"{MIN_COMPLETENESS}, max {SIGNALS_PER_EXHIBITOR} per exhibitor)")

    # 3. Drop everything else.
    keep_e = ",".join(str(i) for i in keep_exhibitor_ids)
    keep_s = ",".join(str(i) for i in keep_signal_ids) if keep_signal_ids else "0"

    deleted_e = con.execute(
        f"DELETE FROM exhibitors WHERE id NOT IN ({keep_e})"
    ).rowcount
    deleted_s = con.execute(
        f"DELETE FROM attendance_signals WHERE id NOT IN ({keep_s})"
    ).rowcount
    print(f"  Deleted {deleted_e} exhibitors, {deleted_s} signals")

    # 4. Trim child / linking tables.
    for tbl in (
        "exhibitor_categories", "exhibitor_classifications",
        "exhibitor_contacts", "exhibitor_intelligence", "exhibitor_tags",
    ):
        try:
            n = con.execute(
                f"DELETE FROM {tbl} WHERE exhibitor_id NOT IN ({keep_e})"
            ).rowcount
            print(f"  trimmed {tbl} : -{n} rows")
        except sqlite3.OperationalError:
            pass

    # 5. Drop curation tables (no buyer-facing value).
    for tbl in ("custom_lists", "custom_list_members",
                "attendance_watches", "commercial_notes"):
        try:
            con.execute(f"DELETE FROM {tbl}")
        except sqlite3.OperationalError:
            pass

    con.commit()
    con.execute("VACUUM")

    # 6. Final stats — print the completeness distribution of the
    #    shipped demo so we can sanity-check.
    print()
    print("Demo DB quality check:")
    stats = con.execute("""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN person_name IS NOT NULL AND person_name != '' THEN 1 ELSE 0 END) AS has_name,
          SUM(CASE WHEN person_role IS NOT NULL AND person_role != '' THEN 1 ELSE 0 END) AS has_role,
          SUM(CASE WHEN notes LIKE '%Direct email:%' THEN 1 ELSE 0 END) AS has_email,
          SUM(CASE WHEN notes LIKE '%Phone:%' OR notes LIKE '%+%' THEN 1 ELSE 0 END) AS has_phone,
          SUM(CASE WHEN source_url LIKE '%linkedin.com/%' THEN 1 ELSE 0 END) AS has_linkedin
        FROM attendance_signals
    """).fetchone()
    print(f"  signals total : {stats[0]}")
    print(f"    with name    : {stats[1]} ({100*stats[1]//stats[0]}%)")
    print(f"    with role    : {stats[2]} ({100*stats[2]//stats[0]}%)")
    print(f"    with email   : {stats[3]} ({100*stats[3]//stats[0]}%)")
    print(f"    with phone   : {stats[4]} ({100*stats[4]//stats[0]}%)")
    print(f"    with LinkedIn: {stats[5]} ({100*stats[5]//stats[0]}%)")
    con.close()

    sz = os.path.getsize(DST) / 1024 / 1024
    print(f"\n✅ Demo DB rebuilt : {DST} ({sz:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
