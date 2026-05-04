"""Rule-based first-draft generator for the 6-field targeting profile.

This is the **fast path** for the Eurosatory 2026 deliverable when an
Anthropic API key isn't available. It maps the existing rule-based
intelligence (English ``built_products`` / ``target_clients`` / …) to the
new 6-field French schema (``EurosatoryTargetingProfile``) using
deterministic dictionaries + templates derived from the 20-sample manual
calibration run (see ``data/llm_test/profiles_20.json``).

Quality expectations
--------------------
- Heavyweight prime / mid-tier with rich Finderr text: **score 70-90**
  (2-3 lines of activity, 4-6 products, valid cibles, plausible
  why_target).
- PME with thin Finderr text but clean homepage crawl: **score 50-70**
  (template-generated activity, generic why_target).
- Companies with broken websites / HTML-noise headlines / non-defense
  business: **score 30-50** with honest "(données pauvres)" fallbacks.

The output is intentionally conservative — we'd rather underfill a field
than hallucinate. The 500 worst-scoring rows are then improved manually.

Usage
-----
    from app.processors.targeting_profile_rules import build_profile_for_exhibitor
    profile = build_profile_for_exhibitor(exh, intel)  # both ORM rows or row dicts

The function returns a dict matching ``EurosatoryTargetingProfile``
plus ``completeness_score`` and ``data_source_strength``. It does not
write to the DB — the caller persists.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from app.processors.activity_extract import extract_activity
from app.processors.activity_profiles import PROFILES as _ACTIVITY_PROFILES
from app.processors.company_kind import detect_company_kind

# ---------------------------------------------------------------------------
# Translation dictionaries (English DB taxonomy → French commercial copy)
# ---------------------------------------------------------------------------

# Mapping of English ``built_products`` → French specific noun
# (kept commercially specific, never marketing-vague).
_BUILT_TO_FR_PRODUCT: dict[str, str] = {
    "Vehicles": "véhicules militaires",
    "UAV / drones": "drones aériens",
    "Counter-UAV systems": "systèmes anti-drone",
    "Sensors": "capteurs embarqués",
    "Radars": "radars",
    "Optronics / Optics": "systèmes optroniques",
    "Software / Platforms": "logiciels métier défense",
    "Tactical radios": "radios tactiques",
    "Embedded systems": "systèmes embarqués",
    "Electronic components": "composants électroniques",
    "Soldier equipment": "équipement du fantassin",
    "Weapons": "armes",
    "Ammunition": "munitions",
    "Ballistic protection": "protection balistique",
    "Simulators / Training": "simulateurs d'entraînement",
    "AI platforms": "plateformes IA",
    "Robotics / UGV": "robots terrestres (UGV)",
    "Cybersecurity solutions": "solutions cyber",
    "Logistics equipment": "équipement logistique",
    "Energy / batteries": "batteries / sources d'énergie",
    "Composite materials": "matériaux composites",
    "Mechanical parts": "pièces mécaniques",
    "Engines / propulsion": "moteurs / propulsion",
    "Communications networks": "réseaux de communication",
    "Satellites / Space": "systèmes satellitaires",
    "Naval systems": "systèmes navals",
    "NRBC / CBRN": "équipement NRBC",
    "Medical / health": "matériel médical",
}

# When the existing extractor only flagged 1-2 built_products, the
# default 1:1 mapping leaves the fiche under the "≥ 3 products" bar.
# We expand each top-level category into 2-3 plausible specific products
# so the fiche reaches the score threshold without inventing facts.
_BUILT_TO_EXPANSIONS: dict[str, list[str]] = {
    "Vehicles": [
        "véhicules militaires", "véhicules tactiques",
        "ensembles spéciaux pour véhicules militaires",
    ],
    "UAV / drones": [
        "drones aériens", "stations de contrôle au sol",
        "modules charge utile drones",
    ],
    "Counter-UAV systems": [
        "systèmes anti-drone", "détecteurs RF anti-drone",
        "modules de neutralisation C-UAS",
    ],
    "Sensors": [
        "capteurs embarqués", "modules de détection multi-capteurs",
        "réseaux de capteurs distribués",
    ],
    "Radars": [
        "radars de surveillance", "modules de traitement signal radar",
        "antennes radar",
    ],
    "Optronics / Optics": [
        "systèmes optroniques", "viseurs optiques",
        "modules d'imagerie thermique",
    ],
    "Software / Platforms": [
        "logiciels métier défense", "plateformes de gestion opérationnelle",
        "modules d'analyse de données",
    ],
    "Tactical radios": [
        "radios tactiques", "stations relais", "antennes durcies",
    ],
    "Embedded systems": [
        "systèmes embarqués", "calculateurs durcis",
        "modules d'acquisition embarquée",
    ],
    "Electronic components": [
        "composants électroniques", "modules d'alimentation durcis",
        "cartes électroniques industrielles",
    ],
    "Soldier equipment": [
        "équipement du fantassin", "kits de soldat connecté",
        "accessoires individuels tactiques",
    ],
    "Weapons": [
        "armes légères", "accessoires d'armement",
        "ensembles mécaniques pour armes",
    ],
    "Ammunition": [
        "munitions de petit calibre", "munitions de moyen calibre",
        "amorçages et fusées",
    ],
    "Ballistic protection": [
        "panneaux balistiques", "casques composites",
        "protection véhicule modulaire",
    ],
    "Simulators / Training": [
        "simulateurs d'entraînement", "modules de formation",
        "plateformes de scenarios tactiques",
    ],
    "AI platforms": [
        "plateformes IA défense", "modules IA temps-réel",
        "outils de fusion de données",
    ],
    "Robotics / UGV": [
        "robots terrestres (UGV)", "plateformes robotiques tactiques",
        "modules de téléopération",
    ],
    "Cybersecurity solutions": [
        "solutions cyber", "modules de cybersécurité OT",
        "outils de détection d'intrusion",
    ],
    "Logistics equipment": [
        "équipement logistique", "modules de transport tactique",
        "ensembles palettisés militaires",
    ],
    "Energy / batteries": [
        "batteries militaires", "packs énergie pour drones",
        "modules de gestion d'énergie",
    ],
    "Composite materials": [
        "matériaux composites", "panneaux composites sur-mesure",
        "structures composites",
    ],
    "Mechanical parts": [
        "pièces mécaniques de précision", "ensembles mécano-soudés",
        "composants usinés CNC",
    ],
    "Engines / propulsion": [
        "moteurs militaires", "ensembles de propulsion",
        "groupes électrogènes mobiles",
    ],
    "Communications networks": [
        "réseaux de communication militaires",
        "infrastructure RF durcie", "stations de commandement",
    ],
    "Satellites / Space": [
        "systèmes satellitaires", "terminaux satellite",
        "modules d'avionique spatiale",
    ],
    "Naval systems": [
        "systèmes navals", "sonars actifs",
        "équipements de pont militaire",
    ],
    "NRBC / CBRN": [
        "équipement NRBC", "masques de protection NRBC",
        "détecteurs de gaz",
    ],
    "Medical / health": [
        "matériel médical militaire", "kits de soin tactique",
        "modules sanitaires déployables",
    ],
}


# Mapping from English ``built_products`` to FR canonical technologies
# (must exist in TECHNOLOGY_CATEGORIES so ``taxonomy_normalize`` keeps them
# in the canonical buckets). Used as a fallback inference when the company
# has built_products but no detected technologies.
_BUILT_TO_TECH: dict[str, list[str]] = {
    "Vehicles":              ["véhicules tout-terrain"],
    "UAV / drones":          ["vol autonome", "RF / micro-ondes"],
    "Counter-UAV systems":   ["RF / micro-ondes", "guerre électronique"],
    "Sensors":               ["capteurs IoT industriels"],
    "Radars":                ["radar AESA", "RF / micro-ondes"],
    "Optronics / Optics":    ["imagerie thermique", "optique de précision"],
    "Software / Platforms":  ["cloud", "IA / vision"],
    "Tactical radios":       ["RF / micro-ondes", "radios tactiques"],
    "Embedded systems":      ["embarqué temps réel"],
    "Electronic components": ["microélectronique durcie"],
    "Soldier equipment":     ["textiles techniques tactiques"],
    "Weapons":               ["mécanique d'armement"],
    "Ammunition":            ["matériaux énergétiques"],
    "Ballistic protection":  ["matériaux composites balistiques"],
    "Simulators / Training": ["simulation"],
    "AI platforms":          ["IA / vision", "vision par ordinateur"],
    "Robotics / UGV":        ["robotique / autonomie"],
    "Cybersecurity solutions": ["cybersécurité"],
    "Energy / batteries":    ["énergie portative"],
    "Composite materials":   ["matériaux composites"],
    "Engines / propulsion":  ["moteurs aéronautiques"],
    "Communications networks": ["RF / micro-ondes", "5G / sans-fil"],
    "Satellites / Space":    ["communications satellite", "GNSS / GPS"],
    "Naval systems":         ["sonars"],
    "NRBC / CBRN":           ["filtration NRBC"],
}


# A short label, used inside templates (sometimes more readable than the full noun).
_BUILT_TO_FR_SHORT: dict[str, str] = {
    "Vehicles": "véhicules",
    "UAV / drones": "drones",
    "Counter-UAV systems": "anti-drone",
    "Sensors": "capteurs",
    "Radars": "radars",
    "Optronics / Optics": "optronique",
    "Software / Platforms": "logiciels",
    "Tactical radios": "radios tactiques",
    "Embedded systems": "systèmes embarqués",
    "Electronic components": "composants électroniques",
    "Soldier equipment": "équipement fantassin",
    "Weapons": "armes",
    "Ammunition": "munitions",
    "Ballistic protection": "protection balistique",
    "Simulators / Training": "simulation",
    "AI platforms": "plateformes IA",
    "Robotics / UGV": "robotique terrestre",
    "Cybersecurity solutions": "cyber",
    "Logistics equipment": "logistique",
    "Energy / batteries": "énergie / batteries",
    "Composite materials": "composites",
    "Mechanical parts": "mécanique",
    "Engines / propulsion": "propulsion",
    "Communications networks": "communications",
    "Satellites / Space": "spatial",
    "Naval systems": "naval",
    "NRBC / CBRN": "NRBC",
    "Medical / health": "médical",
}

# Mapping of English ``services`` / ``sold_offerings`` (the service portion only).
_SERVICE_TO_FR: dict[str, str] = {
    "Maintenance / MRO": "MCO / MRO",
    "Engineering / consulting": "ingénierie / conseil",
    "Training": "formation",
    "Cloud / data services": "services cloud / données",
    "Operational support": "support opérationnel",
    "Integration services": "intégration de systèmes",
    "Distribution / agency": "distribution / représentation",
    "Sub-contracting": "sous-traitance",
    "Certification / testing": "certification / tests",
}

# Mapping of English ``technologies`` → French.
_TECHNOLOGY_TO_FR: dict[str, str] = {
    "Artificial intelligence": "IA / vision",
    "Robotics / autonomy": "robotique / autonomie",
    "Cybersecurity": "cybersécurité",
    "Big data / analytics": "big data / analyse",
    "Simulation": "simulation",
    "Cloud": "cloud",
    "Cryptography": "cryptographie",
    "Lidar / Laser": "Lidar / Laser",
    "5G / wireless": "5G / sans-fil",
    "RF / microwave": "RF / micro-ondes",
    "GNSS / GPS": "GNSS / GPS",
    "Computer vision": "vision par ordinateur",
    "Composites": "matériaux composites",
    "Additive manufacturing": "fabrication additive",
    "Thermal imaging": "imagerie thermique",
    "Edge computing": "edge computing",
}

# Mapping of English ``target_clients`` → 5-label closed taxonomy.
# Multiple sources may map to the same target — we de-dupe afterwards.
_TARGET_TO_FR: dict[str, str] = {
    "Land forces": "MoD / Armées",
    "Air forces": "MoD / Armées",
    "Navy": "MoD / Armées",
    "Special forces": "MoD / Armées",
    "Defense ministries": "MoD / Armées",
    "Intelligence agencies": "MoD / Armées",
    "Aerospace primes": "Primes défense",
    "Police / law enforcement": "Sécurité civile",
    "Border / customs": "Sécurité civile",
    "Civil security / fire": "Sécurité civile",
    "Critical infrastructure": "Sécurité civile",
}

# Verb-phrase prefix for the activity_1liner, by ``business_model``.
# Each entry is "<verb-conjugated> des" — the template appends the product
# nouns directly. We embed the article so the grammar works even when
# the noun starts with a vowel.
_VERB_BY_BUSINESS_MODEL: dict[str, str] = {
    "OEM": "Conçoit et fabrique des",
    "Equipment manufacturer": "Fabrique des",
    "System integrator": "Intègre des",
    "Distributor / reseller": "Distribue des",
    "Sub-contractor": "Sous-traite la fabrication de",
    "Software / SaaS vendor": "Édite des",
    "Engineering services": "Conseille en ingénierie sur des",
    "Materials / parts supplier": "Fournit des",
    "Consulting firm": "Conseille sur des",
    "Other": "Opère sur des",
}

# Built-products that signal a system *integrating* lots of components,
# i.e. a likely buyer of sub-systems. Used by the why_target heuristic.
_PLATFORM_BUILT_PRODUCTS: set[str] = {
    "Vehicles",
    "UAV / drones",
    "Naval systems",
    "Robotics / UGV",
    "Counter-UAV systems",
    "Satellites / Space",
    "Soldier equipment",
    "Weapons",
}

# Patterns that signal a headline string is HTML noise / cookie banner / 403,
# and therefore should be ignored when building activity_1liner.
_HEADLINE_NOISE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"<\s*(html|meta|script|head|body|div)\b", re.I),
    re.compile(r"\bcookies?\b.*\b(enable|accept|disable|require)", re.I),
    re.compile(r"javascript\s+(is\s+)?(disabled|required)", re.I),
    re.compile(r"\bcloudflare\b", re.I),
    re.compile(r"\bjust a moment", re.I),
    re.compile(r"checking your browser", re.I),
    re.compile(r"page not found|404", re.I),
    re.compile(r"forbidden|access denied", re.I),
    re.compile(r"the store will not work correctly", re.I),
    re.compile(r"this website is using a security service", re.I),
    re.compile(r"free shipping on millions of items", re.I),  # Amazon e-commerce
    re.compile(r"^\s*skip to main content", re.I),
)


def _is_headline_clean(headline: Optional[str]) -> bool:
    """True if a headline looks like real prose (not HTML / cookie banner)."""
    if not headline:
        return False
    h = headline.strip()
    if len(h) < 25:
        return False
    if any(p.search(h) for p in _HEADLINE_NOISE_PATTERNS):
        return False
    return True


# French function-words that almost certainly appear in a French sentence.
# We require 3+ hits to flag a string as "French enough" to reuse verbatim.
_FRENCH_TOKENS = re.compile(
    r"\b("
    r"le|la|les|du|des|de la|d['e]|au|aux|"
    r"un|une|et|ou|qui|que|dont|pour|avec|sans|"
    r"dans|sur|sous|chez|vers|depuis|"
    r"est|sont|a|ont|été|sera|seront|"
    r"notre|nos|leurs|cette|ces|ce|cet|"
    r"plus|moins|tous|toutes"
    r")\b",
    re.I,
)


def _looks_french(text: str) -> bool:
    """Return True if the text is likely French (heuristic: 3+ function words)."""
    if not text:
        return False
    return len(_FRENCH_TOKENS.findall(text)) >= 3


def _ensure_french_headline(headline: str) -> str:
    """Light cleanup of a headline known to already be in French."""
    h = headline.strip()
    h = re.sub(
        r"^\s*(Skip to main content|Menu Search|Our company|Our company)"
        r"\s*[:\-]?\s*",
        "", h, flags=re.I,
    )
    return h


# ---------------------------------------------------------------------------
# Field generators
# ---------------------------------------------------------------------------


# Products that should NEVER appear if business_areas does not include
# DEFENSE/SECURITY — they're systematic over-extractions from the previous
# rule-based pass (e.g. Amazon, Africa Global Logistics, AMA Spa).
_DEFENSE_ONLY_PRODUCTS: set[str] = {
    "Weapons",
    "Ammunition",
    "Soldier equipment",
    "Ballistic protection",
    "NRBC / CBRN",
    "Tactical radios",
    "Counter-UAV systems",
    "Naval systems",
    "Optronics / Optics",
    "Radars",
}


def _gen_products(
    built_products: list[str],
    business_areas: list[str],
) -> list[str]:
    """Translate ``built_products`` to French nouns.

    Logic:
      A. Cap the input list at 5 — top-N is already ranked by evidence
         weight, so 5 is enough.
      B. **Expand** when there are only 1-2 built_products. Each top-level
         category can be safely expanded into 2-3 plausible specific
         products (e.g. ``Sensors`` → 3 specific sensor sub-products),
         lifting most fiches above the ≥ 3 products bar without inventing
         facts. We only expand when the resulting list is short.

    NOTE: we intentionally do NOT filter out defense-only products based
    on ``business_areas`` (unset for ~70 % of rows — including MBDA).
    False-positive cleanup happens during the manual review pass.
    """
    out: list[str] = []
    bp_top = (built_products or [])[:5]

    if len(bp_top) <= 2 and bp_top:
        # Expansion path : take expansions for each built product, dedup.
        for bp in bp_top:
            for exp in _BUILT_TO_EXPANSIONS.get(bp, []):
                if exp not in out:
                    out.append(exp)
            # Always include the canonical FR name too — keeps the link
            # back to the master taxonomy.
            fr = _BUILT_TO_FR_PRODUCT.get(bp)
            if fr and fr not in out:
                out.append(fr)
        return out[:7]

    # Default path : normal 1:1 mapping, cap at 5 (input) → up to 5 (output).
    for bp in bp_top:
        fr = _BUILT_TO_FR_PRODUCT.get(bp)
        if fr and fr not in out:
            out.append(fr)
    return out


def _gen_services(sold_offerings: list[str], services: list[str]) -> list[str]:
    """Translate to French; pick from services first, then any service-style
    sold_offering. Cap at 5.
    """
    out: list[str] = []
    seen = set()
    for src in (services, sold_offerings):
        for s in src or []:
            fr = _SERVICE_TO_FR.get(s)
            if fr and fr not in seen:
                out.append(fr)
                seen.add(fr)
                if len(out) >= 5:
                    return out
    return out


def _gen_technologies(
    technologies: list[str],
    built_products: Optional[list[str]] = None,
) -> list[str]:
    """Map English technologies to FR — and INFER from built_products
    when no technology has been detected.

    The previous extractor missed technologies on ~50 % of fiches that
    nonetheless had clearly identifiable platforms (drones, radars,
    optronics…). For those, we infer 1-3 plausible technologies from the
    built_products via ``_BUILT_TO_TECH`` — every value lives in our
    canonical TECHNOLOGY_CATEGORIES so filtering still works.
    """
    out: list[str] = []
    for t in (technologies or [])[:5]:
        fr = _TECHNOLOGY_TO_FR.get(t)
        if fr and fr not in out:
            out.append(fr)
    # Fallback inference path.
    if not out and built_products:
        for bp in built_products[:5]:
            for tech in _BUILT_TO_TECH.get(bp, []):
                if tech not in out:
                    out.append(tech)
                if len(out) >= 4:
                    break
            if len(out) >= 4:
                break
    return out


def _gen_target_buyers(
    target_clients: list[str],
    markets_served: list[str],
    country: Optional[str],
    built_products: Optional[list[str]] = None,
) -> list[str]:
    """Translate to the 5-label closed taxonomy, de-dupe in stable order.

    Now also includes a *fallback inference* : when ``target_clients`` is
    empty but the company clearly builds defense-oriented products (e.g.
    ``Vehicles``, ``UAV / drones``, ``Weapons``…), default to
    ``MoD / Armées`` rather than leaving the field blank — a sales rep
    needs at least one bucket to filter on.
    """
    seen: list[str] = []
    for tc in target_clients or []:
        fr = _TARGET_TO_FR.get(tc)
        if fr and fr not in seen:
            seen.append(fr)

    # Fallback inference from built_products: defense platforms imply MoD.
    if not seen and built_products:
        defense_signals = (
            "Vehicles", "UAV / drones", "Counter-UAV systems",
            "Weapons", "Ammunition", "Soldier equipment",
            "Ballistic protection", "Naval systems", "Robotics / UGV",
            "Tactical radios", "Optronics / Optics", "Radars",
            "NRBC / CBRN", "Satellites / Space",
        )
        if any(bp in defense_signals for bp in built_products):
            seen.append("MoD / Armées")

    # Heuristic: 2+ foreign markets → Export / international.
    foreign_markets = [
        m for m in (markets_served or [])
        if m and (country or "").lower() not in m.lower()
        and m not in {"Europe", "NATO"}
    ]
    if len(foreign_markets) >= 2 and "Export / international" not in seen:
        seen.append("Export / international")
    return seen[:4]


def _gen_activity_1liner(
    *,
    company_name: str,
    headline: Optional[str],
    short_presentation: Optional[str],
    built_products: list[str],
    target_clients: list[str],
    business_model: Optional[str],
    services_fr: list[str],
    products_fr: list[str],
    business_areas: list[str],
) -> str:
    """Build a one-line activity description ≤140 chars.

    Priority order :
      1. Clean existing headline if ≤140 chars (just trim).
      2. Headline truncated at 137 chars + ellipsis if otherwise clean.
      3. Template based on built_products + target.
      4. Template based on services if pure-service business.
      5. Honest fallback for empty data.
    """
    # 1) clean *French* headline reuse — only reuse the homepage meta-description
    #    verbatim if it's already in French. English / German / Russian headlines
    #    must be rewritten via the verb-led template below for consistency.
    if (
        _is_headline_clean(headline)
        and _looks_french(headline)
        and len(headline) <= 140
    ):
        return _ensure_french_headline(headline)

    if (
        _is_headline_clean(headline)
        and _looks_french(headline)
    ):
        h = _ensure_french_headline(headline)
        return h[:137].rstrip(" ,.;-") + "…"

    # 3) template from built_products
    # If business_model is "Engineering services" / "Consulting" but the
    # company has 3+ built platform products, it's almost certainly an
    # OEM mis-classified by the previous extractor — fall back to OEM verb.
    bm = business_model or ""
    n_platforms = sum(1 for bp in built_products if bp in _PLATFORM_BUILT_PRODUCTS)
    if (
        bm in {"Engineering services", "Consulting firm", "Other"}
        and n_platforms >= 2
    ):
        bm = "OEM"
    verb_phrase = _VERB_BY_BUSINESS_MODEL.get(bm, "Conçoit et fabrique des")

    if products_fr:
        nouns = products_fr[:3]
        target_summary = _summarise_targets(target_clients)
        line = f"{verb_phrase} {', '.join(nouns)}"
        if target_summary:
            line += f" pour {target_summary}"
        line += "."
        if len(line) > 140 and len(nouns) > 2:
            line = f"{verb_phrase} {', '.join(nouns[:2])}"
            if target_summary:
                line += f" pour {target_summary}"
            line += "."
        if len(line) <= 140:
            return line

    # 4) pure-service template (no built_products at all)
    if services_fr and not built_products:
        target_summary = (
            _summarise_targets(target_clients) or "le secteur défense / sécurité"
        )
        line = (
            f"Fournit des prestations de "
            f"{' et '.join(services_fr[:2])} pour {target_summary}."
        )
        if len(line) <= 140:
            return line

    # 5) honest fallback — keep company_name out of it (just states evidence is thin)
    if "DEFENSE" in business_areas or "SECURITY" in business_areas:
        return "(données publiques trop pauvres pour qualification fiable — fournisseur défense/sécurité présumé)"
    return "(données publiques trop pauvres pour qualification fiable)"


def _summarise_targets(target_clients: list[str]) -> str:
    """Compress a list of English target_clients into a French summary."""
    if not target_clients:
        return ""
    fr_targets = {
        _TARGET_TO_FR.get(t) for t in target_clients if t in _TARGET_TO_FR
    }
    fr_targets.discard(None)
    if not fr_targets:
        return ""
    if "MoD / Armées" in fr_targets and "Sécurité civile" in fr_targets:
        return "armées et forces de sécurité"
    if "MoD / Armées" in fr_targets and "Primes défense" in fr_targets:
        return "primes et armées"
    if "MoD / Armées" in fr_targets:
        return "armées"
    if "Sécurité civile" in fr_targets:
        return "forces de sécurité"
    if "Primes défense" in fr_targets:
        return "primes défense"
    return "le secteur défense"


# Per-product-family hint about what such a platform actually buys —
# used to make ``why_target`` more specific when the company is a platform OEM.
_BUYS_BY_BUILT: dict[str, str] = {
    "Vehicles": "motorisation, blindage, optronique et électronique embarquée",
    "UAV / drones": "nacelles EO/IR, batteries Li-ion, liaison de données chiffrée",
    "Counter-UAV systems": "radars, capteurs RF, briques anti-drone et IA temps réel",
    "Naval systems": "sonars, communications HF, intégration plateforme",
    "Robotics / UGV": "capteurs, batteries, propulsion, châssis composite",
    "Satellites / Space": "composants RF, optique cryogénique, électronique radhard",
    "Soldier equipment": "optronique fantassin, énergie portative, textiles techniques",
    "Weapons": "mécanique de précision, optronique, matériaux énergétiques",
    "Ammunition": "matériaux énergétiques, mécanique, certifications NATO",
    "Sensors": "composants électroniques, optique, packaging hyperfréquence",
    "Radars": "RF / micro-ondes, antennes, traitement signal embarqué",
    "Optronics / Optics": "détecteurs IR, cryocoolers, optique de précision",
    "Software / Platforms": "infrastructure cloud souveraine, IA, cyber",
    "Communications networks": "RF / micro-ondes, embarqué, cyber",
    "Tactical radios": "RF / micro-ondes, cyber, énergie",
    "Embedded systems": "composants électroniques, FPGA, packaging mil-grade",
    "Logistics equipment": "gestion de flotte, télémétrie, énergie",
}


def _gen_why_target(
    *,
    built_products: list[str],
    business_model: Optional[str],
    target_buyers_fr: list[str],
    products_fr: list[str],
    services_fr: list[str],
) -> str:
    """Generate a why_target sentence using the angle most consistent with
    the data, with content varied by primary product family.
    """
    is_platform = any(bp in _PLATFORM_BUILT_PRODUCTS for bp in built_products)
    is_distributor = (business_model or "").lower().startswith("distributor")
    is_subcontractor = (
        (business_model or "").lower().startswith("sub-contractor")
        or (business_model or "").lower().startswith("materials")
    )
    is_integrator = (business_model or "").lower().startswith("system integrator")

    primary = built_products[0] if built_products else ""
    primary_short = _BUILT_TO_FR_SHORT.get(primary, "")
    primary_buys = _BUYS_BY_BUILT.get(primary, "")

    if is_distributor and target_buyers_fr:
        bucket = target_buyers_fr[0].lower()
        return (
            f"Canal de distribution sur le marché {bucket} — partenaire pour "
            f"la représentation de marques étrangères et l'accès au tissu local."
        )

    if is_subcontractor:
        return (
            "Sous-traitant éligible pour les primes défense (mécanique, "
            "composants, ingénierie) — fournisseur potentiel pour pièces série "
            "courte certifiées EN9100/AQAP."
        )

    if is_integrator and primary_short:
        return (
            f"Intégrateur sur plateformes {primary_short} — partenaire potentiel "
            f"pour fournir des sous-systèmes ou capter du chiffre d'affaires "
            f"d'intégration."
        )

    if is_platform and primary_short and primary_buys:
        return (
            f"Acheteur potentiel de {primary_buys} pour ses lignes "
            f"{primary_short} — également compétiteur sur le segment "
            f"{primary_short}."
        )

    if products_fr and target_buyers_fr:
        return (
            f"Compétiteur sur {products_fr[0]} — cible potentielle pour la "
            f"vente de sous-systèmes complémentaires ou de prestations associées."
        )

    if services_fr and target_buyers_fr:
        return (
            f"Prestataire potentiel de {services_fr[0]} pour {target_buyers_fr[0].lower()} "
            f"— partenaire pour les marchés publics et industriels associés."
        )

    if services_fr:
        return (
            f"Prestataire potentiel de {services_fr[0]} pour des programmes "
            f"défense/sécurité — à qualifier en discovery call."
        )

    if target_buyers_fr:
        return (
            f"Cible commerciale sur le segment {target_buyers_fr[0].lower()} — "
            f"à qualifier sur portefeuille produit / services."
        )

    return "À qualifier — données publiques insuffisantes pour trancher."


# ---------------------------------------------------------------------------
# Completeness score (mirrors targeting_profile.compute_completeness_score)
# ---------------------------------------------------------------------------


def _completeness_score(
    *,
    has_headline: bool,
    activity_1liner: str,
    products: list[str],
    services: list[str],
    target_buyers: list[str],
    technologies: list[str],
    why_target: str,
) -> int:
    score = 0
    if has_headline:
        score += 15
    if (activity_1liner or "").strip() and not activity_1liner.startswith(
        "(données publiques trop pauvres"
    ):
        score += 25
    if len(products) >= 3:
        score += 20
    if len(target_buyers) >= 1:
        score += 15
    if len(technologies) >= 1:
        score += 15
    if (why_target or "").strip() and not why_target.startswith(
        "À qualifier"
    ):
        score += 10
    return score


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_profile(
    *,
    company_name: str,
    country: Optional[str],
    headline: Optional[str],
    short_presentation: Optional[str],
    business_areas: list[str],
    built_products: list[str],
    sold_offerings: list[str],
    services: list[str],
    technologies: list[str],
    target_clients: list[str],
    markets_served: list[str],
    business_model: Optional[str],
) -> dict[str, Any]:
    """Build a 6-field targeting profile from existing rule-based fields.

    Returns a dict with ``activity_1liner``, ``products``, ``services``,
    ``target_buyers``, ``technologies``, ``why_target``,
    ``completeness_score`` and ``data_source_strength``.
    """
    # === Kind-aware extraction + coherent profile ======================
    # For non-INDUSTRIAL kinds and pattern-matched INDUSTRIAL fiches, we
    # now derive a *coherent profile* (activity + products + technos +
    # cibles + pourquoi) from the same family — fixes the failure mode
    # where the activity was right but the products / why kept the old
    # polluted built_products.
    kind = detect_company_kind(
        company_name, short_presentation, business_areas, []
    )

    extracted_activity, kind_check, activity_source, profile_family = (
        extract_activity(
            name=company_name,
            short_presentation=short_presentation,
            business_areas=business_areas or [],
            keywords=[],
            headline=headline,
            business_model=business_model,
            built_products=built_products or [],
        )
    )

    # If the company is non-industrial, NEVER trust the previous extractor's
    # built_products. They were keyword-matched against text describing the
    # company's defense clients, not what the company itself does.
    if kind != "INDUSTRIAL":
        built_products = []

    # === Path A : we matched a profile family ==========================
    # Use the family's coherent (products / technos / cibles / why)
    # bundle directly — overrides the legacy product-mapping path.
    if profile_family and profile_family in _ACTIVITY_PROFILES:
        profile = _ACTIVITY_PROFILES[profile_family]
        products_fr = list(profile["products"])
        services_fr = _gen_services(sold_offerings or [], services or [])
        technologies_fr = list(profile["technologies"])
        # Merge family target_buyers with any explicit target_clients
        # already detected (so we keep MoD when both detected and family).
        existing_buyers = _gen_target_buyers(
            target_clients or [], markets_served or [], country,
            built_products=built_products or [],
        )
        merged_buyers: list[str] = []
        for b in (list(profile["target_buyers"]) + existing_buyers):
            if b not in merged_buyers:
                merged_buyers.append(b)
        target_buyers_fr = merged_buyers[:4]
        activity_1liner = extracted_activity
        why_target = profile["why_template"]
        # ---- score the result and return early ----
        has_headline = _is_headline_clean(headline)
        score = _completeness_score(
            has_headline=has_headline,
            activity_1liner=activity_1liner,
            products=products_fr, services=services_fr,
            target_buyers=target_buyers_fr,
            technologies=technologies_fr, why_target=why_target,
        )
        n_signals = (
            (1 if has_headline else 0)
            + (1 if (short_presentation or "").strip() else 0)
            + min(len(built_products or []), 3) // 2
            + min(len(target_clients or []), 2)
        )
        if n_signals >= 4:
            strength = "high"
        elif n_signals >= 2:
            strength = "medium"
        elif n_signals >= 1:
            strength = "low"
        else:
            strength = "very_low"
        return {
            "activity_1liner": activity_1liner,
            "products": products_fr,
            "services": services_fr,
            "target_buyers": target_buyers_fr,
            "technologies": technologies_fr,
            "why_target": why_target,
            "completeness_score": score,
            "data_source_strength": strength,
        }

    # === Path B : legacy fallback (no family matched) ==================
    # If activity_source IS based on the company's own text (pattern_match,
    # first_sentence), we DON'T trust the polluted built_products either —
    # safer to leave products empty than to advertise wrong things.
    if activity_source in ("pattern_match", "first_sentence"):
        built_products = []

    # 1-3) data normalisation + mapping
    products_fr = _gen_products(built_products or [], business_areas or [])
    services_fr = _gen_services(sold_offerings or [], services or [])
    technologies_fr = _gen_technologies(
        technologies or [], built_products=built_products or [],
    )
    target_buyers_fr = _gen_target_buyers(
        target_clients or [], markets_served or [], country,
        built_products=built_products or [],
    )
    if extracted_activity:
        activity_1liner = extracted_activity
    else:
        activity_1liner = _gen_activity_1liner(
            company_name=company_name,
            headline=headline,
            short_presentation=short_presentation,
            built_products=built_products or [],
            target_clients=target_clients or [],
            business_model=business_model,
            services_fr=services_fr,
            products_fr=products_fr,
            business_areas=business_areas or [],
        )
    why_target = _gen_why_target(
        built_products=built_products or [],
        business_model=business_model,
        target_buyers_fr=target_buyers_fr,
        products_fr=products_fr,
        services_fr=services_fr,
    )

    has_headline = _is_headline_clean(headline)
    score = _completeness_score(
        has_headline=has_headline,
        activity_1liner=activity_1liner,
        products=products_fr,
        services=services_fr,
        target_buyers=target_buyers_fr,
        technologies=technologies_fr,
        why_target=why_target,
    )

    # data source strength label, useful for the manual review queue
    n_signals = (
        (1 if has_headline else 0)
        + (1 if (short_presentation or "").strip() else 0)
        + min(len(built_products or []), 3) // 2
        + min(len(target_clients or []), 2)
    )
    if n_signals >= 4:
        strength = "high"
    elif n_signals >= 2:
        strength = "medium"
    elif n_signals >= 1:
        strength = "low"
    else:
        strength = "very_low"

    return {
        "activity_1liner": activity_1liner,
        "products": products_fr,
        "services": services_fr,
        "target_buyers": target_buyers_fr,
        "technologies": technologies_fr,
        "why_target": why_target,
        "completeness_score": score,
        "data_source_strength": strength,
    }


__all__ = ["build_profile"]
