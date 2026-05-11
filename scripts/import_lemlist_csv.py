"""Import Lemlist contact CSVs into ``attendance_signals``.

Each row in the CSV = one person attached to a company. We keep only the
fields commercially relevant to our defence intelligence DB :

  - person_name      = Firstname + Lastname
  - person_role      = Jobtitle
  - company_name     = Companyname
  - country          = parsed from Location / Companylocation
  - source_url       = Linkedinurl (always present)
  - notes            = "Direct email: X. Phone: Y." (so the existing
    regex extraction surfaces them as derived_email / derived_phone)

Lemlist-internal columns (Owner, Campaigns, CRMstatus, lead status,
dates…) are intentionally NOT imported.

Two contact lists are supported :
  • "Lead Buyer - Eurosatory Exhibitor"   → buyer_contact signal
  • "Sales - Eurosatory"                  → sales_prospect signal

Source platforms are kept confidential (``buyer-prospection`` and
``sales-prospection``) — the UI masks every non-LinkedIn source.

Run :
    python -m scripts.import_lemlist_csv \\
        "/path/to/satory 1 .csv" "/path/to/satory 2.csv"
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# ── DB bootstrap ───────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, init_db  # noqa: E402
from app.database.models import AttendanceSignal  # noqa: E402


# ── Country lookup (top 30 of the dataset) ─────────────────────────────────
_COUNTRY_ISO2: dict[str, str] = {
    "United States": "US", "USA": "US", "U.S.": "US",
    "United Kingdom": "GB", "UK": "GB", "England": "GB", "Scotland": "GB",
    "France": "FR", "Germany": "DE", "Italy": "IT", "Spain": "ES",
    "Portugal": "PT", "Netherlands": "NL", "Belgium": "BE", "Switzerland": "CH",
    "Sweden": "SE", "Norway": "NO", "Denmark": "DK", "Finland": "FI",
    "Iceland": "IS", "Poland": "PL", "Czech Republic": "CZ", "Czechia": "CZ",
    "Slovakia": "SK", "Hungary": "HU", "Romania": "RO", "Bulgaria": "BG",
    "Greece": "GR", "Turkey": "TR", "Türkiye": "TR", "Israel": "IL",
    "UAE": "AE", "United Arab Emirates": "AE", "Saudi Arabia": "SA",
    "Egypt": "EG", "Morocco": "MA", "Tunisia": "TN", "South Africa": "ZA",
    "India": "IN", "China": "CN", "Japan": "JP", "South Korea": "KR",
    "Korea": "KR", "Singapore": "SG", "Australia": "AU", "New Zealand": "NZ",
    "Canada": "CA", "Mexico": "MX", "Brazil": "BR", "Argentina": "AR",
    "Chile": "CL", "Colombia": "CO", "Peru": "PE", "Ukraine": "UA",
    "Russia": "RU", "Estonia": "EE", "Latvia": "LV", "Lithuania": "LT",
    "Ireland": "IE", "Luxembourg": "LU", "Austria": "AT", "Slovenia": "SI",
    "Croatia": "HR", "Serbia": "RS", "Cyprus": "CY", "Malta": "MT",
    "Bahrain": "BH", "Qatar": "QA", "Kuwait": "KW", "Oman": "OM",
    "Jordan": "JO", "Lebanon": "LB", "Indonesia": "ID", "Thailand": "TH",
    "Vietnam": "VN", "Malaysia": "MY", "Philippines": "PH",
    "Taiwan": "TW", "Pakistan": "PK", "Bangladesh": "BD",
}


# ── Helpers ────────────────────────────────────────────────────────────────
_alnum = re.compile(r"[^a-z0-9]+")


def _str(v) -> str:
    """Coerce pandas cell to clean str (handles NaN floats / None)."""
    if v is None:
        return ""
    if isinstance(v, float):
        return ""  # NaN
    return str(v).strip()


def _canonical(s: Optional[str]) -> str:
    """Lowercase, strip accents-light, alphanumeric only."""
    if not isinstance(s, str):
        return ""
    s = s.lower().strip()
    return _alnum.sub(" ", s).strip()


def _parse_country(loc) -> tuple[Optional[str], Optional[str]]:
    """Return ``(country_name, iso2)``. Country = last comma-separated piece."""
    loc = _str(loc)
    if not loc:
        return None, None
    pieces = [p.strip() for p in loc.split(",") if p.strip()]
    if not pieces:
        return None, None
    last = pieces[-1]
    return last, _COUNTRY_ISO2.get(last)


def _build_notes(row: pd.Series) -> str:
    """Build the ``notes`` payload so the existing email/phone regex
    extraction picks up derived_email / derived_phone in the UI.
    """
    bits: list[str] = []
    email = _str(row.get("Email")) or _str(row.get("Find Email"))
    if email and "@" in email:
        bits.append(f"Direct email: {email}")
    # Phone is sparse in this dataset but include it when present.
    for col in ("Phone", "Phone1", "Phone2"):
        v = _str(row.get(col))
        if v.startswith("+"):
            bits.append(f"Phone: {v}")
            break
    return ". ".join(bits)


def _dedupe_key(canonical_company: str, canonical_person: str, src: str) -> str:
    raw = f"{canonical_company}::{canonical_person}::{src}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def _classify_signal(contactlists: str) -> tuple[str, str]:
    """Return ``(source_platform, signal_type)`` from the contactlist column."""
    cl = (contactlists or "").lower()
    if "lead buyer" in cl or "exhibitor" in cl:
        return "buyer-prospection", "buyer_contact"
    return "sales-prospection", "sales_prospect"


# ── Main ───────────────────────────────────────────────────────────────────
def import_csv(path: Path) -> tuple[int, int, int]:
    df = pd.read_csv(path, dtype=str, low_memory=False)
    print(f"\n→ {path.name}  ({len(df)} lignes)")

    init_db()
    s = SessionLocal()

    # Existing dedupe keys to skip duplicates across re-runs.
    existing_keys: set[str] = set()
    for (k,) in s.execute(
        # SQLAlchemy 2.x text query
        __import__("sqlalchemy").text("SELECT dedupe_key FROM attendance_signals")
    ).all():
        if k:
            existing_keys.add(k)
    print(f"  Existing dedupe_keys in DB : {len(existing_keys)}")

    n_added = 0
    n_skipped_dup = 0
    n_skipped_empty = 0
    seen_in_this_run: set[str] = set()

    for _, row in df.iterrows():
        first = _str(row.get("Firstname"))
        last = _str(row.get("Lastname"))
        company = _str(row.get("Companyname"))
        if not company or (not first and not last):
            n_skipped_empty += 1
            continue
        person = f"{first} {last}".strip()
        canonical_company = _canonical(company)
        canonical_person = _canonical(person)

        platform, signal_type = _classify_signal(_str(row.get("Contactlists")))
        key = _dedupe_key(canonical_company, canonical_person, platform)
        if key in existing_keys or key in seen_in_this_run:
            n_skipped_dup += 1
            continue
        seen_in_this_run.add(key)

        # Country : prefer Location (the person's) over Companylocation.
        country, iso2 = _parse_country(row.get("Location"))
        if not country:
            country, iso2 = _parse_country(row.get("Companylocation"))

        # source_url must be non-NULL. LinkedIn URL is always present in
        # this dataset ; fall back to a placeholder if not.
        source_url = _str(row.get("Linkedinurl"))
        if not source_url:
            source_url = _str(row.get("Companywebsite"))
        if not source_url:
            source_url = f"lemlist://{platform}/{canonical_company}"

        sig = AttendanceSignal(
            edition_year=2026,
            entity_type="person",
            person_name=person or None,
            person_role=_str(row.get("Jobtitle")) or None,
            company_name=company,
            country=country,
            country_iso2=iso2,
            source_platform=platform,
            source_url=source_url[:899],
            source_title=None,
            signal_type=signal_type,
            signal_text=None,
            is_personal_post=False,
            is_company_post=False,
            canonical_company_name=canonical_company,
            canonical_person_name=canonical_person,
            dedupe_key=key,
            notes=_build_notes(row) or None,
            gdpr_risk_level="Medium",
            manual_validation_status="Pending",
            first_seen_at=datetime.utcnow(),
            last_checked_at=datetime.utcnow(),
        )
        s.add(sig)
        n_added += 1
        if n_added % 500 == 0:
            s.commit()
            print(f"    … committed {n_added}")
    s.commit()
    s.close()
    print(
        f"  ✓ Ajoutés : {n_added}  ·  dupliqués : {n_skipped_dup}  "
        f"·  vides : {n_skipped_empty}"
    )
    return n_added, n_skipped_dup, n_skipped_empty


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print("Usage : python -m scripts.import_lemlist_csv <csv1> [<csv2> …]")
        return 1
    for p in paths:
        if not p.exists():
            print(f"❌ Introuvable : {p}")
            return 2
    totals = [0, 0, 0]
    for p in paths:
        a, d, e = import_csv(p)
        totals[0] += a
        totals[1] += d
        totals[2] += e
    print(f"\n═══ TOTAL ═══")
    print(f"  Ajoutés    : {totals[0]}")
    print(f"  Dupliqués  : {totals[1]}")
    print(f"  Vides      : {totals[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
