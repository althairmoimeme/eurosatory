"""Generators for the four narrative columns added to the CRM record:

- ``core_business``                 — short FR phrase shown in the table (~50 chars).
- ``main_products_services``        — concise FR list of what they make / sell.
- ``commercial_relevance_summary``  — one-line "why they matter" for sales.
- ``company_pitch``                 — 3-4 line professional mini-pitch shown
                                      under the company name in the detail card.

All four are produced from data we already have in the DB (Eurosatory catalogue
+ Finderr details + crawled pages — never invented).  When the inputs are too
thin to produce a useful sentence, we emit ``"À enrichir — activité exacte à
vérifier."`` instead of fabricating something.
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Translation tables — English internal taxonomy → French marketing labels
# ---------------------------------------------------------------------------

SEGMENT_FR: dict[str, str] = {
    "Counter-UAV": "systèmes anti-drones",
    "UAV / drones": "drones et UAS",
    "Cybersecurity": "cybersécurité défense",
    "AI / data": "plateformes IA et fusion de données",
    "Electronic warfare": "guerre électronique",
    "Intelligence / ISR": "systèmes ISR",
    "C4ISR": "systèmes C4ISR",
    "Radar / sensors": "radars et capteurs",
    "Optics / optronics": "optronique et imagerie",
    "Communications": "communications tactiques",
    "Armored vehicles": "véhicules blindés",
    "Weapons": "armement",
    "Ammunition": "munitions",
    "Soldier systems": "équipements du combattant",
    "Ballistic protection": "protection balistique",
    "Simulation / training": "simulation et entraînement",
    "Logistics / MRO": "logistique et MCO",
    "Engineering services": "ingénierie et bureau d'études",
    "Industrial subcontracting": "sous-traitance industrielle",
    "Naval defense": "défense navale",
    "Air defense": "défense aérienne",
    "Land defense": "défense terrestre",
    "Homeland security": "sécurité intérieure",
    "Space / satellite": "spatial et satellite",
    "NRBC / CBRN": "NRBC",
    "Dual-use technology": "technologies duales",
    "Export / distribution": "export et distribution",
    "Other": "activité défense (à préciser)",
}

PRODUCT_FR: dict[str, str] = {
    "UAV / drones": "drones",
    "Counter-UAV systems": "systèmes anti-drones",
    "Sensors": "capteurs",
    "Radars": "radars",
    "Optronics / Optics": "optronique",
    "Software / Platforms": "plateformes logicielles",
    "Tactical radios": "radios tactiques",
    "Embedded systems": "systèmes embarqués",
    "Electronic components": "composants électroniques",
    "Soldier equipment": "équipement du combattant",
    "Weapons": "armement",
    "Ammunition": "munitions",
    "Ballistic protection": "protection balistique",
    "Simulators / Training": "simulateurs et systèmes d'entraînement",
    "AI platforms": "plateformes IA",
    "Robotics / UGV": "robotique et UGV",
    "Cybersecurity solutions": "solutions cybersécurité",
    "Logistics equipment": "équipement logistique",
    "Energy / batteries": "solutions d'énergie embarquée",
    "Composite materials": "matériaux composites",
    "Mechanical parts": "pièces mécaniques",
    "Engines / propulsion": "moteurs et propulsion",
    "Communications networks": "réseaux de communication",
    "Satellites / Space": "satellites",
    "Naval systems": "systèmes navals",
    "Vehicles": "véhicules tactiques",
    "NRBC / CBRN": "équipements NRBC",
    "Medical / health": "équipements médicaux militaires",
}

BUYING_NEED_FR: dict[str, str] = {
    "Energy / batteries": "énergie embarquée et batteries",
    "Engines / propulsion": "moteurs et propulsion",
    "Sensors": "capteurs",
    "Optronics / Optics": "optronique",
    "Communications networks": "réseaux de communication",
    "Composite materials": "matériaux composites",
    "Mechanical parts": "pièces mécaniques",
    "Electronic components": "composants électroniques",
    "Embedded systems": "systèmes embarqués",
    "RF / microwave components": "composants RF / hyperfréquences",
    "AI platforms": "plateformes IA",
    "Cloud / data services": "cloud et services data",
    "Cybersecurity solutions": "solutions cybersécurité",
    "Radars": "radars",
    "Industrial subcontracting / machining": "sous-traitance industrielle / usinage",
    "Quality / certification services (ITAR, NATO AQAP, ISO 9100)": "certification qualité (ITAR, AQAP, ISO 9100)",
    "Export financing / insurance": "financement / assurance export",
    "Logistics & freight forwarding": "logistique et transit",
    "Cybersecurity audits": "audits cybersécurité",
    "Marketing / event representation": "marketing / représentation événementielle",
    "Hardware appliances": "appliances hardware",
    "Threat intelligence feeds": "flux de threat intelligence",
    "Annotation / data services": "services de data annotation",
    "Compute hardware (GPU)": "infrastructure GPU",
    "Materials": "matériaux",
    "Energetic materials": "matériaux énergétiques",
    "Packaging": "packaging industriel",
    "Launch services": "services de lancement spatial",
    "Filtration materials": "matériaux de filtration",
    "Personal protective equipment": "EPI militaires",
    "Ammunition components": "composants munitions",
    "Textile / industrial fabrics": "textiles et tissus industriels",
}


OFFERING_FR: dict[str, str] = {
    "Finished products": "produits finis",
    "Sub-systems / Components": "sous-systèmes et composants",
    "Integration services": "intégration de systèmes",
    "Maintenance / MRO": "maintenance / MCO",
    "Engineering / consulting": "ingénierie et conseil",
    "Training": "formation",
    "Software licenses": "licences logicielles",
    "Cloud / data services": "services cloud / data",
    "Operational support": "support opérationnel",
    "Distribution / agency": "distribution / agence",
    "Sub-contracting": "sous-traitance",
    "Certification / testing": "certification et essais",
}

VERB_BY_TYPE: dict[str, str] = {
    "Manufacturer": "fabrique",
    "Software / SaaS": "édite",
    "Service company": "propose",
    "Distributor": "distribue",
    "Research / lab": "développe",
    "Engineering firm": "conçoit",
}

TO_ENRICH = "À enrichir — activité exacte à vérifier."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fr_segment(segment_main: Optional[str]) -> Optional[str]:
    if not segment_main:
        return None
    return SEGMENT_FR.get(segment_main, segment_main.lower())


def _smart_capitalize(text: str) -> str:
    """Uppercase first character only — preserves acronyms (ISR, C4ISR, NRBC)."""
    if not text:
        return text
    return text[0].upper() + text[1:]


def _fr_product(label: str) -> str:
    return PRODUCT_FR.get(label, label.lower())


def _fr_products(builts: list[str], limit: int = 3) -> list[str]:
    return [_fr_product(p) for p in (builts or [])[:limit]]


def _fr_offerings(sold: list[str], limit: int = 3) -> list[str]:
    return [OFFERING_FR.get(s, s.lower()) for s in (sold or [])[:limit]]


def _fr_buying_need(need: Optional[str]) -> Optional[str]:
    if not need:
        return None
    return BUYING_NEED_FR.get(need, need.lower())


def _ascii_join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} et {items[1]}"
    return ", ".join(items[:-1]) + f" et {items[-1]}"


def _verb_for(company_type: Optional[str], builts: list[str]) -> str:
    if company_type and company_type in VERB_BY_TYPE:
        return VERB_BY_TYPE[company_type]
    if builts:
        return "fabrique"
    return "propose"


# ---------------------------------------------------------------------------
# core_business — single FR phrase, ~50-90 chars
# ---------------------------------------------------------------------------


def core_business(
    *, defense_segment_main: Optional[str], built_products: list[str]
) -> str:
    seg_fr = _fr_segment(defense_segment_main)
    products_fr = _fr_products(built_products, limit=2)

    if seg_fr and products_fr:
        return f"{_smart_capitalize(seg_fr)} ({_ascii_join(products_fr)})"
    if seg_fr and seg_fr != "activité défense (à préciser)":
        return _smart_capitalize(seg_fr)
    if products_fr:
        return _smart_capitalize(_ascii_join(products_fr))
    return TO_ENRICH


# ---------------------------------------------------------------------------
# main_products_services — concise FR list
# ---------------------------------------------------------------------------


def main_products_services(
    *, built_products: list[str], sold_offerings: list[str], services: list[str]
) -> str:
    products_fr = _fr_products(built_products, limit=4)
    services_fr = _fr_offerings(services or sold_offerings, limit=2)

    chunks: list[str] = []
    if products_fr:
        chunks.append("Produits : " + ", ".join(products_fr))
    if services_fr:
        chunks.append("Services : " + ", ".join(services_fr))
    if not chunks:
        return TO_ENRICH
    return " · ".join(chunks)


# ---------------------------------------------------------------------------
# commercial_relevance_summary — 1 sentence "why they matter"
# ---------------------------------------------------------------------------


def commercial_relevance_summary(
    *, target_type_fr: str, buying_need_main: Optional[str],
    defense_segment_main: Optional[str],
    priority_level: Optional[str],
) -> str:
    seg_fr = _fr_segment(defense_segment_main) or "défense"
    priority_label = {
        "A+": "priorité maximale",
        "A": "priorité élevée",
        "B": "à qualifier",
        "C": "à surveiller",
        "D": "potentiel limité",
    }.get(priority_level or "D", "à qualifier")

    bn = (buying_need_main or "").strip()
    bn_low = bn.lower()
    has_real_need = bn and bn != TO_ENRICH and not bn_low.startswith("à enrichir")
    bn_fr = _fr_buying_need(bn) if has_real_need else None

    if target_type_fr == "Acheteur potentiel":
        if bn_fr:
            return f"Acheteur potentiel ({priority_label}) — sourcing probable de {bn_fr} pour ses programmes {seg_fr}."
        return f"Acheteur potentiel ({priority_label}) sur le segment {seg_fr}."
    if target_type_fr == "Fournisseur potentiel":
        return f"Fournisseur potentiel ({priority_label}) pour les primes {seg_fr}."
    if target_type_fr == "Partenaire industriel":
        return f"Partenaire industriel ({priority_label}) — co-développement plausible sur {seg_fr}."
    if target_type_fr == "Intégrateur potentiel":
        return f"Intégrateur potentiel ({priority_label}) sur {seg_fr}."
    if target_type_fr == "Distributeur potentiel":
        return f"Distributeur potentiel ({priority_label}) — canal export pour le portefeuille {seg_fr}."
    if target_type_fr == "Sous-traitant":
        return f"Sous-traitant ({priority_label}) du segment {seg_fr}."
    if target_type_fr == "Donneur d'ordre":
        return f"Donneur d'ordre ({priority_label}) — pilote des programmes {seg_fr}."
    if target_type_fr == "Concurrent":
        return f"Concurrent ({priority_label}) sur {seg_fr} — veille recommandée."
    return f"À qualifier — segment {seg_fr}, {priority_label}."


# ---------------------------------------------------------------------------
# company_pitch — 3-4 lines, structured
# ---------------------------------------------------------------------------

# Sentence templates per target_type (line 4)

LINE4_BY_TARGET: dict[str, str] = {
    "Acheteur potentiel": "Commercialement, elle peut être ciblée comme acheteur sur {need_or_segment}.",
    "Fournisseur potentiel": "Commercialement, elle peut être approchée comme fournisseur de sous-systèmes pour {segment}.",
    "Partenaire industriel": "Commercialement, elle peut être ciblée pour des partenariats industriels sur {segment}.",
    "Intégrateur potentiel": "Commercialement, elle peut être approchée comme intégrateur potentiel sur {segment}.",
    "Distributeur potentiel": "Commercialement, elle peut servir de canal de distribution / représentation sur {segment}.",
    "Sous-traitant": "Commercialement, elle peut intervenir comme sous-traitant industriel sur {segment}.",
    "Donneur d'ordre": "Commercialement, elle peut être approchée comme donneur d'ordre sur {segment}.",
    "Concurrent": "Commercialement, elle constitue un concurrent direct à surveiller sur {segment}.",
}


def company_pitch(
    *, account_name: str, country_fr: Optional[str],
    defense_segment_main: Optional[str], defense_segments_secondary_list: list[str],
    built_products: list[str], sold_offerings: list[str], services: list[str],
    company_type: Optional[str], target_type_fr: str,
    buying_need_main: Optional[str],
) -> str:
    """Compose a 3-4 line professional mini-pitch.

    Lines drop out if their inputs are too thin — but we still emit the lines
    we can support, never fabricating filler.
    """
    seg_fr = _fr_segment(defense_segment_main)
    products_fr = _fr_products(built_products, limit=3)
    services_fr = _fr_offerings(services or sold_offerings, limit=2)
    secondary_fr = [_fr_segment(c) for c in defense_segments_secondary_list[:3]]
    secondary_fr = [s for s in secondary_fr if s]

    # If we have nothing useful at all, return a single fallback line.
    if not seg_fr and not products_fr:
        return f"{account_name} — {TO_ENRICH}"

    # ---- Line 1: identity + activity ----
    # ``country_fr`` is a noun (e.g. "Lituanie") — phrase as "basée en X" to
    # avoid needing a separate adjective lookup table.
    country_part = f"une société basée en {country_fr}" if country_fr else "une société"
    if seg_fr and seg_fr != "activité défense (à préciser)":
        line1 = f"{account_name} est {country_part}, spécialisée dans {seg_fr}."
    elif products_fr:
        line1 = (
            f"{account_name} est {country_part}, active sur le marché de la défense "
            f"({_ascii_join(products_fr[:2])})."
        )
    else:
        line1 = f"{account_name} est {country_part}, active sur le marché de la défense."

    # ---- Line 2: products / services ----
    verb = _verb_for(company_type, built_products)
    parts = []
    if products_fr:
        parts.append(_ascii_join(products_fr))
    if services_fr:
        parts.append("et propose " + ", ".join(services_fr))
    if parts:
        line2 = f"Elle {verb} {parts[0]}"
        if len(parts) > 1:
            line2 += " " + parts[1]
        line2 += "."
    else:
        line2 = ""  # skip

    # ---- Line 3: cores of business (segment + secondary) ----
    cores = []
    if seg_fr and seg_fr != "activité défense (à préciser)":
        cores.append(seg_fr)
    cores += [s for s in secondary_fr if s and s not in cores]
    cores = cores[:4]
    if cores:
        line3 = f"Ses cœurs de métier couvrent {_ascii_join(cores)}."
    else:
        line3 = ""

    # ---- Line 4: commercial angle ----
    line4_template = LINE4_BY_TARGET.get(
        target_type_fr,
        "Commercialement, l'angle d'approche reste à qualifier en discovery call."
    )
    bn = (buying_need_main or "").strip()
    bn_ok = bn and bn != TO_ENRICH and not bn.lower().startswith("à enrichir")
    bn_fr_line4 = _fr_buying_need(bn) if bn_ok else None
    need_or_segment = bn_fr_line4 or (seg_fr or "ce segment")
    line4 = line4_template.format(
        need_or_segment=need_or_segment,
        segment=seg_fr or "ce segment",
    )

    return "\n".join(l for l in (line1, line2, line3, line4) if l)
