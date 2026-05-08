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


# Activity-verb signals are the most reliable LLM-grade hint. The verb
# at the start of activity_1liner directly states the company's mode
# d'opération.
_VERB_EDITEUR = re.compile(r"^[ÉE]dite\b", re.I)
_VERB_DISTRIBUTEUR = re.compile(r"^Distribue\b", re.I)
_VERB_SOUS_TRAITANT = re.compile(r"^Sous[- ]traite\b", re.I)
_VERB_INTEGRATEUR = re.compile(r"^Int[èe]gre\b", re.I)
_VERB_INGENIERIE = re.compile(
    r"^(Conseille|Audite|Conduit\s+des?\s+recherches?|"
    r"Conduit\s+de\s+la\s+(?:R&D|recherche))\b",
    re.I,
)
_VERB_SERVICES = re.compile(
    r"^(Maintient|Op[èe]re|Loue|Forme|Anime|Pilote|Coordonne|"
    r"Mutualise|Promeut|F[ée]d[èe]re|Investit|Finance|Soutient|"
    r"Accompagne|Assure|Repr[ée]sente)\b",
    re.I,
)
_VERB_FABRICATION = re.compile(
    r"^(Con[çc]oit|Fabrique|Forge|Usine|Imprime|Assemble|Construit|"
    r"Soude|Produit|Transforme|D[ée]veloppe|R[ée]alise|D[ée]ploie)\b",
    re.I,
)
# "Fournit des services bancaires" / "Fournit de l'ingénierie" → service.
_VERB_FOURNIT_SERVICES = re.compile(
    r"^Fournit\s+(des?\s+)?(services?|de l[\'’]ingénierie|"
    r"du conseil|de la formation|de la maintenance|"
    r"de la R&D)",
    re.I,
)


def derive_company_type(
    business_model: Optional[str] = None,
    supply_chain_tier: Optional[str] = None,
    products_categories: Optional[list[str]] = None,
    activity_1liner: Optional[str] = None,
) -> str:
    """Smart-merge ``supply_chain_tier`` + ``business_model`` + product
    categories + activity verb into one of the 8 ``ALLOWED_COMPANY_TYPES``.

    Order of resolution (most-specific signal wins):
      1. **Activity verb** — "Édite" → Éditeur logiciel ; "Distribue" →
         Distributeur ; "Sous-traite" → Sous-traitant industriel. Always
         wins, because the activity is human-vetted (LLM-generated +
         manual review) and overrides stale rule-based business_model.
      2. **Activity verb softer signals** — "Conseille / Audite /
         Conduit des recherches" → Bureau d'ingénierie when the
         business_model agrees ; "Anime / Pilote / Représente / etc." →
         Société de services for institutional fiches.
      3. **business_model with strong specificity**, BUT only when the
         activity verb doesn't contradict (Distributor business_model
         doesn't override a "Conçoit et fabrique" activity).
      4. **N/A-tier institutional signal** from products_categories.
      5. **Supply_chain_tier** — the canonical pyramid (OEM / Tier 1-4 /
         MRO) for industrial fiches.
      6. **business_model fallback** for accepted values.
      7. Default to ``"Société de services"``.
    """
    # Defensive str cast — pandas may pass NaN floats for missing
    # cells, and ``nan or ""`` returns nan in Python.
    def _s(v) -> str:
        return v.strip() if isinstance(v, str) else ""

    bm = _s(business_model)
    tier = _s(supply_chain_tier) or "N/A"
    act = _s(activity_1liner)

    # Detect the action verb at the start of the activity_1liner.
    is_editeur = bool(act and _VERB_EDITEUR.match(act))
    is_distributeur = bool(act and _VERB_DISTRIBUTEUR.match(act))
    is_sous_traitant = bool(act and _VERB_SOUS_TRAITANT.match(act))
    is_integrateur = bool(act and _VERB_INTEGRATEUR.match(act))
    is_ingenierie = bool(act and _VERB_INGENIERIE.match(act))
    is_services_verb = bool(act and _VERB_SERVICES.match(act))
    is_fabrication = bool(act and _VERB_FABRICATION.match(act))
    is_fournit_services = bool(act and _VERB_FOURNIT_SERVICES.match(act))

    # ──────────────── Step 1 — Hard activity-verb signals ────────────────
    # The activity verb is the most reliable LLM-grade signal — it
    # always wins over stale business_model values from rule-based
    # scraping.
    if is_editeur:
        return "Éditeur logiciel"
    if is_distributeur:
        return "Distributeur"
    if is_sous_traitant:
        return "Sous-traitant industriel"
    if is_integrateur:
        return "Intégrateur"
    if is_ingenierie:
        return "Bureau d'ingénierie"

    # ──────────────── Step 2 — Soft activity-verb signals ───────────────
    # "Fournit des services" / "Fournit de l'ingénierie" → service.
    if is_fournit_services:
        return "Société de services"
    # Institutional / service verbs lock Société de services.
    if is_services_verb and not is_fabrication:
        return "Société de services"

    # ──────────────── Step 3 — business_model overrides ────────────────
    # Software / SaaS is unambiguous — always wins.
    if bm in {"Software / SaaS vendor", "Software / SaaS"}:
        return "Éditeur logiciel"

    # System integrator wins UNLESS activity verb explicitly contradicts.
    if bm == "System integrator" and not is_fabrication:
        return "Intégrateur"

    # Distributor wins UNLESS activity says "Conçoit / Fabrique / Déploie"
    # (some OEM-tier fiches have stale bm=Distributor / reseller).
    if bm in {"Distributor / reseller", "Distributor"} and not is_fabrication:
        return "Distributeur"

    # Engineering services / consulting / research wins ONLY when the
    # activity is genuinely engineering and NOT a manufacturer.
    if bm in {
        "Engineering services", "Engineering firm",
        "Consulting firm", "Research / lab",
    } and not is_fabrication:
        return "Bureau d'ingénierie"

    # ──────────────── Step 4 — N/A : institutional category ─────────────
    # Strong institutional signals (cluster, finance, R&D academic, etc.)
    # win even over fabrication activity. Soft signals (Logistique
    # militaire, Distribution composants…) only apply when the activity
    # is NOT a manufacturer verb.
    _INSTITUTIONAL_HARD = {
        "Représentation institutionnelle (cluster, fédération, chambre)",
        "Achat public défense & politique industrielle",
        "Médias & publications défense",
        "Organisation de salons & conférences défense",
        "Financement, banque & assurance défense",
        "R&D académique & laboratoires",
        "Conseil stratégique & due-diligence M&A",
    }
    if tier == "N/A" and products_categories:
        for cat in products_categories:
            c = (cat or "").strip()
            if c in _INSTITUTIONAL_HARD:
                return PRODUCT_CAT_TO_COMPANY_TYPE[c]
            if c in PRODUCT_CAT_TO_COMPANY_TYPE and not is_fabrication:
                return PRODUCT_CAT_TO_COMPANY_TYPE[c]

    # If tier=N/A but the fiche is clearly a manufacturer (verb "Fabrique"
    # / "Conçoit"…), classify as Sous-traitant industriel — better than
    # the default Société de services for industrial fiches without a
    # canonical product cat.
    if tier == "N/A" and is_fabrication:
        return "Sous-traitant industriel"

    # ──────────────── Step 5 — supply_chain_tier ─────────────────────
    if tier in TIER_TO_COMPANY_TYPE:
        return TIER_TO_COMPANY_TYPE[tier]

    # ──────────────── Step 6 — business_model fallback ─────────────────
    # Skip Distributor / Engineering fallback when the activity verb
    # clearly contradicts (already filtered above, but defensive).
    if bm in BUSINESS_MODEL_TO_COMPANY_TYPE:
        candidate = BUSINESS_MODEL_TO_COMPANY_TYPE[bm]
        # If the activity is fabrication, only accept industrial buckets.
        if is_fabrication and candidate in {
            "Distributeur", "Bureau d'ingénierie", "Société de services",
        }:
            return "Sous-traitant industriel"
        return candidate

    # ──────────────── Step 7 — default ─────────────────────────────────
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
