"""Lookup tables that turn the rule-based intelligence English labels into
French CRM-friendly values requested by the sales team.

All maps are *closed enums*: the transformer always falls back to the
documented "À vérifier" / "Other" / etc. value when an input doesn't match.
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Country ISO2 → French country name
# ---------------------------------------------------------------------------

ISO2_TO_COUNTRY_FR: dict[str, str] = {
    "FR": "France",
    "DE": "Allemagne",
    "GB": "Royaume-Uni",
    "UK": "Royaume-Uni",
    "US": "États-Unis",
    "IT": "Italie",
    "ES": "Espagne",
    "TR": "Turquie",
    "SE": "Suède",
    "CZ": "République tchèque",
    "FI": "Finlande",
    "BE": "Belgique",
    "DK": "Danemark",
    "AT": "Autriche",
    "CN": "Chine",
    "HR": "Croatie",
    "PL": "Pologne",
    "NL": "Pays-Bas",
    "PT": "Portugal",
    "NO": "Norvège",
    "CH": "Suisse",
    "IL": "Israël",
    "JP": "Japon",
    "KR": "Corée du Sud",
    "IN": "Inde",
    "BR": "Brésil",
    "CA": "Canada",
    "AU": "Australie",
    "AE": "Émirats arabes unis",
    "SA": "Arabie saoudite",
    "EG": "Égypte",
    "ZA": "Afrique du Sud",
    "MX": "Mexique",
    "AR": "Argentine",
    "CL": "Chili",
    "CO": "Colombie",
    "RO": "Roumanie",
    "GR": "Grèce",
    "BG": "Bulgarie",
    "RS": "Serbie",
    "UA": "Ukraine",
    "LT": "Lituanie",
    "LV": "Lettonie",
    "EE": "Estonie",
    "SK": "Slovaquie",
    "SI": "Slovénie",
    "HU": "Hongrie",
    "IE": "Irlande",
    "LU": "Luxembourg",
    "MA": "Maroc",
    "TN": "Tunisie",
    "DZ": "Algérie",
    "JO": "Jordanie",
    "QA": "Qatar",
    "KW": "Koweït",
    "OM": "Oman",
    "PK": "Pakistan",
    "TH": "Thaïlande",
    "ID": "Indonésie",
    "VN": "Vietnam",
    "PH": "Philippines",
    "TW": "Taïwan",
    "MY": "Malaisie",
    "SG": "Singapour",
    "NZ": "Nouvelle-Zélande",
}


def country_fr(iso2: Optional[str], fallback_name: Optional[str] = None) -> Optional[str]:
    if not iso2:
        return fallback_name or None
    code = iso2.upper().strip()[:2]
    return ISO2_TO_COUNTRY_FR.get(code) or fallback_name or code


# ---------------------------------------------------------------------------
# Business model → French CRM company_type
# ---------------------------------------------------------------------------

BUSINESS_MODEL_TO_COMPANY_TYPE: dict[str, str] = {
    "OEM": "OEM",
    "System integrator": "Intégrateur",
    "Equipment manufacturer": "Équipementier",
    "Sub-contractor": "Sous-traitant industriel",
    "Distributor / reseller": "Distributeur",
    "Software / SaaS vendor": "Éditeur logiciel",
    "Engineering services": "Bureau d'ingénierie",
    "Consulting firm": "Société de services",
    "Materials / parts supplier": "Sous-traitant industriel",
}

ALLOWED_COMPANY_TYPES = [
    "OEM",
    "Intégrateur",
    "Équipementier",
    "Sous-traitant industriel",
    "Distributeur",
    "Éditeur logiciel",
    "Société de services",
    "Bureau d'ingénierie",
    "Institutionnel",
    "Autre",
    "À vérifier",
]


def company_type_fr(business_model: Optional[str]) -> str:
    if not business_model:
        return "À vérifier"
    if business_model in BUSINESS_MODEL_TO_COMPANY_TYPE:
        return BUSINESS_MODEL_TO_COMPANY_TYPE[business_model]
    return "Autre"


# ---------------------------------------------------------------------------
# Commercial target_type → French CRM target_type
# ---------------------------------------------------------------------------

TARGET_TYPE_FR: dict[str, str] = {
    "potential_buyer": "Acheteur potentiel",
    "industrial_partner": "Partenaire industriel",
    "integrator": "Intégrateur potentiel",
    "technology_integrator": "Intégrateur potentiel",
    "distributor": "Distributeur potentiel",
    "potential_supplier": "Fournisseur potentiel",
    "competitor": "Concurrent",
    "prime_contractor": "Donneur d'ordre",
    "subcontractor": "Sous-traitant",
    "to_qualify": "À vérifier",
}

ALLOWED_TARGET_TYPES_FR = list(dict.fromkeys(TARGET_TYPE_FR.values())) + ["Non prioritaire"]


def target_type_fr(target_type_en: Optional[str]) -> str:
    if not target_type_en:
        return "À vérifier"
    return TARGET_TYPE_FR.get(target_type_en, "À vérifier")


def target_types_to_french(target_types: list[str]) -> list[str]:
    out: list[str] = []
    for t in target_types or []:
        fr = target_type_fr(t)
        if fr not in out:
            out.append(fr)
    return out or ["À vérifier"]


# ---------------------------------------------------------------------------
# Internal status → CRM lead_status
# ---------------------------------------------------------------------------

STATUS_TO_LEAD_STATUS: dict[str, str] = {
    "new": "New",
    "qualified": "Qualified",
    "to_contact": "To contact",
    "contacted": "Contacted",
    "not_relevant": "Not relevant",
    "archived": "Archived",
}
ALLOWED_LEAD_STATUSES = [
    "New", "To qualify", "Qualified", "To contact", "Contacted",
    "Meeting requested", "Meeting booked", "Not relevant", "Archived",
]


def lead_status(internal_status: Optional[str]) -> str:
    if not internal_status:
        return "New"
    return STATUS_TO_LEAD_STATUS.get(internal_status, "New")


# ---------------------------------------------------------------------------
# Score → CRM stage / next_best_action / priority
# ---------------------------------------------------------------------------


def crm_stage(priority_level: Optional[str]) -> str:
    if priority_level in ("A+", "A"):
        return "Priority targeting"
    if priority_level == "B":
        return "Qualification"
    if priority_level == "C":
        return "Watchlist"
    return "Low priority"


def next_best_action(score: Optional[float]) -> str:
    if score is None:
        return "No immediate action"
    if score >= 90:
        return "Book meeting before Eurosatory"
    if score >= 75:
        return "Contact sales team and qualify need"
    if score >= 60:
        return "Review website and validate opportunity"
    if score >= 40:
        return "Keep in watchlist"
    return "No immediate action"


# ---------------------------------------------------------------------------
# Booth helper
# ---------------------------------------------------------------------------


def first_booth(stands: Optional[list]) -> Optional[str]:
    """Return ``Hall 5a / K221`` from the list of stand objects, or ``None``."""
    if not stands:
        return None
    for s in stands:
        if not isinstance(s, dict):
            continue
        hall = s.get("Hall") or ""
        name = s.get("Name") or s.get("MapPoint") or ""
        token = " / ".join(p for p in (hall, name) if p)
        if token:
            return token
    return None
