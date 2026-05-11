"""Merge the Lemlist-enriched email CSV back into ``attendance_signals``.

Takes the CSV exported from Lemlist (after running the Enrich.so
integration) and updates the ``notes`` field of the matching signals
with ``Direct email: <addr>`` — so the UI surfaces the email via the
existing ``derived_email`` regex extraction.

Trust policy
------------
We only accept :
  • ``Email`` field with ``Email - Status == "valid"`` (SMTP-verified).
  • ``Find Email`` with ``Find Email - Confidence == "high"`` AND
    no collision across rows (initials-based emails like
    ``mp@hpe.com`` that match multiple people get dropped).

For matching we canonicalize first+last name + company name with the
same NFKC / lowercase normalizer used during the original import.
``source_platform`` is fixed to ``sales-prospection`` since the CSV
comes from the satory 2 (Sales) contactlist.

Run :
    python -m scripts.merge_enriched_emails /Users/bertantoine/Desktop/enrich.csv
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.database.models import AttendanceSignal  # noqa: E402


_email_rx = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _canonical(s: str) -> str:
    """Same Unicode-aware canonicalizer used at import time."""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKC", s).lower().strip()
    return re.sub(r"\s+", " ", s)


def build_whitelist(df: pd.DataFrame) -> tuple[dict[tuple[str, str], str], dict]:
    """Return ``{(canon_person, canon_company): email}`` + stats."""
    df = df.fillna("")
    # 1. Count every email occurrence to detect cross-row collisions.
    all_counts: Counter[str] = Counter()
    for _, r in df.iterrows():
        for col in ("Email", "Find Email"):
            v = str(r[col]).strip().lower()
            if "@" in v:
                all_counts[v] += 1

    out: dict[tuple[str, str], str] = {}
    stats = {
        "direct_valid": 0, "find_high": 0,
        "collide": 0, "low_or_none": 0, "rows": 0,
    }
    for _, r in df.iterrows():
        stats["rows"] += 1
        person = f"{r['Firstname']} {r['Lastname']}".strip()
        company = str(r["Companyname"]).strip()
        if not person or not company:
            stats["low_or_none"] += 1
            continue
        key = (_canonical(person), _canonical(company))

        direct = str(r["Email"]).strip().lower()
        direct_status = str(r["Email - Status"]).strip().lower()
        fe = str(r["Find Email"]).strip().lower()
        fe_conf = str(r["Find Email - Confidence"]).strip().lower()

        if "@" in direct and direct_status == "valid":
            if all_counts[direct] == 1:
                out[key] = direct
                stats["direct_valid"] += 1
            else:
                stats["collide"] += 1
        elif "@" in fe and fe_conf == "high":
            if all_counts[fe] == 1:
                out[key] = fe
                stats["find_high"] += 1
            else:
                stats["collide"] += 1
        else:
            stats["low_or_none"] += 1
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument(
        "--source-platform",
        default="sales-prospection",
        help="Which attendance_signals.source_platform to scope the merge to.",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Compute the merge plan but don't write to DB.",
    )
    args = ap.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"❌ CSV introuvable : {csv_path}")
        return 1

    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    print(f"CSV : {csv_path.name}  →  {len(df)} lignes")

    whitelist, stats = build_whitelist(df)
    print(
        f"  ✓ direct-verified (valid + unique)  : {stats['direct_valid']}\n"
        f"  ✓ Find Email high + unique          : {stats['find_high']}\n"
        f"  ✗ skipped (collision multi-person)  : {stats['collide']}\n"
        f"  ✗ skipped (pas mail / low conf)     : {stats['low_or_none']}\n"
        f"  → emails whitelistés                : {len(whitelist)}"
    )

    # Apply
    s = SessionLocal()
    signals = s.execute(
        select(AttendanceSignal).where(
            AttendanceSignal.source_platform == args.source_platform
        )
    ).scalars().all()
    print(f"\nSignals existants ({args.source_platform}) : {len(signals)}")

    n_matched = 0
    n_new_email = 0
    n_already_had = 0
    n_unmatched = 0
    for sig in signals:
        key = (
            sig.canonical_person_name or "",
            sig.canonical_company_name or "",
        )
        em = whitelist.get(key)
        if not em:
            n_unmatched += 1
            continue
        n_matched += 1
        notes = sig.notes or ""
        existing = _email_rx.search(notes)
        if existing and existing.group(0).lower() == em:
            n_already_had += 1
            continue
        if existing:
            # already has a different email — skip to avoid overwriting
            # a previously-kept reliable address with a guessed one
            n_already_had += 1
            continue
        # Append the new email to notes
        new_notes = (notes.rstrip(". ") + ". " if notes else "") + \
            f"Direct email: {em}."
        sig.notes = new_notes.lstrip(". ")
        n_new_email += 1

    if args.dry_run:
        print("\n--- DRY RUN — no changes committed ---")
        s.rollback()
    else:
        s.commit()
    s.close()

    print(
        f"\nRésultat :\n"
        f"  Signals matchés par (person, company) : {n_matched}\n"
        f"  → Email AJOUTÉ aux notes               : {n_new_email}\n"
        f"  → Email déjà présent (skip)             : {n_already_had}\n"
        f"  Signals non matchés (pas dans CSV)     : {n_unmatched}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
