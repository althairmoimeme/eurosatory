"""Commercial taxonomy classifier.

Maps an exhibitor's free-text presentation, business areas and Finderr
``CategoriesLabels`` onto the Eurosatory commercial taxonomy supplied by the
sales team.  An exhibitor can belong to several taxonomy labels.

Approach
--------
* For each taxonomy label we keep a list of multilingual keyword regexes.
* We score a label against the concatenated text of (presentation +
  one-liner + meta description + Finderr category labels) by counting matches.
* Labels with at least one strong-keyword hit are emitted with a confidence
  proportional to the number and quality of matches (capped at 0.95).
* If no label matches we tag the exhibitor as ``Autre`` so the sales team can
  still surface it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Taxonomy: label -> keyword groups. Each group is a list of regex patterns
# (case-insensitive). A label is awarded one point per matched group.
TAXONOMY: dict[str, list[list[str]]] = {
    "Défense terrestre": [
        [r"\bdefense terrestre\b", r"\bland (defen[cs]e|forces)\b", r"\barmy\b", r"\barmée de terre\b"],
        [r"\bcombat vehicle", r"\bvéhicule(s)? de combat", r"\binfantry"],
    ],
    "Sécurité intérieure": [
        [r"\bhomeland security\b", r"\bsécurité intérieure\b", r"\binternal security\b",
         r"\bborder (control|security)\b", r"\blaw enforcement\b"],
    ],
    "Cybersécurité": [
        [r"\bcyber( |-)?security\b", r"\bcybersécurité\b", r"\bcyber( |-)?defen[cs]e\b",
         r"\bSOC\b", r"\bSIEM\b", r"\bencryption\b", r"\bchiffrement\b"],
    ],
    "Drones / UAV": [
        [r"\bUAV(s)?\b", r"\bUAS\b", r"\bdrone(s)?\b", r"\bunmanned aerial\b", r"\bRPAS\b"],
    ],
    "Anti-drones": [
        [r"\banti(-| )?drone(s)?\b", r"\bC( |-)?UAS\b", r"\bcounter(-| )?UAV\b",
         r"\bRF disrupt", r"\bjamm(er|ing)\b", r"\bdrone defen[cs]e\b"],
    ],
    "Véhicules blindés": [
        [r"\barmou?red vehicle", r"\bAPC\b", r"\bIFV\b", r"\bMRAP\b", r"\btank(s)?\b",
         r"\bchar(s)? de combat\b", r"\bblind[ée]"],
    ],
    "Armement": [
        [r"\bweapon system", r"\bsmall arms\b", r"\bfirearm", r"\bartillerie\b",
         r"\bartillery\b", r"\bcannon", r"\bmissile launcher"],
    ],
    "Munitions": [
        [r"\bammunition\b", r"\bmunitions?\b", r"\bcartridge", r"\bshell(s)?\b",
         r"\bexplos(ifs?|ives?)\b", r"\bwarhead"],
    ],
    "Optique / vision nocturne": [
        [r"\bnight vision\b", r"\bvision nocturne\b", r"\bthermal imag", r"\bimagerie thermique\b",
         r"\bsight(s)?\b", r"\bgoggles?\b", r"\bjumelles?\b", r"\boptronic"],
    ],
    "Communication / radio": [
        [r"\btactical radio", r"\bradio communication", r"\bSDR\b", r"\bsoftware defined radio\b",
         r"\bcommunication tactique\b", r"\bnetwork-centric\b", r"\bsatcom\b", r"\bHF\b VHF\b"],
    ],
    "Intelligence artificielle": [
        [r"\bartificial intelligence\b", r"\bintelligence artificielle\b", r"\bmachine learning\b",
         r"\bAI(-| )?powered\b", r"\bdeep learning\b", r"\bneural network"],
    ],
    "Simulation / formation": [
        [r"\bsimulation\b", r"\btraining system", r"\bsimulator(s)?\b", r"\bformation militaire\b",
         r"\blive virtual constructive\b", r"\bLVC\b"],
    ],
    "Logistique militaire": [
        [r"\bmilitary logistics\b", r"\blogistique militaire\b", r"\bsupply chain\b",
         r"\bMRO\b", r"\bsoutien op[ée]rationnel\b"],
    ],
    "Équipements individuels": [
        [r"\bindividual equipment", r"\bsoldier (system|equipment)", r"\bcombat gear\b",
         r"\béquipement(s)? individuel(s)?\b", r"\buniform", r"\bhelmet", r"\bcasque"],
    ],
    "Robotique": [
        [r"\brobot(ic)?s?\b", r"\bUGV(s)?\b", r"\bunmanned ground\b", r"\bautonomous (system|platform)"],
    ],
    "Surveillance / renseignement": [
        [r"\bISR\b", r"\bsurveillance\b", r"\brenseignement\b", r"\bintelligence gathering\b",
         r"\bSIGINT\b", r"\bIMINT\b", r"\breconnaissance\b"],
    ],
    "Protection balistique": [
        [r"\bballistic protection\b", r"\bbody armou?r\b", r"\bprotection balistique\b",
         r"\barmou?r plate", r"\bblindage\b", r"\bgilet pare( |-)?balle"],
    ],
    "Services / conseil": [
        [r"\bconsulting\b", r"\badvisory services\b", r"\bconseil\b", r"\bprofessional services\b"],
    ],
    "Maintenance / MCO": [
        [r"\bMCO\b", r"\bmaintien en condition op[ée]rationnelle\b", r"\bmaintenance services\b",
         r"\boverhaul\b", r"\bsustainment\b"],
    ],
    "Spatial / satellite": [
        [r"\bsatellite\b", r"\bspace (system|asset)\b", r"\bspatial\b", r"\bspace situational\b",
         r"\bSSA\b", r"\borbital\b"],
    ],
    "NRBC / CBRN": [
        [r"\bCBRN\b", r"\bNRBC\b", r"\bchemical biological", r"\bnuclear (defen[cs]e|protection)\b"],
    ],
    "Santé / médical militaire": [
        [r"\bcombat medical", r"\bmilitary medic", r"\bm[ée]dical (militaire|de combat)\b",
         r"\bcasualty care\b"],
    ],
}

OTHER_LABEL = "Autre"


@dataclass
class Classification:
    label: str
    confidence: float
    rationale: str


def _compile() -> dict[str, list[list[re.Pattern[str]]]]:
    return {
        label: [[re.compile(p, re.IGNORECASE) for p in group] for group in groups]
        for label, groups in TAXONOMY.items()
    }


_COMPILED = _compile()


def classify_text(corpus: str) -> list[Classification]:
    if not corpus:
        return []
    text = corpus
    out: list[Classification] = []
    for label, groups in _COMPILED.items():
        groups_hit = 0
        sample_matches: list[str] = []
        for group in groups:
            matched = False
            for pat in group:
                m = pat.search(text)
                if m:
                    matched = True
                    if len(sample_matches) < 3:
                        sample_matches.append(m.group(0))
                    break
            if matched:
                groups_hit += 1
        if groups_hit:
            confidence = min(0.55 + 0.2 * groups_hit, 0.95)
            out.append(
                Classification(
                    label=label,
                    confidence=round(confidence, 2),
                    rationale="matched: " + ", ".join(sample_matches),
                )
            )
    return out


def build_corpus(exhibitor_dict: dict, finderr_category_labels: Iterable[str] = ()) -> str:
    parts = [
        exhibitor_dict.get("company_name") or "",
        exhibitor_dict.get("one_liner") or "",
        exhibitor_dict.get("short_presentation") or "",
        exhibitor_dict.get("presentation") or "",
        " ".join(exhibitor_dict.get("business_areas") or []),
        " ".join(finderr_category_labels),
    ]
    enrichment_meta = exhibitor_dict.get("raw_enrichment_payload") or {}
    if isinstance(enrichment_meta, dict):
        parts.append(enrichment_meta.get("meta_description") or "")
    return " | ".join(p for p in parts if p)


def classify_exhibitor(
    exhibitor_dict: dict, finderr_category_labels: Iterable[str] = ()
) -> list[Classification]:
    corpus = build_corpus(exhibitor_dict, finderr_category_labels)
    found = classify_text(corpus)
    if not found:
        return [Classification(label=OTHER_LABEL, confidence=0.3, rationale="no keyword match")]
    return found


def keywords_from_corpus(corpus: str, max_n: int = 12) -> list[str]:
    """Extract a coarse keyword list (capitalised tokens / acronyms) for display."""
    if not corpus:
        return []
    tokens = re.findall(r"\b[A-Z][A-Za-z0-9\-]{2,}\b|\b[A-Z]{2,}\b", corpus)
    seen, out = set(), []
    for t in tokens:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        if len(t) <= 2 or t.lower() in {"the", "and", "for", "with"}:
            continue
        out.append(t)
        if len(out) >= max_n:
            break
    return out
