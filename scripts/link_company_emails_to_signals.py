"""Link generic company emails (info@, sales@, contact@) onto attendance
signals — for free, no API call needed.

Why
---
The attendance_signals table holds 1603 person-level signals (people we
detected attending Eurosatory via OSINT). Of those, only 14 currently
have a nominative email — bulk email enrichment via FullEnrich /
RocketReach is paid and the keys are out of credit.

But the ``exhibitors`` and ``exhibitor_contacts`` tables already hold
~929 generic company emails (info@, sales@, contact@) ingested from
Finderr. Linking those to the signals — by matching the canonical
company name — gives every signal at least *some* contact path : the
company switchboard email, which a sales rep can reach out to to ask
for the named individual.

Persistence
-----------
We write the email to the ``AttendanceSignal.notes`` field (existing
free-text column already used for FullEnrich tracking). Signals that
already have an email pattern in notes are left untouched.

Run :
    .venv/bin/python scripts/link_company_emails_to_signals.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.attendance.dedup import canonical_company_name  # noqa: E402
from app.database import (  # noqa: E402
    AttendanceSignal, Exhibitor, ExhibitorContact, SessionLocal,
)


def main() -> int:
    s = SessionLocal()
    try:
        # 1. Build {canonical_company → {generic_email, nominative_email}}
        catalog: dict[str, dict[str, str]] = defaultdict(dict)
        for ex in s.execute(select(Exhibitor)).scalars().all():
            canon = canonical_company_name(ex.company_name or "")
            if not canon:
                continue
            entry = catalog[canon]
            if ex.contact_email and "@" in ex.contact_email and "contact" not in entry:
                entry["contact"] = ex.contact_email
            if ex.generic_sales_email and "@" in ex.generic_sales_email and "sales" not in entry:
                entry["sales"] = ex.generic_sales_email

        # Add per-contact rows (some are nominative, some generic)
        for c in s.execute(select(ExhibitorContact)).scalars().all():
            if not c.email or "@" not in c.email:
                continue
            ex = s.get(Exhibitor, c.exhibitor_id)
            if not ex:
                continue
            canon = canonical_company_name(ex.company_name or "")
            if not canon:
                continue
            entry = catalog[canon]
            if c.is_generic and "generic_contact" not in entry:
                entry["generic_contact"] = c.email
            elif (not c.is_generic) and "nominative" not in entry:
                entry["nominative"] = c.email

        print(f"Built catalog for {len(catalog)} companies")

        # 2. For each canonical signal that lacks an email in notes,
        # try to attach the best email we have.
        signals = s.execute(
            select(AttendanceSignal).where(
                AttendanceSignal.entity_type == "person",
                AttendanceSignal.person_name.is_not(None),
                AttendanceSignal.company_name.is_not(None),
            )
        ).scalars().all()

        n_linked = 0
        n_skipped_existing = 0
        n_no_match = 0
        for sig in signals:
            existing = sig.notes or ""
            if "@" in existing:
                # Already has an email recorded.
                n_skipped_existing += 1
                continue
            canon = canonical_company_name(sig.company_name or "")
            if not canon:
                n_no_match += 1
                continue
            entry = catalog.get(canon)
            if not entry:
                n_no_match += 1
                continue
            # Prefer nominative > generic_contact > contact > sales
            email = (
                entry.get("nominative") or entry.get("generic_contact")
                or entry.get("contact") or entry.get("sales")
            )
            if not email:
                n_no_match += 1
                continue
            kind = (
                "nominative" if email == entry.get("nominative")
                else "generic"
            )
            note_line = f"[email-{kind}] {email}"
            sig.notes = (
                f"{existing}\n{note_line}" if existing else note_line
            )
            n_linked += 1

        s.commit()
        print(f"Linked emails on {n_linked} signals")
        print(f"  Skipped (already had an email)    : {n_skipped_existing}")
        print(f"  No match (no canonical exhibitor) : {n_no_match}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
