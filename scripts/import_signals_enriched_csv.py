"""Import a Lemlist-style enriched CSV of attendance signals.

Context
───────
  The morning's outreach test had a 20% bounce rate — too high. This
  import takes a NEW enriched CSV and aims to lower future bounce rates
  by being VERY strict on which emails make it into the DB.

Anti-bounce filters
───────────────────
  - Direct ``Email`` column : accepted (assumed verified by source).
  - ``Find Email`` column : ONLY accepted when ``Find Email - Confidence``
    is "high". Medium / low confidence rejected — they're the main
    bounce source.
  - ``Find Email - Catch All == true`` : rejected (catch-all domains
    happily accept any address, including invalid ones — major bounce
    risk in cold outreach).
  - Generic mailboxes (info@/contact@/sales@/support@/etc.) : rejected.
  - Junk locals (example, your.email, …) : rejected.

Matching strategy (in priority order)
─────────────────────────────────────
  1. **By email** : if the CSV email already exists in any signal's
     ``notes``, that signal gets ENRICHED with any missing fields
     (phone, LinkedIn, role).
  2. **By person + company** (normalized — ASCII lower, no spaces) :
     attaches the email/phone/LinkedIn to the existing signal.
  3. **No match** : INSERT a new signal IFF the row has at least one
     substantive piece of contact data (email or LinkedIn).

Updates applied (only when slot is empty in the existing signal)
────────────────────────────────────────────────────────────────
  - notes : "Direct email: …" → added if no email currently
  - notes : "Phone: …"        → added if no phone currently
  - source_url : ← LinkedIn    → updated if current source_url isn't a
                                 LinkedIn URL and the CSV row has one
  - person_role : ← Rôle       → updated if currently empty / generic

Usage
─────
    python -m scripts.import_signals_enriched_csv "/path/to/file.csv" --dry-run
    python -m scripts.import_signals_enriched_csv "/path/to/file.csv"
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, update

from app.config import settings  # noqa: F401
from app.database import AttendanceSignal, SessionLocal

try:
    import dns.resolver
    _HAS_DNS = True
except ImportError:
    _HAS_DNS = False


# ─── MX CHECK (cached per domain) ────────────────────────────────────

_MX_CACHE: dict[str, bool] = {}


def _domain_has_mx(domain: str, timeout: float = 3.0) -> bool:
    """Cached MX-record check. ``True`` when the domain has at least one
    MX record (= can receive mail). ``False`` when no MX (= 100 % bounce
    if you send to it)."""
    if not _HAS_DNS:
        return True  # gracefully skip when dnspython not installed
    if domain in _MX_CACHE:
        return _MX_CACHE[domain]
    try:
        r = dns.resolver.Resolver()
        r.lifetime = timeout
        r.timeout = timeout
        ans = r.resolve(domain, "MX")
        ok = len(ans) > 0
    except Exception:  # noqa: BLE001
        ok = False
    _MX_CACHE[domain] = ok
    return ok


# ─── FILTERS (same as previous email-import scripts) ─────────────────

_GENERIC_LOCALS = {
    "info", "infos", "contact", "contacts", "sales", "support",
    "hello", "hi", "office", "team", "service", "services",
    "marketing", "presse", "press", "media", "communication",
    "communications", "commercial", "admin", "administration",
    "kontakt", "secretariat", "rh", "hr", "jobs", "career",
    "careers", "recrutement", "recruiting", "legal", "compliance",
    "gdpr", "rgpd", "webmaster", "newsletter", "noreply", "no-reply",
    "donotreply", "feedback", "general", "enquiries", "inquiries",
    "vente", "ventes", "client", "clients", "boutique", "shop",
}
_GENERIC_PREFIXES = (
    "info-", "info.", "info_", "contact-", "contact.", "contact_",
    "sales-", "sales.", "sales_", "support-", "support.", "support_",
    "press-", "presse-", "kontakt-", "service-", "marketing-",
)
_JUNK_LOCALS = {
    "example", "your.email", "youremail", "user", "email", "name",
    "firstname", "firstname.lastname", "test", "tbd", "todo",
}

_EMAIL_RX = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)
_PHONE_RX = re.compile(r"\+\d[\d\s().\-]{8,}\d")


def _norm(s: str) -> str:
    if not s:
        return ""
    s = str(s).strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]", "", s).lower()
    return s


def _is_valid_named_email(email: str) -> tuple[bool, str]:
    if not email or "@" not in email:
        return False, "no_at"
    email = email.strip().lower()
    if len(email) > 80:
        return False, "too_long"
    local, _, dom = email.partition("@")
    if not local or not dom or "." not in dom:
        return False, "malformed"
    if local in _JUNK_LOCALS:
        return False, "junk"
    if local in _GENERIC_LOCALS:
        return False, "generic"
    if local.startswith(_GENERIC_PREFIXES):
        return False, "generic"
    return True, ""


def _pick_email(row: dict, mx_check: bool = True) -> tuple[str | None, str]:
    """Return (email, source_tag).

    Priority:
      1. ``Email`` column (assumed verified by source)
      2. ``Find Email`` IF confidence == "high" AND catch_all != "true"

    Anti-bounce filters applied :
      - format validation (no junk, no malformed)
      - generic mailbox rejection (info@/contact@/sales@/...)
      - MX-record check on the domain (skipped when dnspython missing or
        ``mx_check=False``)

    Returns (None, reason) when nothing qualifies.
    """
    direct = (row.get("Email") or "").strip().lower()
    if direct and "@" in direct:
        ok, reason = _is_valid_named_email(direct)
        if not ok:
            return None, f"direct_rejected:{reason}"
        if mx_check:
            dom = direct.split("@", 1)[1]
            if not _domain_has_mx(dom):
                return None, "direct_rejected:no_mx"
        return direct, "direct"

    find = (row.get("Find Email") or "").strip().lower()
    if not find or "@" not in find:
        return None, "no_email"
    conf = (row.get("Find Email - Confidence") or "").strip().lower()
    catch_all = (row.get("Find Email - Catch All") or "").strip().lower()
    if conf != "high":
        return None, f"low_confidence:{conf or 'empty'}"
    if catch_all == "true":
        return None, "catch_all"
    ok, reason = _is_valid_named_email(find)
    if not ok:
        return None, f"find_rejected:{reason}"
    if mx_check:
        dom = find.split("@", 1)[1]
        if not _domain_has_mx(dom):
            return None, "find_rejected:no_mx"
    return find, "find_high_confidence"


# ─── DB I/O ──────────────────────────────────────────────────────────

def build_indexes(s):
    """Build :
      - by_email : {normalized_email → [signal_id, …]} (from notes)
      - by_person_company : {(person_norm, company_norm) → [signal_id, …]}
      - signal_cache : {signal_id → dict of mutable fields for in-mem updates}
    """
    by_email: dict[str, list[int]] = {}
    by_person_company: dict[tuple, list[int]] = {}
    signal_cache: dict[int, dict] = {}

    rows = s.execute(select(
        AttendanceSignal.id,
        AttendanceSignal.person_name,
        AttendanceSignal.canonical_person_name,
        AttendanceSignal.company_name,
        AttendanceSignal.canonical_company_name,
        AttendanceSignal.person_role,
        AttendanceSignal.notes,
        AttendanceSignal.source_url,
    )).all()

    for row in rows:
        sid, pn, cpn, cn, ccn, role, notes, src = row
        person_n = _norm(cpn or pn or "")
        company_n = _norm(ccn or cn or "")
        signal_cache[sid] = {
            "person_name": pn,
            "canonical_person_name": cpn,
            "company_name": cn,
            "canonical_company_name": ccn,
            "person_role": role or "",
            "notes": notes or "",
            "source_url": src or "",
        }
        if person_n and company_n:
            by_person_company.setdefault((person_n, company_n), []).append(sid)
        # Email in notes
        m = _EMAIL_RX.search(notes or "")
        if m:
            by_email.setdefault(m.group(0).lower(), []).append(sid)
    return by_email, by_person_company, signal_cache


# ─── UPDATE HELPERS ──────────────────────────────────────────────────

def _has_email_in_notes(notes: str) -> bool:
    return bool(_EMAIL_RX.search(notes or ""))


def _has_phone_in_notes(notes: str) -> bool:
    return bool(_PHONE_RX.search(notes or ""))


def _is_linkedin_url(url: str) -> bool:
    return bool(url) and "linkedin.com/" in url.lower()


def _enriched_notes(current: str, email: str | None, phone: str | None,
                    role_from_csv: str | None) -> str:
    """Return notes augmented with email/phone if missing. Preserves
    existing content."""
    out = current or ""
    additions = []
    if email and not _has_email_in_notes(out):
        additions.append(f"Direct email: {email}.")
    if phone and not _has_phone_in_notes(out):
        # Normalize phone (drop double spaces, dots)
        phone_clean = re.sub(r"\s+", " ", phone).strip().rstrip(".")
        additions.append(f"Phone: {phone_clean}.")
    if additions:
        sep = " " if out and not out.endswith(("\n", " ")) else ""
        out = f"{out}{sep}{' '.join(additions)}"
    return out


# ─── MAIN ────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-insert", action="store_true",
                    help="Only enrich existing signals, don't INSERT new rows")
    ap.add_argument("--no-mx-check", action="store_true",
                    help="Skip MX record validation (default: ON)")
    args = ap.parse_args()
    mx_check = not args.no_mx_check
    if mx_check and not _HAS_DNS:
        print("⚠️  dnspython missing — install with: pip install dnspython", file=sys.stderr)
        print("    Falling back to NO MX check.", file=sys.stderr)
        mx_check = False
    elif mx_check:
        print("✅ MX record validation enabled (anti-bounce)")

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"❌ CSV not found : {csv_path}", file=sys.stderr)
        return 1

    s = SessionLocal()
    print("Building indexes from current DB...")
    by_email, by_pc, sig_cache = build_indexes(s)
    print(f"  signals indexed: {len(sig_cache)}")
    print(f"  emails in notes indexed: {len(by_email)}")
    print(f"  (person,company) keys: {len(by_pc)}")

    stats = Counter()
    updates_to_apply: dict[int, dict] = {}  # signal_id → fields to set
    new_signals_to_insert: list[dict] = []

    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for csv_row in reader:
            stats["csv_rows"] += 1

            # Pick the best valid email (or None)
            email, email_reason = _pick_email(csv_row, mx_check=mx_check)
            stats[f"email_{email_reason}"] += 1

            phone_raw = (csv_row.get("Téléphone") or "").strip()
            phone_match = _PHONE_RX.search(phone_raw) if phone_raw else None
            phone = phone_match.group(0).strip() if phone_match else None
            linkedin = (csv_row.get("LinkedIn") or "").strip()
            role_csv = (csv_row.get("Rôle") or "").strip()
            person_csv = (csv_row.get("Nom") or "").strip()
            company_csv = (csv_row.get("Société") or "").strip()
            country_csv = (csv_row.get("Pays") or "").strip()
            source_csv = (csv_row.get("Source") or "").strip()
            year_csv = (csv_row.get("Édition") or "").strip()

            # Match
            target_sid = None
            match_kind = ""

            if email and email in by_email:
                target_sid = by_email[email][0]  # first one
                match_kind = "email"
            else:
                pn_n = _norm(person_csv)
                cn_n = _norm(company_csv)
                if pn_n and cn_n and (pn_n, cn_n) in by_pc:
                    target_sid = by_pc[(pn_n, cn_n)][0]
                    match_kind = "person_company"

            if target_sid is not None:
                stats[f"matched_by_{match_kind}"] += 1
                # Build the update payload
                current = sig_cache[target_sid].copy()
                changes = {}
                # Email + phone go into notes
                new_notes = _enriched_notes(
                    current["notes"], email, phone, role_csv,
                )
                if new_notes != current["notes"]:
                    changes["notes"] = new_notes
                    sig_cache[target_sid]["notes"] = new_notes
                    if email and "Direct email:" in new_notes and "Direct email:" not in current["notes"]:
                        stats["enriched_email_added"] += 1
                    if phone and "Phone:" in new_notes and "Phone:" not in current["notes"]:
                        stats["enriched_phone_added"] += 1

                # LinkedIn — promote to source_url if signal has no LinkedIn
                if linkedin and "linkedin.com/" in linkedin.lower():
                    if not _is_linkedin_url(current["source_url"]):
                        changes["source_url"] = linkedin
                        sig_cache[target_sid]["source_url"] = linkedin
                        stats["enriched_linkedin_added"] += 1

                # Person role — update if currently empty or very generic
                if role_csv:
                    cur_role = (current["person_role"] or "").strip().lower()
                    if not cur_role or cur_role in ("staff", "employee", "—"):
                        changes["person_role"] = role_csv
                        sig_cache[target_sid]["person_role"] = role_csv
                        stats["enriched_role_added"] += 1

                if changes:
                    updates_to_apply[target_sid] = (
                        {**updates_to_apply.get(target_sid, {}), **changes}
                    )
                    stats["signals_with_at_least_one_update"] += 1 if target_sid not in updates_to_apply else 0
                else:
                    stats["matched_no_new_info"] += 1
            else:
                # No match → potentially INSERT
                if args.no_insert:
                    stats["no_match_skip_insert"] += 1
                    continue
                # Insert only if has substantive contact info
                if not email and not linkedin:
                    stats["no_match_insufficient_data"] += 1
                    continue
                stats["new_signal_to_insert"] += 1
                year = 2026
                try:
                    year = int(year_csv) if year_csv else 2026
                except ValueError:
                    year = 2026
                notes = _enriched_notes("", email, phone, role_csv)
                src_url = (
                    linkedin if linkedin and "linkedin.com/" in linkedin.lower()
                    else (source_csv or "csv-import-enriched")
                )
                new_signals_to_insert.append({
                    "edition_year": year,
                    "entity_type": "person",
                    "person_name": person_csv or None,
                    "person_role": role_csv or None,
                    "company_name": company_csv or None,
                    "country": country_csv or None,
                    "source_platform": "csv-import-enriched",
                    "source_url": src_url,
                    "source_title": None,
                    "source_snippet": None,
                    "signal_type": "csv_import",
                    "signal_text": None,
                    "is_company_post": False,
                    "is_personal_post": False,
                    "is_official_delegation": False,
                    "is_exhibitor_employee": False,
                    "is_duplicate": False,
                    "manual_validation_status": "Pending",
                    "notes": notes,
                })

    # Recompute "signals with updates" properly
    real_signal_updates = len(updates_to_apply)

    print()
    print("─── stats ───")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    print(f"  signals to UPDATE : {real_signal_updates}")
    print(f"  new signals to INSERT : {len(new_signals_to_insert)}")

    if args.dry_run:
        # Sample a few updates and a few inserts
        if updates_to_apply:
            print("\nSample of updates (first 3) :")
            for i, (sid, changes) in enumerate(list(updates_to_apply.items())[:3]):
                print(f"  signal #{sid}:")
                for k, v in changes.items():
                    val = str(v)[:120]
                    print(f"    {k} ← {val!r}")
                if i >= 2:
                    break
        if new_signals_to_insert:
            print("\nSample of inserts (first 3) :")
            for i, ins in enumerate(new_signals_to_insert[:3]):
                print(f"  {ins.get('person_name')} @ {ins.get('company_name')} ({ins.get('country')})")
                print(f"    notes: {ins.get('notes')[:120]}")
        print("\n[dry-run] no DB writes")
        s.close()
        return 0

    # APPLY UPDATES
    print("\nApplying updates...")
    for sid, fields in updates_to_apply.items():
        s.execute(
            update(AttendanceSignal).where(AttendanceSignal.id == sid).values(**fields)
        )
    s.commit()
    print(f"✅ Updated {len(updates_to_apply)} signals")

    # INSERT NEW
    if new_signals_to_insert:
        print(f"Inserting {len(new_signals_to_insert)} new signals...")
        s.bulk_insert_mappings(AttendanceSignal, new_signals_to_insert)
        s.commit()
        print(f"✅ Inserted {len(new_signals_to_insert)} new signals")

    s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
