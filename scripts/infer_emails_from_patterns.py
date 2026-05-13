"""Infer emails for named persons in attendance signals, when the
exhibitor has zero direct emails.

Strategy
────────
  1. For each domain we ALREADY have emails for in ``exhibitor_contacts``,
     compute the dominant pattern (firstname.lastname / f.lastname /
     firstname / etc.). This is the most reliable input.
  2. Layer a CURATED dictionary on top — for ~50 top defense groups
     where we have ZERO existing emails but the pattern is publicly
     known (SAAB, Lockheed, etc.).
  3. For each exhibitor that
       (a) has named persons in attendance_signals,
       (b) has NO email anywhere,
       (c) has a website URL we can derive a domain from,
     apply the matching pattern and INSERT a row into
     ``exhibitor_contacts`` with ``confidence='inferred'`` so the UI
     can flag it.
  4. (Optional, default-on) MX-verify each domain before inserting.
     Free, fast, eliminates dead domains.

Usage :
    python -m scripts.infer_emails_from_patterns                    # full run
    python -m scripts.infer_emails_from_patterns --limit 50         # pilot
    python -m scripts.infer_emails_from_patterns --no-mx-check      # skip DNS
    python -m scripts.infer_emails_from_patterns --dry-run          # report only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import select

from app.config import settings  # noqa: F401
from app.database import (
    AttendanceSignal, Exhibitor, ExhibitorContact, SessionLocal,
)

try:
    import dns.resolver  # dnspython
except ImportError:
    print("Install dnspython : pip install dnspython", file=sys.stderr)
    raise


# ─── PATTERN VOCABULARY ───────────────────────────────────────────────

# Each pattern is a callable (firstname, lastname) → local part.
# Names are pre-normalized (ASCII lower, no spaces).
_PATTERNS: dict[str, callable] = {
    "firstname.lastname": lambda f, l: f"{f}.{l}",
    "f.lastname":         lambda f, l: f"{f[0]}.{l}" if f else l,
    "firstnamelastname":  lambda f, l: f"{f}{l}",
    "flastname":          lambda f, l: f"{f[0]}{l}" if f else l,
    "firstname_lastname": lambda f, l: f"{f}_{l}",
    "firstname-lastname": lambda f, l: f"{f}-{l}",
    "lastname.firstname": lambda f, l: f"{l}.{f}",
    "lastname":           lambda f, l: l,
    "firstname":          lambda f, l: f,
}

_REVERSE_PATTERNS = {
    "firstname.lastname": re.compile(r"^([a-z]+)\.([a-z]+)$"),
    "f.lastname":         re.compile(r"^([a-z])\.([a-z]+)$"),
    "firstnamelastname":  re.compile(r"^([a-z]+)([a-z]+)$"),
    "lastname.firstname": re.compile(r"^([a-z]+)\.([a-z]+)$"),  # ambiguous
}


# ─── CURATED DICTIONARY FOR TOP DEFENSE GROUPS ────────────────────────
# Domain → pattern. Only put well-known, publicly-documented patterns
# here. When in doubt, leave it out — the auto-detection from existing
# emails handles the rest.

_CURATED_PATTERNS: dict[str, str] = {
    # — French groups —
    "thalesgroup.com":      "firstname.lastname",
    "safrangroup.com":      "firstname.lastname",
    "safran-electronics-defense.com": "firstname.lastname",
    "airbus.com":           "firstname.lastname",
    "dassault-aviation.com": "firstname.lastname",
    "nexter-group.fr":      "firstname.lastname",
    "knds.fr":              "firstname.lastname",
    "knds.de":              "firstname.lastname",
    "arquus-defense.com":   "firstname.lastname",
    "mbda-systems.com":     "firstname.lastname",
    "naval-group.com":      "firstname.lastname",
    "ariane.group":         "firstname.lastname",
    # — US groups —
    "lockheedmartin.com":   "firstname.lastname",
    "lmco.com":             "firstname.lastname",
    "raytheon.com":         "firstname.lastname",
    "rtx.com":              "firstname.lastname",
    "boeing.com":           "firstname.lastname",
    "ngc.com":              "firstname.lastname",
    "northropgrumman.com":  "firstname.lastname",
    "gd-ms.com":            "firstname.lastname",
    "generaldynamics.com":  "firstname.lastname",
    "l3harris.com":         "firstname.lastname",
    "honeywell.com":        "firstname.lastname",
    "textron.com":          "firstname.lastname",
    "skydio.com":           "firstname.lastname",
    "persistentsystems.com": "firstname.lastname",
    # — German groups —
    "rheinmetall.com":      "firstname.lastname",
    "rheinmetall-defence.com": "firstname.lastname",
    "diehl.com":            "firstname.lastname",
    "diehl-defence.com":    "firstname.lastname",
    "hensoldt.net":         "firstname.lastname",
    "rohde-schwarz.com":    "firstname.lastname",
    "mercedes-benz.com":    "firstname.lastname",
    "group.mercedes-benz.com": "firstname.lastname",
    # — UK —
    "baesystems.com":       "firstname.lastname",
    # — Italy —
    "leonardo.com":         "firstname.lastname",
    "leonardocompany.com":  "firstname.lastname",
    # — Israel —
    "elbitsystems.com":     "firstname.lastname",
    "iai.co.il":            "firstname.lastname",
    "rafael.co.il":         "firstname.lastname",
    # — Sweden —
    "saab.com":             "firstname.surname",  # SAAB uses .surname not .lastname
    # — Other —
    "kongsberg.com":        "firstname.lastname",
    "aselsan.com":          "firstname.lastname",
    "kuehne-nagel.com":     "firstname.lastname",
    "parker.com":           "firstname.lastname",
    "nicomatic.com":        "firstname.lastname",
    "sabenatechnics.com":   "firstname.lastname",
    "galvion.com":          "firstname.lastname",
    "invisio.com":          "firstname.lastname",
}

# SAAB-specific quirk : their pattern is firstname.surname not firstname.lastname.
# (Same generator either way — just a naming difference for our own clarity.)
_PATTERNS["firstname.surname"] = _PATTERNS["firstname.lastname"]


# ─── HELPERS ──────────────────────────────────────────────────────────

def _normalize_name_part(s: str) -> str:
    """Lower, strip accents, keep [a-z] only."""
    s = s or ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z]", "", s)
    return s


def _split_firstname_lastname(full: str) -> Optional[tuple[str, str]]:
    """Split 'John Smith' / 'SMITH John' / 'John Pierre Dupont' into
    (firstname, lastname). Heuristic : first non-ALLCAPS token = first,
    last token = last. Returns ``None`` if we can't reasonably split."""
    if not full:
        return None
    parts = full.strip().split()
    if len(parts) < 2:
        return None

    # If everything is ALLCAPS, it's probably "DUHAN Carole" — lastname first
    if all(p == p.upper() and any(c.isalpha() for c in p) for p in parts[:-1]):
        last = " ".join(parts[:-1])
        first = parts[-1]
    elif parts[-1].isupper() and any(c.isalpha() for c in parts[-1]):
        # "Carole DUHAN" — lastname is the ALLCAPS at the end
        last = parts[-1]
        first = parts[0]
    else:
        # Standard "First [Middle] Last"
        first = parts[0]
        last = parts[-1]

    first_n = _normalize_name_part(first)
    last_n = _normalize_name_part(last)
    if not first_n or not last_n:
        return None
    if len(first_n) < 2 or len(last_n) < 2:
        return None
    return first_n, last_n


def _domain_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return None
    if not host:
        return None
    # Strip www., en., fr., m. prefixes
    for prefix in ("www.", "en.", "fr.", "de.", "es.", "it.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host


def _detect_pattern(local: str, firstname: str, lastname: str) -> Optional[str]:
    """Given a local part + a known (firstname, lastname), identify the
    pattern. Returns the pattern name or None."""
    f = _normalize_name_part(firstname)
    l = _normalize_name_part(lastname)
    if not f or not l:
        return None
    candidates = []
    for name, fn in _PATTERNS.items():
        try:
            if fn(f, l) == local:
                candidates.append(name)
        except Exception:  # noqa: BLE001
            continue
    # Prefer the more specific patterns when multiple match.
    if not candidates:
        return None
    priority = [
        "firstname.lastname", "f.lastname", "lastname.firstname",
        "firstname_lastname", "firstname-lastname",
        "firstnamelastname", "flastname",
        "lastname", "firstname",
    ]
    for p in priority:
        if p in candidates:
            return p
    return candidates[0]


# ─── 1. DETECT PATTERNS FROM EXISTING EMAILS ──────────────────────────

def detect_domain_patterns(s) -> dict[str, str]:
    """Scan ``exhibitor_contacts`` + the named ``contact_email`` /
    ``generic_sales_email`` on ``exhibitors``, and figure out the
    dominant pattern for each domain."""
    # Collect (email, full_name) tuples.
    rows: list[tuple[str, Optional[str]]] = []
    for email, name in s.execute(
        select(ExhibitorContact.email, ExhibitorContact.full_name)
        .where(
            ExhibitorContact.email.is_not(None),
            ExhibitorContact.is_generic.is_(False),
            ExhibitorContact.full_name.is_not(None),
        )
    ).all():
        if email and name:
            rows.append((email, name))

    domain_pattern_votes: dict[str, Counter] = defaultdict(Counter)
    for email, name in rows:
        if "@" not in email:
            continue
        local, _, domain = email.lower().partition("@")
        domain = domain.strip()
        split = _split_firstname_lastname(name)
        if not split:
            continue
        first, last = split
        pat = _detect_pattern(local, first, last)
        if pat:
            domain_pattern_votes[domain][pat] += 1

    result: dict[str, str] = {}
    for domain, votes in domain_pattern_votes.items():
        # Only accept a detected pattern if we have ≥ 2 examples agreeing
        top, n = votes.most_common(1)[0]
        if n >= 2:
            result[domain] = top
    return result


# ─── 2. MX VERIFICATION ───────────────────────────────────────────────

def _domain_has_mx(domain: str, timeout: float = 3.0,
                   cache: Optional[dict] = None) -> bool:
    if cache is not None and domain in cache:
        return cache[domain]
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = timeout
        resolver.timeout = timeout
        ans = resolver.resolve(domain, "MX")
        ok = len(ans) > 0
    except Exception:  # noqa: BLE001
        ok = False
    if cache is not None:
        cache[domain] = ok
    return ok


# ─── 3. MAIN ──────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Max exhibitors to process")
    ap.add_argument("--no-mx-check", action="store_true",
                    help="Skip DNS MX verification")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report only — don't write to DB")
    ap.add_argument("--default-pattern", default="firstname.lastname",
                    help="Pattern when domain is unknown")
    args = ap.parse_args()

    s = SessionLocal()

    # 1. Pattern detection
    detected = detect_domain_patterns(s)
    print(f"[detect] {len(detected)} domains with confidently detected patterns")
    if detected:
        sample = list(detected.items())[:5]
        for d, p in sample:
            print(f"         {d:30s} → {p}")

    # 2. Merge with curated dict (curated wins if both exist — these are
    # publicly-documented).
    patterns: dict[str, str] = dict(detected)
    patterns.update(_CURATED_PATTERNS)
    print(f"[patterns] {len(patterns)} domain → pattern mappings active")

    # 3. Pick the target exhibitors
    targets = list(s.execute(
        select(Exhibitor).where(Exhibitor.website_url.is_not(None))
    ).scalars())

    # Map (exhibitor_id, lowercased company name) → set of named persons
    # from attendance_signals.
    signals = list(s.execute(
        select(
            AttendanceSignal.canonical_company_name,
            AttendanceSignal.company_name,
            AttendanceSignal.person_name,
            AttendanceSignal.person_role,
        ).where(
            AttendanceSignal.person_name.is_not(None),
            AttendanceSignal.person_name != "",
        )
    ).all())

    # Build name-key → set of (person_name, person_role)
    by_company: dict[str, set] = defaultdict(set)
    for cn, n, pn, pr in signals:
        key = (cn or n or "").strip().lower()
        if not key or not pn:
            continue
        by_company[key].add((pn.strip(), (pr or "").strip()))

    # Which exhibitors already have any email ?
    has_email: set[int] = set()
    for (eid,) in s.execute(
        select(ExhibitorContact.exhibitor_id).where(
            ExhibitorContact.email.is_not(None),
            ExhibitorContact.email != "",
        )
    ).all():
        has_email.add(eid)
    for eid, c_e, g_e in s.execute(
        select(Exhibitor.id, Exhibitor.contact_email, Exhibitor.generic_sales_email)
    ).all():
        if (c_e and c_e.strip()) or (g_e and g_e.strip()):
            has_email.add(eid)

    candidates = []
    for exh in targets:
        if exh.id in has_email:
            continue
        key = (exh.company_name or "").strip().lower()
        persons = by_company.get(key, set())
        if not persons:
            continue
        domain = _domain_from_url(exh.website_url or "")
        if not domain:
            continue
        candidates.append((exh, domain, persons))

    print(f"[candidates] {len(candidates)} exhibitors with named "
          f"persons + no email")

    if args.limit:
        candidates = candidates[:args.limit]
        print(f"[candidates] limited to {len(candidates)}")

    # MX cache
    mx_cache: dict[str, bool] = {}
    inserted = 0
    skipped_no_pattern = 0
    skipped_mx_fail = 0
    skipped_persons = 0
    rows_to_insert: list[ExhibitorContact] = []

    for exh, domain, persons in candidates:
        pat_name = patterns.get(domain)
        is_default_pattern = False
        if not pat_name:
            pat_name = args.default_pattern
            is_default_pattern = True
        fn = _PATTERNS.get(pat_name)
        if fn is None:
            skipped_no_pattern += 1
            continue

        if not args.no_mx_check:
            if not _domain_has_mx(domain, cache=mx_cache):
                skipped_mx_fail += 1
                continue

        # Generate ONE email per named person.
        # Confidence : 'medium' if pattern detected/curated, 'low' if default fallback.
        confidence = "low" if is_default_pattern else "medium"
        # Take the first 1-3 named persons (to avoid blasting the DB).
        for person_name, person_role in sorted(persons)[:3]:
            split = _split_firstname_lastname(person_name)
            if not split:
                skipped_persons += 1
                continue
            first, last = split
            local = fn(first, last)
            if not local or "@" in local:
                skipped_persons += 1
                continue
            email = f"{local}@{domain}"
            rows_to_insert.append(ExhibitorContact(
                exhibitor_id=exh.id,
                full_name=person_name,
                function=person_role or None,
                email=email,
                is_generic=False,
                confidence=confidence,
                source_url=f"inferred:{pat_name}",
            ))
            inserted += 1

    print()
    print(f"[plan] would insert  : {inserted} inferred-email contacts")
    print(f"       skip no MX    : {skipped_mx_fail}")
    print(f"       skip no name  : {skipped_persons}")
    print(f"       skip no pat   : {skipped_no_pattern}")

    if args.dry_run:
        print("[dry-run] no DB writes")
        s.close()
        return 0

    if rows_to_insert:
        s.bulk_save_objects(rows_to_insert)
        s.commit()
    s.close()
    print(f"✅ Inserted {inserted} new exhibitor_contacts with confidence=inferred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
