"""Merge manually-validated profiles on top of the rule-based output.

Reads
-----
- ``data/exports/targeting_profiles_all.json``        — rule-based first draft
- ``data/llm_test/profiles_20.json``                  — 20-sample calibration
- ``data/llm_test/profiles_manual_overrides.json``    — accumulator for batch 2b

Writes
------
- ``data/exports/targeting_profiles_final.json`` — merged result
- ``data/exports/targeting_profiles_final.csv``  — flat CSV
- ``data/exports/profiles_long_tail.json``       — worst N (default 60), sorted
                                                   by score asc, for the next
                                                   manual review batch.

Manual profiles override every field, including ``data_source_strength`` which
is set to ``manual``. ``completeness_score`` is recomputed from the schema
(headline presence comes from the DB).
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.processors.targeting_profile import (  # noqa: E402
    EurosatoryTargetingProfile, compute_completeness_score,
)

DB_PATH = ROOT / "data" / "eurosatory.db"
EXPORT_DIR = ROOT / "data" / "exports"
LLM_TEST_DIR = ROOT / "data" / "llm_test"


def _load_manual_overrides() -> dict[int, dict]:
    """Return ``{exhibitor_id: profile_dict}`` from all manual sources."""
    overrides: dict[int, dict] = {}

    p20 = LLM_TEST_DIR / "profiles_20.json"
    if p20.exists():
        data = json.loads(p20.read_text(encoding="utf-8"))
        for eid_str, row in data.get("profiles", {}).items():
            overrides[int(eid_str)] = row["profile"]

    extra = LLM_TEST_DIR / "profiles_manual_overrides.json"
    if extra.exists():
        data = json.loads(extra.read_text(encoding="utf-8"))
        # Extra accumulator format: {"<eid>": {profile fields}}
        for eid_str, row in data.items():
            overrides[int(eid_str)] = row

    return overrides


def _has_clean_headline(eid: int, cur) -> bool:
    cur.execute(
        "SELECT headline FROM exhibitor_intelligence WHERE exhibitor_id=?",
        (eid,),
    )
    r = cur.fetchone()
    if not r:
        return False
    h = (r[0] or "").strip()
    if len(h) < 25:
        return False
    bad = (
        "<html", "cookie", "javascript", "cloudflare", "just a moment",
        "404", "forbidden", "free shipping",
    )
    if any(b in h.lower() for b in bad):
        return False
    return True


def main() -> int:
    rb_path = EXPORT_DIR / "targeting_profiles_all.json"
    if not rb_path.exists():
        print(f"ERR: missing {rb_path} — run scripts/run_targeting_profiles.py first")
        return 1

    rule_records = json.loads(rb_path.read_text(encoding="utf-8"))
    overrides = _load_manual_overrides()
    print(f"Loaded {len(rule_records)} rule-based profiles + "
          f"{len(overrides)} manual overrides")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    final_records: list[dict] = []
    for rec in rule_records:
        eid = rec["exhibitor_id"]
        if eid in overrides:
            mp = overrides[eid]
            try:
                profile = EurosatoryTargetingProfile(**mp)
            except Exception as e:  # noqa: BLE001
                print(f"WARN: manual override for #{eid} failed schema: {e}")
                final_records.append(rec)
                continue
            score = compute_completeness_score(
                has_headline=_has_clean_headline(eid, cur),
                profile=profile,
            )
            final_records.append({
                **rec,
                "activity_1liner": profile.activity_1liner,
                "products": profile.products,
                "services": profile.services,
                "target_buyers": profile.target_buyers,
                "technologies": profile.technologies,
                "why_target": profile.why_target,
                "completeness_score": score,
                "data_source_strength": "manual",
            })
        else:
            final_records.append(rec)

    con.close()

    # ---- write final JSON + CSV ----
    out_json = EXPORT_DIR / "targeting_profiles_final.json"
    out_json.write_text(
        json.dumps(final_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {out_json}  ({out_json.stat().st_size:,} bytes)")

    out_csv = EXPORT_DIR / "targeting_profiles_final.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow([
            "exhibitor_id", "company_name", "country", "website", "pavilion",
            "activity_1liner", "products", "services", "target_buyers",
            "technologies", "why_target", "completeness_score",
            "data_source_strength",
        ])
        for r in final_records:
            w.writerow([
                r["exhibitor_id"], r["company_name"], r["country"] or "",
                r["website"] or "", r["pavilion"] or "",
                r["activity_1liner"],
                " · ".join(r["products"]),
                " · ".join(r["services"]),
                ", ".join(r["target_buyers"]),
                " · ".join(r["technologies"]),
                r["why_target"],
                r["completeness_score"],
                r["data_source_strength"],
            ])
    print(f"Wrote {out_csv}  ({out_csv.stat().st_size:,} bytes)")

    # ---- long-tail queue: worst 60 records that aren't manually overridden ----
    long_tail = [
        r for r in final_records
        if r["data_source_strength"] != "manual"
    ]
    long_tail.sort(key=lambda r: (r["completeness_score"], r["exhibitor_id"]))
    long_tail_top = long_tail[:60]
    out_lt = EXPORT_DIR / "profiles_long_tail.json"
    out_lt.write_text(
        json.dumps(long_tail_top, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {out_lt}  (worst 60 non-overridden)")

    # ---- final summary ----
    n = len(final_records)
    avg = sum(r["completeness_score"] for r in final_records) / n
    n100 = sum(1 for r in final_records if r["completeness_score"] == 100)
    n80p = sum(1 for r in final_records if r["completeness_score"] >= 80)
    n60p = sum(1 for r in final_records if r["completeness_score"] >= 60)
    n_low = sum(1 for r in final_records if r["completeness_score"] < 50)
    n_zero = sum(1 for r in final_records if r["completeness_score"] == 0)
    n_manual = sum(1 for r in final_records if r["data_source_strength"] == "manual")
    print()
    print("==== FINAL SUMMARY (after manual overrides) ====")
    print(f"  N total            : {n}")
    print(f"  N manuel           : {n_manual}")
    print(f"  Score moyen        : {avg:.1f}/100")
    print(f"  >= 80              : {n80p:>5}  ({100*n80p/n:.1f}%)")
    print(f"  >= 60              : {n60p:>5}  ({100*n60p/n:.1f}%)")
    print(f"  Score = 100        : {n100:>5}")
    print(f"  Score < 50         : {n_low:>5}  ({100*n_low/n:.1f}%)")
    print(f"  Score = 0          : {n_zero:>5}  ({100*n_zero/n:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
