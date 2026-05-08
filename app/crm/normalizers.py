"""Lookup tables that turn the rule-based intelligence English labels into
French CRM-friendly values requested by the sales team.

All maps are *closed enums*: the transformer always falls back to the
documented "À vérifier" / "Other" / etc. value when an input doesn't match.
"""
from __future__ import annotations

import re
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

# Strict business_model → company_type mapping. Generic raw values like
# "Manufacturer" or "Service company" are intentionally NOT in this dict
# because they don't disambiguate (a "Manufacturer" of bolts is Tier 3,
# not OEM). They fall through to the supply_chain_tier rule which is the
# accurate signal.
BUSINESS_MODEL_TO_COMPANY_TYPE: dict[str, str] = {
    "OEM": "OEM",
    "System integrator": "Intégrateur",
    "Equipment manufacturer": "Équipementier / Tier 1",
    "Sub-contractor": "Sous-traitant industriel",
    "Distributor / reseller": "Distributeur",
    "Distributor": "Distributeur",
    "Software / SaaS vendor": "Éditeur logiciel",
    "Software / SaaS": "Éditeur logiciel",
    "Engineering services": "Bureau d'ingénierie",
    "Engineering firm": "Bureau d'ingénierie",
    "Consulting firm": "Bureau d'ingénierie",
    "Research / lab": "Bureau d'ingénierie",
    "Materials / parts supplier": "Sous-traitant industriel",
}

# Closed taxonomy of 8 commercial company-type categories. This list MERGES
# the former ``supply_chain_tier`` (OEM / Tier 1-4 / MRO / N/A) with the
# old ``company_type`` axis : a single dimension that matters to a defense
# rep ("what kind of business is this ?").
ALLOWED_COMPANY_TYPES = [
    "OEM",
    "Intégrateur",
    "Équipementier / Tier 1",
    "Sous-traitant industriel",
    "Distributeur",
    "Éditeur logiciel",
    "Société de services",
    "Bureau d'ingénierie",
]

# Supply-chain tier → fallback company_type when ``business_model`` is
# missing or doesn't disambiguate.
TIER_TO_COMPANY_TYPE: dict[str, str] = {
    "OEM": "OEM",
    "Tier 1": "Équipementier / Tier 1",
    "Tier 2": "Sous-traitant industriel",
    "Tier 3": "Sous-traitant industriel",
    "Tier 4": "Sous-traitant industriel",
    "MRO": "Société de services",
}

# Product-category → company_type signal (used for N/A-tier fiches whose
# products_categories points to an institutional / service-only role).
PRODUCT_CAT_TO_COMPANY_TYPE: dict[str, str] = {
    "R&D académique & laboratoires": "Bureau d'ingénierie",
    "Conseil stratégique & due-diligence M&A": "Bureau d'ingénierie",
    "Représentation institutionnelle (cluster, fédération, chambre)": "Société de services",
    "Achat public défense & politique industrielle": "Société de services",
    "Médias & publications défense": "Société de services",
    "Organisation de salons & conférences défense": "Société de services",
    "Financement, banque & assurance défense": "Société de services",
    "Logistique militaire & transport": "Société de services",
    "Logistique export défense & transit": "Société de services",
    "Distribution composants & représentation de marques": "Distributeur",
    "Cybersécurité (logiciels & appliances)": "Éditeur logiciel",
    "Logiciels métier défense (autres)": "Éditeur logiciel",
    "Logiciels de simulation & cyber range": "Éditeur logiciel",
    "Plateformes IA / vision défense": "Éditeur logiciel",
}


# business_model values that are MORE specific than ``supply_chain_tier``
# and should override the tier-derived classification (e.g. an OEM-tier
# software vendor is really an "Éditeur logiciel", not an OEM).
SPECIFICITY_OVERRIDES: dict[str, str] = {
    "System integrator": "Intégrateur",
    "Software / SaaS vendor": "Éditeur logiciel",
    "Software / SaaS": "Éditeur logiciel",
    "Distributor / reseller": "Distributeur",
    "Distributor": "Distributeur",
    "Engineering services": "Bureau d'ingénierie",
    "Engineering firm": "Bureau d'ingénierie",
    "Consulting firm": "Bureau d'ingénierie",
    "Research / lab": "Bureau d'ingénierie",
}


def derive_company_type(
    business_model: Optional[str] = None,
    supply_chain_tier: Optional[str] = None,
    products_categories: Optional[list[str]] = None,
    activity_1liner: Optional[str] = None,
) -> str:
    """Smart-merge ``supply_chain_tier`` + ``business_model`` + product
    categories into one of the 8 ``ALLOWED_COMPANY_TYPES``.

    Order of resolution :
      1. **Specificity overrides** — when ``business_model`` carries a
         more precise signal than the tier (Software / SaaS, System
         integrator, Distributor, Engineering / Research), it wins
         outright.
      2. **Activity verb signal** — strong single-word verbs ("Édite",
         "Distribue", "Conseille") lock the type regardless of tier
         (catches LLM-classified fiches where business_model is stale).
      3. **Supply-chain tier** — the canonical pyramid (OEM / Tier 1-4 /
         MRO) wins for industrial fiches.
      4. **Product-category institutional signal** — only used for
         ``N/A``-tier fiches (clusters, banks, R&D labs).
      5. Default to ``"Société de services"``.
    """
    bm = (business_model or "").strip()
    tier = (supply_chain_tier or "").strip() or "N/A"
    act = (activity_1liner or "").strip()

    # Step 1 — specificity overrides (business_model trumps tier)
    if bm in SPECIFICITY_OVERRIDES:
        return SPECIFICITY_OVERRIDES[bm]

    # Step 2 — activity-verb specificity (LLM-grade signal)
    if act:
        if re.match(r"^[ÉE]dite\b", act, re.I):
            return "Éditeur logiciel"
        if re.match(r"^Distribue\b", act, re.I):
            return "Distributeur"
        if re.match(r"^Conseille\b", act, re.I) and tier == "N/A":
            return "Bureau d'ingénierie"

    # Step 3 — supply_chain_tier (canonical industrial axis)
    if tier in TIER_TO_COMPANY_TYPE:
        # OEM-tier business_model wins (strict OEM marker, not generic
        # "Manufacturer" which we removed from the mapping)
        if tier == "OEM" and bm == "OEM":
            return "OEM"
        if tier == "Equipment manufacturer":
            return "Équipementier / Tier 1"
        return TIER_TO_COMPANY_TYPE[tier]

    # Step 4 — N/A : look for institutional / sector signal in cats
    if products_categories:
        for cat in products_categories:
            c = (cat or "").strip()
            if c in PRODUCT_CAT_TO_COMPANY_TYPE:
                return PRODUCT_CAT_TO_COMPANY_TYPE[c]

    # Step 4b — N/A : business_model fallback for accepted values
    if bm in BUSINESS_MODEL_TO_COMPANY_TYPE:
        return BUSINESS_MODEL_TO_COMPANY_TYPE[bm]

    # Step 4c — N/A : activity verb hints
    if act:
        if re.match(
            r"^(Maintient|Op[èe]re|Loue|Forme|Anime|Pilote|Coordonne|"
            r"Mutualise|Promeut|F[ée]d[èe]re|Investit|Finance|Soutient|"
            r"Accompagne|Assure|Repr[ée]sente)\b",
            act, re.I,
        ):
            return "Société de services"

    return "Société de services"


def company_type_fr(business_model: Optional[str]) -> str:
    """Legacy wrapper kept for backwards compatibility — prefer
    :func:`derive_company_type` which considers tier + categories."""
    return derive_company_type(business_model=business_model)


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
