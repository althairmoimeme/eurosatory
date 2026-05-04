"""English defense taxonomy used by the intelligence layer.

Distinct from the French commercial taxonomy in ``classifier.py`` — that one
labels exhibitors for the sales team's existing categories, this one drives the
buying-need / partner / target inference and the ``defense_commercial_score``.

A company can belong to several categories.  Each category lists a few
keyword groups; we score by counting hit groups.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

DEFENSE_TAXONOMY: dict[str, list[list[str]]] = {
    "Land defense": [
        [r"\bland (defen[cs]e|forces?|warfare|systems?)\b", r"\bground forces?\b", r"\barmy\b", r"\barm[ée]e de terre\b"],
    ],
    "Air defense": [
        [r"\bair defen[cs]e\b", r"\banti[\-_ ]?aircraft\b", r"\bSAM\b", r"\bsurface[\-_ ]to[\-_ ]air\b", r"\bMANPADS\b"],
    ],
    "Naval defense": [
        [r"\bnaval (defen[cs]e|forces?|systems?)\b", r"\bnav(y|ies)\b", r"\bmar[iy](ne|time)\b", r"\bsubmarine\b", r"\bship\b"],
    ],
    "Homeland security": [
        [r"\bhomeland security\b", r"\bborder (control|security)\b", r"\binternal security\b", r"\blaw enforcement\b", r"\bpolice\b", r"\bgendarmerie\b"],
    ],
    "Cybersecurity": [
        [r"\bcyber( |-)?security\b", r"\bcyber( |-)?defen[cs]e\b", r"\bSOC\b", r"\bSIEM\b", r"\bzero[\-_ ]?trust\b", r"\bencryption\b", r"\bchiffrement\b"],
    ],
    "Intelligence / ISR": [
        [r"\bISR\b", r"\bintelligence (gathering|systems?)\b", r"\brenseignement\b", r"\bSIGINT\b", r"\bIMINT\b", r"\bELINT\b", r"\breconnaissance\b"],
    ],
    "C4ISR": [
        [r"\bC2\b", r"\bC4\b", r"\bC4I\b", r"\bC4ISR\b", r"\bcommand and control\b", r"\bbattle management\b"],
    ],
    "UAV / drones": [
        [r"\bUAV\b", r"\bUAS\b", r"\bdrone(s)?\b", r"\bunmanned aerial\b", r"\bRPAS\b", r"\bloitering munition\b"],
    ],
    "Counter-UAV": [
        [r"\banti[\-_ ]?drone(s)?\b", r"\bcounter[\-_ ]?(UAV|UAS)\b", r"\bC[\-_ ]?UAS\b", r"\bjamm(er|ing)\b", r"\bRF disrupt"],
    ],
    "Armored vehicles": [
        [r"\barmou?red vehicle", r"\bAPC\b", r"\bIFV\b", r"\bMRAP\b", r"\btank(s)?\b", r"\bchar(s)? de combat\b"],
    ],
    "Weapons": [
        [r"\bweapon system", r"\bsmall arms\b", r"\bfirearm", r"\bartiller(y|ie)\b", r"\bcannon", r"\bmissile launcher", r"\bmortar"],
    ],
    "Ammunition": [
        [r"\bammunition\b", r"\bmunitions?\b", r"\bcartridge", r"\bshell(s)?\b", r"\bexplosi(ves?|fs?)\b", r"\bwarhead"],
    ],
    "Soldier systems": [
        [r"\bsoldier (system|equipment|kit|gear)", r"\bdismounted soldier\b", r"\béquipement(s)? individuel(s)?\b", r"\bcombat gear\b", r"\bhelmet", r"\buniform"],
    ],
    "Ballistic protection": [
        [r"\bballistic protection\b", r"\bbody armou?r\b", r"\barmou?r plate", r"\bblindage\b"],
    ],
    "Optics / optronics": [
        [r"\boptronic", r"\boptics?\b", r"\bnight vision\b", r"\bvision nocturne\b", r"\bthermal imag", r"\bsight(s)?\b", r"\bgoggles?\b"],
    ],
    "Communications": [
        [r"\btactical radio", r"\bradio communication", r"\bSDR\b", r"\bsoftware defined radio\b", r"\bsatcom\b", r"\bHF/VHF\b"],
    ],
    "Electronic warfare": [
        [r"\belectronic warfare\b", r"\bEW\b", r"\bguerre [ée]lectronique\b", r"\bjamming\b", r"\bspoofing\b"],
    ],
    "Radar / sensors": [
        [r"\bradar(s)?\b", r"\bsensor(s)?\b", r"\blidar\b", r"\bsonar\b", r"\bcapteurs?\b"],
    ],
    "AI / data": [
        [r"\bartificial intelligence\b", r"\bmachine learning\b", r"\bdeep learning\b", r"\bAI[\-_ ]?powered\b", r"\bbig data\b", r"\bdata fusion\b"],
    ],
    "Simulation / training": [
        [r"\bsimulation\b", r"\btraining system", r"\bsimulator(s)?\b", r"\blive virtual constructive\b", r"\bLVC\b"],
    ],
    "Logistics / MRO": [
        [r"\bMRO\b", r"\bmaintenance(,? repair(,? and)? overhaul)?\b", r"\bsustainment\b", r"\blogistics?\b", r"\bsupply chain\b", r"\bMCO\b"],
    ],
    "Engineering services": [
        [r"\bengineering services\b", r"\bsystems? integration\b", r"\bbureau d'[ée]tudes?\b", r"\bing[ée]nierie\b"],
    ],
    "Industrial subcontracting": [
        [r"\bsubcontract(ing|or)\b", r"\bsous[\-_ ]?traitance\b", r"\bcontract manufacturing\b", r"\bOEM partner\b", r"\bjob[\-_ ]?shop\b"],
    ],
    "Dual-use technology": [
        [r"\bdual[\-_ ]?use\b", r"\bcivil(ian)?\s+(?:and|&|/)\s+military\b", r"\bcommercial off[\-_ ]the[\-_ ]shelf\b", r"\bCOTS\b"],
    ],
    "Export / distribution": [
        [r"\bexport (markets?|sales?)\b", r"\bagent / reseller\b", r"\bdistributor\b", r"\brepresentative\b", r"\bdealer\b"],
    ],
    "NRBC / CBRN": [
        [r"\bCBRN\b", r"\bNRBC\b", r"\bchemical biological", r"\bradiological\b"],
    ],
    "Space / satellite": [
        [r"\bsatellite\b", r"\bspace (system|asset|domain|situational)\b", r"\bspatial\b", r"\bSSA\b"],
    ],
}

OTHER = "Other"


@dataclass
class DefenseLabel:
    label: str
    confidence: float
    matches: list[str]


_COMPILED = {
    label: [[re.compile(p, re.IGNORECASE) for p in group] for group in groups]
    for label, groups in DEFENSE_TAXONOMY.items()
}


def classify_defense(text: str) -> list[DefenseLabel]:
    if not text:
        return []
    out: list[DefenseLabel] = []
    for label, groups in _COMPILED.items():
        groups_hit = 0
        sample: list[str] = []
        for group in groups:
            for pat in group:
                m = pat.search(text)
                if m:
                    groups_hit += 1
                    if len(sample) < 3:
                        sample.append(m.group(0))
                    break
        if groups_hit:
            confidence = min(0.55 + 0.2 * groups_hit, 0.95)
            out.append(DefenseLabel(label=label, confidence=round(confidence, 2), matches=sample))
    return out


def classify_defense_labels(text: str) -> list[str]:
    return [d.label for d in classify_defense(text)] or [OTHER]


def is_defense_relevant(labels: Iterable[str]) -> bool:
    label_set = set(labels)
    return bool(label_set - {OTHER, "Dual-use technology", "Export / distribution"})


# ---------------------------------------------------------------------------
# Anchor mapping — what built_product (from intelligence.py BUILT_PRODUCTS)
# must be present for a defense category to be considered *primary*, as opposed
# to a passing keyword mention.  An empty list means the category is always
# valid (e.g. Logistics / MRO can apply even without a manufactured product).
# ---------------------------------------------------------------------------

CATEGORY_ANCHORS: dict[str, set[str]] = {
    "Counter-UAV": {"Counter-UAV systems"},
    "UAV / drones": {"UAV / drones"},
    "Cybersecurity": {"Cybersecurity solutions", "Software / Platforms", "AI platforms"},
    "AI / data": {"AI platforms", "Software / Platforms"},
    "Electronic warfare": {"Tactical radios", "Sensors", "Radars", "Communications networks"},
    "Intelligence / ISR": {"Sensors", "Optronics / Optics", "Radars", "UAV / drones", "Satellites / Space"},
    "C4ISR": {"Software / Platforms", "Communications networks", "Tactical radios"},
    "Radar / sensors": {"Radars", "Sensors"},
    "Optics / optronics": {"Optronics / Optics"},
    "Communications": {"Tactical radios", "Communications networks"},
    "Armored vehicles": {"Vehicles"},
    "Weapons": {"Weapons"},
    "Ammunition": {"Ammunition"},
    "Soldier systems": {"Soldier equipment", "Optronics / Optics", "Ballistic protection"},
    "Ballistic protection": {"Ballistic protection"},
    "Simulation / training": {"Simulators / Training"},
    "Logistics / MRO": set(),  # service-only category — no manufacturing anchor
    "Engineering services": set(),
    "Industrial subcontracting": {"Mechanical parts", "Electronic components", "Composite materials"},
    "Naval defense": {"Naval systems", "Sensors", "Radars"},
    "Air defense": {"Radars", "Weapons", "Sensors"},
    "Land defense": {"Vehicles", "Weapons", "Soldier equipment"},
    "Homeland security": set(),  # cross-cutting, can apply to many product types
    "Space / satellite": {"Satellites / Space", "Engines / propulsion"},
    "NRBC / CBRN": {"NRBC / CBRN"},
    "Dual-use technology": set(),
    "Export / distribution": set(),
    OTHER: set(),
}


def filter_defense_by_anchors(
    candidate_labels: Iterable[str],
    built_products: Iterable[str],
) -> list[str]:
    """Drop categories whose anchor built_product is not present.

    A category with an empty anchor set (service-style: Logistics / MRO,
    Homeland security, Engineering services, Dual-use, Export / distribution)
    always passes — those don't depend on a manufactured product.
    """
    builts = set(built_products or [])
    out: list[str] = []
    seen: set[str] = set()
    for label in candidate_labels or []:
        if label in seen:
            continue
        seen.add(label)
        anchors = CATEGORY_ANCHORS.get(label)
        if not anchors:  # always valid (service category) or unknown
            out.append(label)
            continue
        if anchors & builts:
            out.append(label)
        # else: keyword-only mention, drop it
    return out or [OTHER]
