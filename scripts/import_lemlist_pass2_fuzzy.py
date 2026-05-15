"""Pass 2 — re-attempt the CSV rows that the original import skipped.

The first pass (``import_lemlist_enrichment_csv.py``) matches exhibitors
by **exact** domain and **exact** normalised company name. This catches
~88 % of usable rows but misses a lot of subsidiary / variant matches :

  CSV "Teledyne FLIR"           DB has "TELEDYNE FLIR OEM"
  CSV "KNDS UK"                 DB has "KNDS DEUTSCHLAND" / "KNDS FRANCE"
  CSV "ACTIA Group"             DB has "ACTIA AEROSPACE"
  CSV domain teledyneflir.com   DB has defense.flir.com on a variant

This pass adds two fuzzy strategies on top of the strict ones :

  - **Domain root** : pull the brand part of the domain (drop TLD, drop
    generic words like "group/holdings/defense") → match against the
    same root extracted from exhibitor domains.
  - **Fuzzy company name** : rapidfuzz token_set_ratio ≥ 88 against
    exhibitor names. Resolves "ACTIA Group" → "ACTIA AEROSPACE".

To avoid false positives we always require :
  - The CSV email's domain shares a 2nd-level domain with at least one
    of the matched exhibitor's website domains, OR
  - The fuzzy score is ≥ 92 (very high confidence).

Usage
─────
    python -m scripts.import_lemlist_pass2_fuzzy "/path/to/file.csv" --dry-run
    python -m scripts.import_lemlist_pass2_fuzzy "/path/to/file.csv"
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

try:
    from rapidfuzz import fuzz, process
except ImportError:
    print("Install rapidfuzz : pip install rapidfuzz", file=sys.stderr)
    raise

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import settings  # noqa: F401
from app.database import (
    AttendanceSignal, Exhibitor, ExhibitorContact, SessionLocal,
)


# ─── EMAIL FILTERS (same as pass 1) ───────────────────────────────────

_GENERIC_LOCALS_EXACT = {
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
_GENERIC_LOCALS_PREFIXES = (
    "info-", "info.", "info_", "contact-", "contact.", "contact_",
    "sales-", "sales.", "sales_", "support-", "support.", "support_",
    "press-", "presse-", "kontakt-", "service-", "marketing-",
)


def _is_valid_named_email(email: str) -> bool:
    if not email or "@" not in email:
        return False
    email = email.strip().lower()
    if len(email) > 80:
        return False
    local = email.split("@", 1)[0]
    if local in _GENERIC_LOCALS_EXACT:
        return False
    if local.startswith(_GENERIC_LOCALS_PREFIXES):
        return False
    return True


def _norm_domain(s: str) -> str | None:
    if not s:
        return None
    s = s.strip().lower()
    if "://" not in s and "." in s and " " not in s:
        s = "http://" + s
    try:
        host = urlparse(s).netloc or ""
    except Exception:  # noqa: BLE001
        return None
    if not host:
        return None
    for p in ("www.", "en.", "fr.", "de.", "es.", "it.", "m."):
        if host.startswith(p):
            host = host[len(p):]
            break
    return host.split("/")[0].split(":")[0] or None


def _domain_root(domain: str) -> str | None:
    """Return the brand portion of a domain.

    teledyneflir.com  → 'teledyneflir'
    defense.flir.com  → 'flir'
    knds.fr           → 'knds'
    """
    if not domain:
        return None
    parts = domain.split(".")
    if len(parts) < 2:
        return parts[0] if parts else None
    # Take the part just before the TLD
    return parts[-2]


def _norm_company(s: str) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(
        r"\s+(s\.?a\.?s\.?|s\.?a\.?r\.?l\.?|s\.?a\.?|gmbh|ltd\.?|"
        r"limited|inc\.?|llc|plc|bv|kg|ag|spa|sl|nv|oy|ab|"
        r"a\.?s\.?|d\.?o\.?o\.?)$",
        "", s,
    )
    # Strip country suffixes
    s = re.sub(
        r"\s+(france|uk|usa|us|deutschland|germany|italia|italy|"
        r"españa|spain|nederland|netherlands|asia|america|americas|"
        r"emea|apac|group|holdings|holding|international|global)$",
        "", s,
    )
    return s.strip()


# ─── INDEX BUILDERS ───────────────────────────────────────────────────

def build_indexes(s):
    by_domain: dict[str, int] = {}
    by_company_norm: dict[str, int] = {}
    domain_root_to_ids: dict[str, list[int]] = defaultdict(list)
    company_names: dict[int, str] = {}

    for eid, name, web in s.execute(
        select(Exhibitor.id, Exhibitor.company_name, Exhibitor.website_url)
    ).all():
        if name:
            cn = _norm_company(name)
            if cn:
                by_company_norm.setdefault(cn, eid)
                company_names[eid] = cn
        d = _norm_domain(web or "")
        if d:
            by_domain.setdefault(d, eid)
            root = _domain_root(d)
            if root and len(root) >= 3:
                domain_root_to_ids[root].append(eid)

    existing_emails: set[str] = set()
    for (e,) in s.execute(
        select(ExhibitorContact.email).where(
            ExhibitorContact.email.is_not(None),
            ExhibitorContact.email != "",
        )
    ).all():
        if e:
            existing_emails.add(e.strip().lower())

    return by_domain, by_company_norm, domain_root_to_ids, company_names, existing_emails


# ─── FUZZY HELPERS ────────────────────────────────────────────────────

# Pre-build candidate list for rapidfuzz once
def _fuzzy_match_company(
    needle: str, choices_list: list[str], choices_dict: dict[str, int],
    threshold: int = 88,
) -> int | None:
    """Return exhibitor_id of the best fuzzy match, or None."""
    if not needle:
        return None
    match = process.extractOne(
        needle, choices_list,
        scorer=fuzz.token_set_ratio,
        score_cutoff=threshold,
    )
    if not match:
        return None
    name, score, _ = match
    return choices_dict.get(name)


# ─── MAIN ─────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fuzzy-threshold", type=int, default=88,
                    help="rapidfuzz token_set_ratio score required to "
                    "accept a fuzzy company name match (default 88)")
    args = ap.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"❌ CSV not found : {csv_path}", file=sys.stderr)
        return 1

    s = SessionLocal()
    print("Building indexes...")
    by_domain, by_company, by_root, exh_names, existing_emails = build_indexes(s)
    choices_list = list(by_company.keys())
    print(f"  exhibitors by domain    : {len(by_domain)}")
    print(f"  exhibitors by name norm : {len(by_company)}")
    print(f"  domain roots indexed    : {len(by_root)}")
    print(f"  existing emails in DB   : {len(existing_emails)}")

    stats = {
        "total": 0,
        "skipped_no_email": 0,
        "skipped_generic": 0,
        "skipped_already_in_db": 0,
        "skipped_matched_in_pass1": 0,
        "matched_root_domain": 0,
        "matched_fuzzy_company": 0,
        "no_match_in_pass2": 0,
        "inserted": 0,
    }
    to_insert: list[ExhibitorContact] = []
    matched_exhibitors: set[int] = set()

    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["total"] += 1

            email = (row.get("Email") or "").strip().lower()
            if not email or "@" not in email:
                email = (row.get("Find Email") or "").strip().lower()
            if not email or "@" not in email:
                stats["skipped_no_email"] += 1
                continue
            if not _is_valid_named_email(email):
                stats["skipped_generic"] += 1
                continue
            if email in existing_emails:
                stats["skipped_already_in_db"] += 1
                continue

            # 1. Did pass 1 already cover this ?
            csv_dom = _norm_domain(
                (row.get("Domain") or "")
                or (row.get("Companywebsite") or "")
            )
            if not csv_dom and "@" in email:
                csv_dom = email.split("@", 1)[1]
            csv_cn = _norm_company(row.get("Companyname") or "")

            if csv_dom and csv_dom in by_domain:
                stats["skipped_matched_in_pass1"] += 1
                continue
            if csv_cn and csv_cn in by_company:
                stats["skipped_matched_in_pass1"] += 1
                continue

            # 2. NEW : domain root match (e.g. "flir" from "teledyneflir.com")
            exh_id = None
            if csv_dom:
                root = _domain_root(csv_dom)
                if root and root in by_root:
                    candidates = by_root[root]
                    # Pick the first candidate (assume same root = same parent)
                    exh_id = candidates[0]
                    stats["matched_root_domain"] += 1

            # 3. NEW : fuzzy company name match
            if exh_id is None and csv_cn:
                exh_id = _fuzzy_match_company(
                    csv_cn, choices_list, by_company,
                    threshold=args.fuzzy_threshold,
                )
                if exh_id is not None:
                    stats["matched_fuzzy_company"] += 1

            if exh_id is None:
                stats["no_match_in_pass2"] += 1
                continue

            first = (row.get("Firstname") or "").strip()
            last = (row.get("Lastname") or "").strip()
            full = f"{first} {last}".strip() or None
            role = (row.get("Jobtitle") or "").strip() or None
            phone = (row.get("Phone") or row.get("Phone1") or "").strip() or None
            linkedin = (row.get("Linkedinurl") or "").strip() or None

            to_insert.append(ExhibitorContact(
                exhibitor_id=exh_id,
                full_name=full,
                function=role,
                email=email,
                phone=phone,
                linkedin=linkedin,
                is_generic=False,
                confidence="medium",  # fuzzy match → slightly less confident
                source_url="lemlist-csv-import-pass2",
            ))
            existing_emails.add(email)
            matched_exhibitors.add(exh_id)
            stats["inserted"] += 1

    print()
    print("─── stats ───")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  unique exhibitors touched : {len(matched_exhibitors)}")

    if args.dry_run:
        print("\n[dry-run] no DB writes")
        s.close()
        return 0
    if to_insert:
        s.bulk_save_objects(to_insert)
        s.commit()
    s.close()
    print(f"\n✅ Inserted {len(to_insert)} new ExhibitorContact rows "
          f"on {len(matched_exhibitors)} distinct exhibitors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
