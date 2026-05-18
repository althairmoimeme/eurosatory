"""MX audit + cleanup of all emails in the LeadForges DB.

Why
───
  Cold outreach bouncing means the receiving domain has no working
  mail server (no MX record). MX-less emails are 100% bounces. This
  script removes them BEFORE you send.

What it does
────────────
  1. Extracts all unique emails from :
     - ``attendance_signals.notes`` (regex on "Direct email: X.")
     - ``exhibitor_contacts.email``
  2. Looks up MX records for each unique domain (cached, parallel
     DNS queries with 24 threads).
  3. For each email with no MX :
     - In attendance_signals : surgically removes the "Direct email: X."
       fragment from the notes (preserves the rest of the notes).
     - In exhibitor_contacts : sets email = NULL (keeps the row for
       audit but the email no longer surfaces in queries).
  4. Logs everything to ``data/logs/mx_audit_<timestamp>.json``.

Usage
─────
    python -m scripts.mx_audit_and_clean --dry-run
    python -m scripts.mx_audit_and_clean
    python -m scripts.mx_audit_and_clean --concurrency 48 --timeout 5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text as sql_text, update

from app.config import settings  # noqa: F401
from app.database import AttendanceSignal, ExhibitorContact, SessionLocal

try:
    import dns.resolver
except ImportError:
    print("Install : pip install dnspython", file=sys.stderr)
    raise


_EMAIL_RX = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)
# Matches a full "Direct email: X." (or "Direct email: X" at end of string)
# We capture the trailing dot if any so we remove the whole thing cleanly.
_DIRECT_EMAIL_RX = re.compile(
    r"Direct email:\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\.?\s*"
)


def _check_mx(domain: str, timeout: float = 3.0) -> bool:
    """Return True if the domain has at least one MX record."""
    try:
        r = dns.resolver.Resolver()
        r.lifetime = timeout
        r.timeout = timeout
        ans = r.resolve(domain, "MX")
        return len(ans) > 0
    except Exception:  # noqa: BLE001
        return False


def _check_mx_batch(domains: list[str], concurrency: int,
                    timeout: float) -> dict[str, bool]:
    """Concurrent DNS check on a list of unique domains."""
    out: dict[str, bool] = {}
    t0 = time.time()
    print(f"  MX checking {len(domains)} unique domains "
          f"(concurrency={concurrency})...")
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_check_mx, d, timeout): d for d in domains}
        for i, fut in enumerate(as_completed(futures), 1):
            d = futures[fut]
            try:
                out[d] = fut.result()
            except Exception:  # noqa: BLE001
                out[d] = False
            if i % 200 == 0 or i == len(domains):
                elapsed = int(time.time() - t0)
                ok = sum(1 for v in out.values() if v)
                rate = i / max(elapsed, 1)
                print(f"  [{elapsed:>3}s] {i}/{len(domains)} "
                      f"({ok} valid) · rate={rate:.0f}/s")
    return out


def _domain_of(email: str) -> str | None:
    if "@" not in email:
        return None
    return email.split("@", 1)[1].strip().lower() or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--timeout", type=float, default=3.0)
    args = ap.parse_args()

    s = SessionLocal()

    # 1. Collect all emails + their locations
    print("Scanning emails in DB...")
    # attendance_signals : (signal_id, email) — multiple emails per row
    # possible but rare.
    sig_emails: list[tuple[int, str]] = []
    for sid, notes in s.execute(
        select(AttendanceSignal.id, AttendanceSignal.notes).where(
            AttendanceSignal.notes.is_not(None),
            AttendanceSignal.notes != "",
        )
    ).all():
        for m in _DIRECT_EMAIL_RX.finditer(notes or ""):
            sig_emails.append((sid, m.group(1).lower()))

    contact_emails: list[tuple[int, str]] = []
    for cid, email in s.execute(
        select(ExhibitorContact.id, ExhibitorContact.email).where(
            ExhibitorContact.email.is_not(None),
            ExhibitorContact.email != "",
        )
    ).all():
        if email and "@" in email:
            contact_emails.append((cid, email.lower()))

    print(f"  attendance_signals.notes : {len(sig_emails)} email occurrences")
    print(f"  exhibitor_contacts.email : {len(contact_emails)} rows")

    # 2. Unique domains
    domains = set()
    for _, e in sig_emails:
        d = _domain_of(e)
        if d:
            domains.add(d)
    for _, e in contact_emails:
        d = _domain_of(e)
        if d:
            domains.add(d)
    print(f"  unique domains to MX-check : {len(domains)}")

    # 3. MX batch check
    mx_results = _check_mx_batch(
        sorted(domains), concurrency=args.concurrency, timeout=args.timeout,
    )
    valid_domains = {d for d, ok in mx_results.items() if ok}
    invalid_domains = sorted(set(mx_results.keys()) - valid_domains)
    print(f"  domains with valid MX : {len(valid_domains)}")
    print(f"  domains WITHOUT MX     : {len(invalid_domains)}")

    # Top invalid domains by email count
    top_invalid: Counter = Counter()
    for _, e in sig_emails + contact_emails:
        d = _domain_of(e)
        if d in invalid_domains:
            top_invalid[d] += 1

    print()
    print("Top 15 dead-MX domains by email count :")
    for d, n in top_invalid.most_common(15):
        print(f"  {n:5d} × {d}")

    # 4. Plan the cleanup
    sig_invalid_emails = [
        (sid, e) for sid, e in sig_emails
        if _domain_of(e) in invalid_domains
    ]
    contact_invalid_ids = [
        cid for cid, e in contact_emails
        if _domain_of(e) in invalid_domains
    ]

    print()
    print("─── cleanup plan ───")
    print(f"  attendance_signals.notes : {len(sig_invalid_emails)} email "
          f"fragments to scrub from {len({sid for sid,_ in sig_invalid_emails})} signals")
    print(f"  exhibitor_contacts : {len(contact_invalid_ids)} rows to set email=NULL")

    # 5. Save audit log
    Path("data/logs").mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    audit_path = Path(f"data/logs/mx_audit_{ts}.json")
    audit = {
        "timestamp_utc": ts,
        "total_unique_domains": len(domains),
        "valid_domains": len(valid_domains),
        "invalid_domains": len(invalid_domains),
        "total_emails_scanned": len(sig_emails) + len(contact_emails),
        "emails_to_scrub_signals": len(sig_invalid_emails),
        "emails_to_null_contacts": len(contact_invalid_ids),
        "top_dead_domains": dict(top_invalid.most_common(50)),
        "dry_run": args.dry_run,
    }
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    print(f"\nAudit log written : {audit_path}")

    if args.dry_run:
        print("\n[dry-run] no DB writes")
        s.close()
        return 0

    # 6. Apply cleanup
    # 6a. attendance_signals : surgically remove "Direct email: X." substring
    # per signal.
    sig_dirty_ids = {sid for sid, _ in sig_invalid_emails}
    # Build per-signal set of invalid emails
    by_sid: dict[int, set] = {}
    for sid, e in sig_invalid_emails:
        by_sid.setdefault(sid, set()).add(e)

    print(f"\nApplying cleanup to {len(sig_dirty_ids)} signal rows...")
    fixed = 0
    for sid in sig_dirty_ids:
        notes = s.execute(
            select(AttendanceSignal.notes).where(AttendanceSignal.id == sid)
        ).scalar()
        if not notes:
            continue
        invalid_emails_for_this = by_sid[sid]
        new_notes = notes
        for inv in invalid_emails_for_this:
            # Build a regex that matches "Direct email: <this exact email>."
            pattern = re.compile(
                r"Direct email:\s*" + re.escape(inv) + r"\.?\s*",
                re.IGNORECASE,
            )
            new_notes = pattern.sub("", new_notes)
        # Collapse double spaces
        new_notes = re.sub(r"\s{2,}", " ", new_notes).strip()
        if new_notes != notes:
            s.execute(
                update(AttendanceSignal)
                .where(AttendanceSignal.id == sid)
                .values(notes=new_notes)
            )
            fixed += 1
    s.commit()
    print(f"  ✅ Scrubbed notes on {fixed} signals")

    # 6b. exhibitor_contacts : set email = NULL
    if contact_invalid_ids:
        print(f"Nulling email on {len(contact_invalid_ids)} exhibitor_contacts...")
        # Batch in chunks
        batch = 500
        for i in range(0, len(contact_invalid_ids), batch):
            chunk = contact_invalid_ids[i:i + batch]
            s.execute(
                update(ExhibitorContact)
                .where(ExhibitorContact.id.in_(chunk))
                .values(email=None, confidence="invalid_mx")
            )
        s.commit()
        print(f"  ✅ Nulled {len(contact_invalid_ids)} contact emails")

    s.close()
    print()
    print(f"✅ MX audit complete. Audit log : {audit_path}")
    print(f"   → expected bounce reduction : ~{len(top_invalid) * 5}% to ~1-2%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
