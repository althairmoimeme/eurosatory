"""Post-import cleanup of Lemlist-imported attendance signals.

The original import dropped any Lemlist email straight into the
``notes`` field. Two quality issues surfaced :

  1. Lemlist's ``Find Email`` is a heuristic guess (initials + domain).
     For medium / low confidence rows the address is essentially a
     guess — keeping it is unreliable.
  2. Initial-collision : ``mp@hpe.com`` is generated for every
     "first name M, last name P" employee of HPE → multiple people
     get the same email, none of them actually verified.

This script :
  • Builds a whitelist of emails that are **safe to keep** :
      - All emails from the verified ``Email`` column.
      - Emails from ``Find Email`` where ``Find Email - Confidence``
        is ``"high"`` AND the email is unique across BOTH CSVs.
  • For every ``buyer-prospection`` / ``sales-prospection`` signal
    whose ``notes`` carries an email NOT in the whitelist, strip the
    ``Direct email: …`` segment from notes (leaving phone & other
    bits untouched).
  • Also drops the lone ``("", "")`` empty signal that slipped in.

Run :
    python -m scripts.clean_lemlist_emails
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.database.models import AttendanceSignal  # noqa: E402

CSVS = [
    "/Users/bertantoine/Desktop/satory 1 .csv",
    "/Users/bertantoine/Desktop/satory 2.csv",
]


_email_rx = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_direct_email_clause_rx = re.compile(
    r"Direct email:\s*[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\.?",
    re.I,
)


def build_whitelist() -> set[str]:
    """Return a lowercase set of emails that are safe to keep."""
    keep: set[str] = set()
    all_find: Counter[str] = Counter()
    high_conf: list[str] = []
    direct: list[str] = []

    for csv_path in CSVS:
        df = pd.read_csv(csv_path, dtype=str, low_memory=False)
        # Verified direct emails (top priority — always safe)
        for v in df["Email"].dropna():
            v = v.strip().lower()
            if "@" in v:
                direct.append(v)
        # Find Email — only keep if confidence == high
        for _, row in df.iterrows():
            fe = row.get("Find Email")
            conf_raw = row.get("Find Email - Confidence")
            conf = str(conf_raw).strip().lower() if isinstance(conf_raw, str) else ""
            if isinstance(fe, str) and "@" in fe:
                fe = fe.strip().lower()
                all_find[fe] += 1
                if conf == "high":
                    high_conf.append(fe)

    keep.update(direct)
    # Add high-confidence Find Emails UNLESS they collide across people.
    # An email is "colliding" when it appears >1× in any ``Find Email``
    # column → not reliable.
    for em in high_conf:
        if all_find[em] == 1:
            keep.add(em)

    print(f"  Direct emails (Email column)           : {len(set(direct))}")
    print(f"  Find Email rows total                  : {sum(all_find.values())}")
    print(f"  Find Email confidence=high             : {len(set(high_conf))}")
    print(f"  Find Email with collisions (>=2 lines) : "
          f"{sum(1 for n in all_find.values() if n > 1)}")
    print(f"  Whitelist size                         : {len(keep)}")
    return keep


def main() -> int:
    print("Building safe-email whitelist from CSVs…")
    keep = build_whitelist()
    print()
    print("Scanning attendance_signals for cleanup…")

    s = SessionLocal()
    sigs = s.execute(
        # SQLAlchemy 2.x select
        __import__("sqlalchemy").select(AttendanceSignal).where(
            AttendanceSignal.source_platform.in_(
                ["buyer-prospection", "sales-prospection"]
            )
        )
    ).scalars().all()
    print(f"  Lemlist signals in DB : {len(sigs)}")

    n_email_stripped = 0
    n_empty_deleted = 0
    for sig in sigs:
        # Drop the (empty company, empty person) row
        if not (sig.company_name or "").strip() and not (sig.person_name or "").strip():
            s.delete(sig)
            n_empty_deleted += 1
            continue

        notes = sig.notes or ""
        m = _email_rx.search(notes)
        if not m:
            continue
        em = m.group(0).lower()
        if em in keep:
            continue
        # Strip the "Direct email: …" clause and tidy up trailing punctuation.
        new_notes = _direct_email_clause_rx.sub("", notes).strip()
        new_notes = re.sub(r"^\.\s*", "", new_notes)
        new_notes = re.sub(r"\.\s*\.", ".", new_notes)
        new_notes = new_notes.strip(". ")
        sig.notes = new_notes or None
        n_email_stripped += 1

    s.commit()
    s.close()
    print(f"  Emails stripped (unreliable / colliding) : {n_email_stripped}")
    print(f"  Empty rows deleted                       : {n_empty_deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
