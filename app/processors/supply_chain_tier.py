"""Map a fiche's product categories to a defense supply-chain tier.

The model
---------
In defense industry, companies sit at one of these levels of the
integration chain:

    OEM      → vend le système final à l'utilisateur (MoD, armée)
                 ex: blindé, missile, drone, navire, casque combat
    MRO      → maintient / répare / remet à niveau les systèmes
                 ex: Sabena Technics, Lufthansa Technik, AAR
    Tier 1   → vend un grand sous-système à l'OEM
                 ex: tourelle, optronique, moteur, C4ISR
    Tier 2   → vend des composants au Tier 1
                 ex: capteurs, cartes électroniques, connecteurs
    Tier 3   → vend des pièces ou opérations au Tier 2
                 ex: usinage, traitement de surface, visserie
    Tier 4   → vend la matière ou la brique amont
                 ex: acier balistique, composites, batteries

The product-derived tier comes from a closed mapping on
PRODUCT_CATEGORIES (taxonomy_normalize.py).  ``MRO`` is detected
**orthogonally** from services + activity verb — when the primary role
is maintenance / overhaul, ``MRO`` wins over the product-derived tier.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Tier ladder — order matters (highest first)
# ---------------------------------------------------------------------------

TIER_ORDER: tuple[str, ...] = ("OEM", "MRO", "Tier 1", "Tier 2", "Tier 3", "Tier 4")


# ---------------------------------------------------------------------------
# Category → Tier mapping
# ---------------------------------------------------------------------------
# NB: every label below MUST match a value present in
# ``app.processors.taxonomy_normalize.PRODUCT_CATEGORIES`` exactly.

OEM: set[str] = {
    # Plateformes terrestres
    "Véhicules blindés",
    "Véhicules tactiques (non blindés)",
    "Camions militaires & logistique",
    "Engins du génie militaire",
    "Ambulances & véhicules sanitaires",
    "Robots terrestres (UGV / EOD)",
    # Plateformes aériennes & espace
    "Avions & hélicoptères militaires",
    "Drones aériens (UAV)",
    "Drones FPV & munitions rôdeuses",
    "Satellites & CubeSats",
    # Naval
    "Navires & sous-systèmes navals",
    # Armement final
    "Armes légères & accessoires",
    "Armes lourdes & systèmes de tir",
    "Armes non-létales",
    "Missiles & armements guidés",
    "Roquettes & lance-roquettes",
    "Munitions petit/moyen calibre",
    "Munitions gros calibre & obus",
    "Pyrotechnie & artifices",
    # Systèmes finaux livrés au MoD / armées
    "Systèmes anti-drone (C-UAS)",
    "Simulateurs d'entraînement & VR/AR",
    # Soldat connecté — équipement fini livré à l'armée
    "Équipement du fantassin (général)",
    "Casques de combat",
    "Gilets & plaques pare-balles",
    "Vision nocturne & jumelles",
    "Optiques d'armes (viseurs)",
    "NRBC (masques, tenues, détecteurs)",
    # Vitrage blindé final pour véhicule / bâtiment
    "Vitrages blindés",
}


TIER_1: set[str] = {
    # Sous-systèmes critiques
    "Tourelles téléopérées (RWS)",
    "Optronique & viseurs (EO/IR)",
    "Radars & traitement signal",
    "Sonars & systèmes ASM",
    "Systèmes de guerre électronique (EW)",
    "Systèmes IFF & identification",
    # Communications & C4ISR
    "Radios tactiques & SDR",
    "Systèmes C2 & C4ISR",
    "Streaming vidéo & data tactiques",
    "Réseaux militaires (5G/P-LTE/MANET)",
    # Cyber & logiciels métier
    "Plateformes IA / vision défense",
    "Cybersécurité (logiciels & appliances)",
    "Logiciels de simulation & cyber range",
    "Logiciels métier défense (autres)",
    # Propulsion & énergie tactique
    "Moteurs diesel / propulsion lourde",
    "Moteurs aéronautiques",
    "Groupes électrogènes & énergie tactique",
    # Avionique
    "Avionique & instruments embarqués",
    # Capteurs « système »
    "Capteurs sismiques & acoustiques",
    # Stations infra
    "Stations de purification d'eau & infra",
}


TIER_2: set[str] = {
    "Composants électroniques & cartes",
    "Connectique & câblage durcis",
    "Capteurs embarqués (généraux)",
    "Détecteurs IR refroidis & non-refroidis",
    "Antennes & infrastructures RF",
    "Alimentations & convertisseurs durcis",
    "Boîtiers & châssis durcis",
    "Servomoteurs & motion control précision",
    "Terminaux & antennes spatiales",
}


TIER_3: set[str] = {
    "Pièces mécaniques & usinage",
    "Mécanique de transmission",
    "Hydraulique & motion control",
    "Fixations & visserie aéronautique",
    "Conteneurs, malles, shelters",
    "Mâts, signalisation & balisage",
    "Sièges & ergonomie cabine",
    "Caméras tactiques & POV",
    "Lubrifiants & fluides",
    "Textiles techniques tactiques",
    "Explosifs & matériaux énergétiques",
}


TIER_4: set[str] = {
    "Matériaux composites & blindage",
    "Aciers & métallurgie spéciale",
    "Céramiques techniques & balistiques",
    "Batteries & packs énergétiques",
    "Piles à combustible & H2",
}


# Categories that are neither industrial products nor sub-products —
# they belong to a different axis (services / institutional / catch-all).
NON_INDUSTRIAL: set[str] = {
    "Logistique militaire & transport",
    "Représentation institutionnelle (cluster, fédération, chambre)",
    "Achat public défense & politique industrielle",
    "Médias & publications défense",
    "Organisation de salons & conférences défense",
    "Financement, banque & assurance défense",
    "R&D académique & laboratoires",
    "Conseil stratégique & due-diligence M&A",
    "Logistique export défense & transit",
    "Distribution composants & représentation de marques",
    "Autre — à qualifier",
}


# Aggregate {category → tier}
_CATEGORY_TO_TIER: dict[str, str] = {}
for cat in OEM:
    _CATEGORY_TO_TIER[cat] = "OEM"
for cat in TIER_1:
    _CATEGORY_TO_TIER[cat] = "Tier 1"
for cat in TIER_2:
    _CATEGORY_TO_TIER[cat] = "Tier 2"
for cat in TIER_3:
    _CATEGORY_TO_TIER[cat] = "Tier 3"
for cat in TIER_4:
    _CATEGORY_TO_TIER[cat] = "Tier 4"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# MRO detection
# ---------------------------------------------------------------------------
# A company is classified MRO when its **primary role** is maintenance,
# repair or overhaul of defense systems — even if it ALSO touches a
# "product" category (e.g. an MRO shop for fighter jets has "Avions
# militaires" as a product tag, but they don't manufacture jets).

# Service categories that are pure MRO (in SERVICE_CATEGORIES taxonomy)
MRO_SERVICE_CATS: set[str] = {
    "MCO / MRO",
    "Modernisation de flottes",
    "Support opérationnel & OPEX",
}

# Activity-line first-verb / keywords that signal MRO as the primary role
_MRO_VERB_RX = re.compile(
    r"^\s*(maintient|maintenance|maintenance et soutien|"
    r"assure le mco|assure la maintenance|assure le mro|"
    r"assure le maintien en condition|effectue la maintenance|"
    r"r[eé]pare|r[eé]nove|remet \s*[aà]\s+niveau|"
    r"r[eé]parez?|maintains|services|repairs?|overhauls?|sustains?|"
    r"perform[a-z]* maintenance|provides? mro)\b",
    re.I | re.U,
)
_MRO_KEYWORD_RX = re.compile(
    r"\b(MCO|MRO|"
    r"maintenance en condition op[eé]rationnelle|"
    r"maintenance, r[eé]paration et r[eé]vision|"
    r"maintenance repair (?:and|&) overhaul|"
    r"fleet support|aftermarket support|sustainment|"
    r"depot[- ]level maintenance|d[eé]potage|"
    r"r[eé]vision g[eé]n[eé]rale|grande visite|"
    r"remise [aà] niveau|mid[- ]life upgrade|MLU)\b",
    re.I | re.U,
)


# Hard list of well-known MRO providers — companies whose primary role
# is maintenance/repair/overhaul but whose rule-based activity_1liner
# might mistakenly say "designs and manufactures".  Match is on the
# canonical (lowercased + stripped legal suffix) form.
MRO_NAME_OVERRIDES: set[str] = {
    "sabena technics",
    "lufthansa technik",
    "aar",
    "aar corp",
    "standardaero",
    "ams (aircraft maintenance services)",
    "magnetic mro",
    "atitech",
    "iberia maintenance",
    "fl technics",
    "joramco",
    "ehsg",
    "elbe flugzeugwerke",
    "munich aerospace mro",
    "aero dienst",
    "aero norway",
    "aerofit",
    "aerotech peissenberg",
    "ramco aviation",
    "leonardo helicopters mro",
    "ndt expert",
    "snef",
    "rolls-royce defence services",
    "ge aerospace defense services",
    "safran helicopter engines mro",
}


def _is_mro(
    services_categories: Optional[Iterable[str]],
    activity_text: Optional[str],
    products_categories: Optional[Iterable[str]] = None,
    canonical_name: Optional[str] = None,
) -> bool:
    """Return True when the fiche's primary role is maintenance/repair.

    Strict logic to avoid tagging OEMs that simply have an aftermarket /
    MRO division as MRO companies :
      1. ``activity_1liner`` starts with a maintenance verb → MRO.
      2. ``activity_1liner`` contains an explicit MCO / MRO keyword → MRO.
      3. Hand-curated override list ``MRO_NAME_OVERRIDES`` matches the
         canonical name → MRO (catches misclassified rule-based fiches
         like Sabena Technics).
      4. Services include MCO/MRO **AND** the company has no clearly
         manufactured OEM-grade product in its products_categories →
         MRO (services-only MRO shop).
    """
    # 1. Activity-verb signal — strongest
    if activity_text and _MRO_VERB_RX.match(activity_text or ""):
        return True
    # 2. Activity-keyword signal
    if activity_text and _MRO_KEYWORD_RX.search(activity_text or ""):
        return True
    # 3. Hand-curated MRO list
    if canonical_name and canonical_name.lower().strip() in MRO_NAME_OVERRIDES:
        return True
    # 4. Service-only MRO shop : has MCO/MRO service AND no real industrial
    #    product. We compare against NON_INDUSTRIAL — if every product
    #    category is institutional / catch-all / "Autre", the company is
    #    pure-services and MCO/MRO becomes the dominant signal.
    if services_categories:
        has_mro_svc = any(
            (s or "").strip() in MRO_SERVICE_CATS for s in services_categories
        )
        if has_mro_svc:
            real_products = [
                (cat or "").strip() for cat in (products_categories or [])
                if (cat or "").strip() and (cat or "").strip() not in NON_INDUSTRIAL
            ]
            if not real_products:
                return True
    return False


def compute_tier(
    products_categories: Optional[Iterable[str]],
    services_categories: Optional[Iterable[str]] = None,
    activity_text: Optional[str] = None,
    canonical_name: Optional[str] = None,
) -> str:
    """Return the supply-chain tier for a fiche.

    Order of resolution:
      1. If primary role = MRO (maintenance verb / MCO-MRO keyword /
         hand-curated override list / services-only MRO shop) → ``"MRO"``.
      2. Else, take the highest product-derived tier (OEM beats Tier 1,
         Tier 1 beats Tier 2, …).
      3. Else ``"N/A"`` (purely institutional / services / unmapped).
    """
    if _is_mro(services_categories, activity_text,
               products_categories, canonical_name):
        return "MRO"

    if not products_categories:
        return "N/A"
    seen_tiers: set[str] = set()
    for cat in products_categories:
        c = (cat or "").strip()
        if not c or c in NON_INDUSTRIAL:
            continue
        t = _CATEGORY_TO_TIER.get(c)
        if t:
            seen_tiers.add(t)
    if not seen_tiers:
        return "N/A"
    # Take the highest tier present (TIER_ORDER without MRO since MRO
    # is detected separately above)
    for t in ("OEM", "Tier 1", "Tier 2", "Tier 3", "Tier 4"):
        if t in seen_tiers:
            return t
    return "N/A"


def add_supply_chain_tier(record: dict) -> None:
    """Mutate ``record`` in-place adding a ``supply_chain_tier`` key.

    Reads ``products_categories`` (required) plus ``services_categories``
    and ``activity_1liner`` (optional, used to detect MRO).
    """
    # Compute canonical name for hand-override matching
    canon = (
        record.get("canonical_company_name")
        or (record.get("company_name") or "").lower().strip()
    )
    # Strip common legal suffixes for the override match
    canon = re.sub(
        r"\s+(sas|sa|sarl|sasu|gmbh|ag|kg|ohg|se|mbh|gbr|ug|"
        r"e\.v\.|ev|ltd|limited|plc|llp|inc|corp|s\.p\.a\.|s\.r\.l\.|spa|srl)\.?$",
        "", canon,
    ).strip()
    record["supply_chain_tier"] = compute_tier(
        record.get("products_categories") or [],
        services_categories=record.get("services_categories") or [],
        activity_text=record.get("activity_1liner") or "",
        canonical_name=canon,
    )


def coverage_check() -> dict[str, int]:
    """Sanity-check helper: how many categories are mapped vs not.

    Useful in unit tests after editing the master taxonomy.
    """
    from app.processors.taxonomy_normalize import PRODUCT_CATEGORIES

    mapped = 0
    unmapped: list[str] = []
    institutional = 0
    for cat in PRODUCT_CATEGORIES:
        if cat in _CATEGORY_TO_TIER:
            mapped += 1
        elif cat in NON_INDUSTRIAL:
            institutional += 1
        else:
            unmapped.append(cat)
    return {
        "total": len(PRODUCT_CATEGORIES),
        "mapped_to_tier": mapped,
        "non_industrial": institutional,
        "unmapped": len(unmapped),
        "_unmapped_labels": unmapped,
    }


__all__ = [
    "TIER_ORDER",
    "compute_tier",
    "add_supply_chain_tier",
    "coverage_check",
]
