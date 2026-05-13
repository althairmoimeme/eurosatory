"""Build a slim 50×50 demo DB for the public LeadForges demo.

Strategy : pick 50 *representative* exhibitors (mix of OEMs, intégrateurs,
équipementiers, distributeurs from different countries) and 50 attendance
signals attached to those exhibitors (mix of LinkedIn posts, corporate
announcements, press articles). Drop everything else.

The demo DB is shipped under ``data/eurosatory_demo.db`` and is loaded by
Streamlit when the URL query param ``?demo=1`` is present, OR when the
env var ``LEADFORGES_DEMO=1`` is set.

Usage :
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
N_SIGNALS = 50


def main() -> int:
    if not os.path.exists(SRC):
        print(f"❌ source DB missing : {SRC}", file=sys.stderr)
        return 1

    # Fresh copy → trim
    shutil.copy(SRC, DST)
    con = sqlite3.connect(DST)
    con.execute("PRAGMA foreign_keys = OFF")

    # 1. Pick 50 representative exhibitors :
    #    - Diversity by country (top 8 countries)
    #    - Mix of company types (OEM, intégrateur, équipementier…)
    #    - Prefer exhibitors that HAVE attendance signals (so the demo
    #      shows the signal layer in action).
    keep_exhibitor_ids = []
    cur = con.execute("""
        WITH ranked AS (
          SELECT e.id,
                 e.company_name,
                 e.country_iso2,
                 (SELECT COUNT(*) FROM attendance_signals s
                  WHERE LOWER(TRIM(COALESCE(s.canonical_company_name, s.company_name)))
                      = LOWER(TRIM(e.company_name))) AS n_sig,
                 ROW_NUMBER() OVER (
                   PARTITION BY e.country_iso2
                   ORDER BY (SELECT COUNT(*) FROM attendance_signals s
                            WHERE LOWER(TRIM(COALESCE(s.canonical_company_name, s.company_name)))
                                = LOWER(TRIM(e.company_name))) DESC,
                            e.company_name
                 ) AS rn
          FROM exhibitors e
          WHERE e.country_iso2 IS NOT NULL
            AND e.company_name IS NOT NULL
        )
        SELECT id FROM ranked
        WHERE rn <= 8                              -- max 8 per country
          AND n_sig >= 1                           -- has at least 1 signal
        ORDER BY n_sig DESC, country_iso2
        LIMIT ?
    """, (N_EXHIBITORS,))
    keep_exhibitor_ids = [r[0] for r in cur.fetchall()]
    print(f"Kept {len(keep_exhibitor_ids)} exhibitors")

    # 2. Pick 50 attendance signals attached to those exhibitors. Prefer
    #    a mix of signal_type / source_platform.
    placeholders = ",".join(["?"] * len(keep_exhibitor_ids))
    cur = con.execute(f"""
        WITH exhib_names AS (
          SELECT LOWER(TRIM(company_name)) AS k FROM exhibitors WHERE id IN ({placeholders})
        ),
        scored AS (
          SELECT s.id, s.signal_type, s.source_platform, s.presence_score,
                 ROW_NUMBER() OVER (
                   PARTITION BY s.source_platform
                   ORDER BY s.presence_score DESC NULLS LAST, s.id DESC
                 ) AS rn
          FROM attendance_signals s
          INNER JOIN exhib_names en
            ON LOWER(TRIM(COALESCE(s.canonical_company_name, s.company_name))) = en.k
          WHERE s.is_duplicate = 0
        )
        SELECT id FROM scored
        WHERE rn <= 15
        ORDER BY presence_score DESC NULLS LAST, id DESC
        LIMIT ?
    """, (*keep_exhibitor_ids, N_SIGNALS))
    keep_signal_ids = [r[0] for r in cur.fetchall()]
    print(f"Kept {len(keep_signal_ids)} signals")

    # 3. Drop everything else.
    keep_e = ",".join(str(i) for i in keep_exhibitor_ids)
    keep_s = ",".join(str(i) for i in keep_signal_ids)

    deleted_e = con.execute(
        f"DELETE FROM exhibitors WHERE id NOT IN ({keep_e})"
    ).rowcount
    deleted_s = con.execute(
        f"DELETE FROM attendance_signals WHERE id NOT IN ({keep_s})"
    ).rowcount
    print(f"Deleted {deleted_e} exhibitors, {deleted_s} signals")

    # 4. Also trim child / linking tables.
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
            pass  # table doesn't exist or schema differs

    # 5. Drop curation tables that have no buyer-facing value.
    for tbl in ("custom_lists", "custom_list_members",
                "attendance_watches", "commercial_notes"):
        try:
            con.execute(f"DELETE FROM {tbl}")
        except sqlite3.OperationalError:
            pass

    con.commit()
    con.execute("VACUUM")
    con.close()

    sz = os.path.getsize(DST) / 1024 / 1024
    print(f"✅ Demo DB built : {DST} ({sz:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
