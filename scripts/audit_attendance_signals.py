"""Comprehensive audit of the ``attendance_signals`` table.

Reports :
    A — volume + field coverage per source_platform
    B — quality issues : junk person_names, malformed emails,
                         placeholder roles, suspicious notes blobs
    C — duplicate buckets : emails (within company), persons
                            (name+company), companies (canonical),
                            cross-source "same domain" overlaps

Read-only. No mutation.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "eurosatory.db"

EMAIL_RX = re.compile(r"\[email\]\s*([^\s\n]+)")
PHONE_RX = re.compile(r"\[phone\]\s*([^\n]+)")
WEB_RX = re.compile(r"\[website\]\s*([^\s\n]+)")
ADDR_RX = re.compile(r"\[address\]\s*([^\n]+)")


def main() -> int:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ====================================================================
    # SECTION A — Volume + field coverage per source
    # ====================================================================
    print("=" * 70)
    print("SECTION A — Volume & field coverage per source_platform")
    print("=" * 70)
    cur.execute(
        "SELECT source_platform, "
        "       COUNT(*) AS total, "
        "       SUM(CASE WHEN person_name IS NOT NULL AND person_name != '' "
        "                 AND person_name NOT LIKE '. .' THEN 1 ELSE 0 END) AS with_name, "
        "       SUM(CASE WHEN person_role IS NOT NULL AND person_role != '' "
        "                THEN 1 ELSE 0 END) AS with_role, "
        "       SUM(CASE WHEN notes LIKE '%[email]%' THEN 1 ELSE 0 END) AS with_email, "
        "       SUM(CASE WHEN notes LIKE '%[phone]%' THEN 1 ELSE 0 END) AS with_phone, "
        "       SUM(CASE WHEN notes LIKE '%[website]%' THEN 1 ELSE 0 END) AS with_web, "
        "       SUM(CASE WHEN notes LIKE '%[address]%' THEN 1 ELSE 0 END) AS with_addr, "
        "       SUM(CASE WHEN notes LIKE '%[linkedin]%' THEN 1 ELSE 0 END) AS with_li, "
        "       SUM(CASE WHEN canonical_company_name IS NOT NULL AND canonical_company_name != '' THEN 1 ELSE 0 END) AS with_canon "
        "FROM attendance_signals "
        "GROUP BY source_platform "
        "ORDER BY total DESC"
    )
    print(f"{'source':<35}{'tot':>5} {'name':>5} {'role':>5} {'mail':>5} {'phon':>5} {'web':>5} {'addr':>5} {'lkin':>5} {'cano':>5}")
    print("-" * 110)
    grand_total = 0
    for r in cur.fetchall():
        grand_total += r["total"]
        print(
            f"{(r['source_platform'] or 'NULL'):<35}"
            f"{r['total']:>5} "
            f"{r['with_name']:>5} "
            f"{r['with_role']:>5} "
            f"{r['with_email']:>5} "
            f"{r['with_phone']:>5} "
            f"{r['with_web']:>5} "
            f"{r['with_addr']:>5} "
            f"{r['with_li']:>5} "
            f"{r['with_canon']:>5}"
        )
    print(f"\nGRAND TOTAL : {grand_total}")

    # ====================================================================
    # SECTION B — Quality issues
    # ====================================================================
    print("\n" + "=" * 70)
    print("SECTION B — Quality issues")
    print("=" * 70)

    # B.1 Junk person_names (very short / numbers / boilerplate)
    print("\nB.1 — Suspicious person_name patterns")
    cur.execute(
        "SELECT person_name, source_platform, COUNT(*) AS c "
        "FROM attendance_signals "
        "WHERE person_name IS NOT NULL AND person_name != '' "
        "  AND person_name NOT LIKE '% % %' "  # less than 3 words = often single-word noise
        "  AND ("
        "    LENGTH(person_name) < 5 "
        "    OR person_name = '. .' "
        "    OR person_name LIKE '%@%' "
        "    OR person_name LIKE '%http%' "
        "    OR person_name LIKE '%GmbH%' "
        "    OR person_name LIKE '%Ltd%' "
        "    OR person_name LIKE '%Limited%' "
        "    OR person_name LIKE '%Inc.%' "
        "    OR person_name GLOB '*[0-9]*' "
        "  ) "
        "GROUP BY person_name, source_platform "
        "ORDER BY c DESC LIMIT 25"
    )
    for r in cur.fetchall():
        print(f"  {r['c']:>3}× [{r['source_platform']:<25}] {r['person_name']!r}")

    # B.2 Junk role/title patterns
    print("\nB.2 — Suspicious person_role")
    cur.execute(
        "SELECT person_role, COUNT(*) AS c "
        "FROM attendance_signals "
        "WHERE person_role IS NOT NULL AND person_role != '' "
        "  AND ("
        "    LENGTH(person_role) > 100 "
        "    OR person_role LIKE '%@%' "
        "    OR person_role LIKE '%http%' "
        "    OR person_role GLOB '*[0-9][0-9][0-9]*' "  # 3+ digits
        "  ) "
        "GROUP BY person_role ORDER BY c DESC LIMIT 15"
    )
    for r in cur.fetchall():
        print(f"  {r['c']:>3}× {r['person_role'][:90]!r}")

    # B.3 Email noise
    print("\nB.3 — Suspicious emails")
    bad_emails = Counter()
    cur.execute("SELECT notes FROM attendance_signals WHERE notes LIKE '%[email]%'")
    for (notes,) in cur.fetchall():
        for em in EMAIL_RX.findall(notes or ""):
            em = em.lower().strip()
            # Heuristic noise patterns
            if any(em.endswith(s) for s in (".png", ".jpg", ".gif", ".svg", ".webp")):
                bad_emails[em] += 1
            elif any(s in em for s in ("@2x.", "@3x.", "wixpress.com", "sentry.io", "@sentry", "@example", "@test")):
                bad_emails[em] += 1
            elif "@" not in em or "." not in em.split("@")[-1]:
                bad_emails[em] += 1
            elif len(em) > 80:
                bad_emails[em] += 1
    print(f"  Total bad/suspicious : {sum(bad_emails.values())}")
    for em, n in bad_emails.most_common(10):
        print(f"  {n:>3}× {em}")

    # B.4 Empty notes for rows that should be enriched
    print("\nB.4 — Rows missing all enrichment markers (email, phone, website)")
    cur.execute(
        "SELECT source_platform, COUNT(*) FROM attendance_signals "
        "WHERE source_platform IN "
        "  ('bdli-de', 'bdsv-de', 'gicat-fr', 'aiad-it', 'ads-group-uk') "
        "  AND notes NOT LIKE '%[email]%' "
        "  AND notes NOT LIKE '%[phone]%' "
        "  AND notes NOT LIKE '%[website]%' "
        "GROUP BY source_platform"
    )
    for r in cur.fetchall():
        print(f"  {r[0]:<35} : {r[1]} rows with no contact data at all")

    # ====================================================================
    # SECTION C — Duplicates
    # ====================================================================
    print("\n" + "=" * 70)
    print("SECTION C — Duplicates")
    print("=" * 70)

    # C.1 Email dups (within company)
    cur.execute(
        "SELECT id, canonical_company_name, notes "
        "FROM attendance_signals WHERE notes LIKE '%[email]%'"
    )
    em_groups = defaultdict(list)
    for r in cur.fetchall():
        for em in EMAIL_RX.findall(r["notes"] or ""):
            em = em.lower().strip().rstrip(".,;:")
            canon = (r["canonical_company_name"] or "").lower()
            em_groups[(em, canon)].append(r["id"])
    em_dups = sum(1 for v in em_groups.values() if len(v) > 1)
    em_dup_rows = sum(len(v) - 1 for v in em_groups.values() if len(v) > 1)
    print(f"\nC.1 Email-dup groups (within same company): {em_dups}")
    print(f"    Excess rows                              : {em_dup_rows}")

    # C.2 Person+company dups
    cur.execute(
        "SELECT id, person_name, canonical_company_name "
        "FROM attendance_signals WHERE person_name IS NOT NULL AND person_name != ''"
    )
    p_groups = defaultdict(list)
    for r in cur.fetchall():
        n = re.sub(r"\s+", " ", (r["person_name"] or "").lower().strip())
        n = re.sub(r"^(m\.|mme|mr|mrs|dr|prof)\.?\s+", "", n)
        canon = (r["canonical_company_name"] or "").lower()
        if n and canon and re.sub(r"[.\s]", "", n):
            p_groups[(n, canon)].append(r["id"])
    p_dups = sum(1 for v in p_groups.values() if len(v) > 1)
    print(f"C.2 Person+Company-dup groups              : {p_dups}")

    # C.3 Same canonical_company across multiple sources (should be one
    # company in our knowledge — useful for cross-association matching)
    cur.execute(
        "SELECT canonical_company_name, COUNT(DISTINCT source_platform) AS n_sources, "
        "       COUNT(*) AS n_rows "
        "FROM attendance_signals "
        "WHERE canonical_company_name IS NOT NULL AND canonical_company_name != '' "
        "GROUP BY canonical_company_name "
        "HAVING n_sources >= 2 "
        "ORDER BY n_sources DESC, n_rows DESC LIMIT 12"
    )
    print(f"\nC.3 Same company across 2+ sources (top 12):")
    for r in cur.fetchall():
        print(f"  {r['canonical_company_name'][:45]:<45} {r['n_sources']} sources, {r['n_rows']} rows")

    # C.4 Email reuse across companies (rare — usually means error)
    em_to_companies = defaultdict(set)
    cur.execute(
        "SELECT canonical_company_name, notes "
        "FROM attendance_signals WHERE notes LIKE '%[email]%'"
    )
    for r in cur.fetchall():
        for em in EMAIL_RX.findall(r["notes"] or ""):
            em = em.lower().strip().rstrip(".,;:")
            canon = r["canonical_company_name"] or ""
            if canon:
                em_to_companies[em].add(canon)
    cross = [(em, comps) for em, comps in em_to_companies.items() if len(comps) > 1]
    print(f"\nC.4 Emails shared across multiple companies : {len(cross)}")
    for em, comps in cross[:8]:
        print(f"  {em} → {len(comps)} companies (e.g. {list(comps)[:3]})")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
