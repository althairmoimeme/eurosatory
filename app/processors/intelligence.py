"""Rule-based commercial intelligence extractor.

Inputs
------
A bag of texts (homepage, product/services pages, brochure PDFs) plus the
Eurosatory description.  We tokenize them and apply curated keyword
dictionaries to derive *what they build / sell / probably buy*, technologies,
target clients, markets, business model.

This module is fully **deterministic** (no LLM, no network).  It always runs
first.  The optional ``llm_intelligence`` module can refine its output and
add a sales-card / pitch / objections layer.

Design
------
* Each dictionary maps a canonical label ("Drones / UAV", "Optics", …) to a
  list of regex patterns.  We hit the corpus with all of them and keep the
  labels that match.
* For every label we keep the first 1–3 matched fragments as evidence.
* Buying-need inference is *derived from* what the company builds: a drone
  manufacturer probably buys batteries, motors, optronics, RF modules, etc.
  This mapping is encoded in ``BUYING_NEEDS_BY_BUILT``.
* Confidence = high when ≥3 distinct evidence matches across pages, medium
  when 1-2, low otherwise.

We never invent — if no evidence, the field stays empty and we add an entry
to ``fields_to_verify``.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# What they BUILD — categories of things companies actually manufacture
# ---------------------------------------------------------------------------

BUILT_PRODUCTS: dict[str, list[str]] = {
    "Vehicles": [r"\bvehicles?\b", r"\bAPC\b", r"\bIFV\b", r"\bMRAP\b", r"\btanks?\b", r"\bblind[ée]s?\b", r"\barmou?red\b", r"\bchassis\b"],
    "UAV / drones": [r"\bUAV(s)?\b", r"\bUAS\b", r"\bdrones?\b", r"\bunmanned aerial\b", r"\bRPAS\b", r"\bquadcopter\b"],
    "Counter-UAV systems": [r"\bcounter[\-_ ]?(UAV|UAS)\b", r"\banti[\-_ ]?drone\b", r"\bC[\-_ ]?UAS\b", r"\bRF disrupt"],
    "Sensors": [r"\bsensors?\b", r"\bcapteurs?\b", r"\bIMU\b", r"\bGNSS\b", r"\bgyroscope\b"],
    "Radars": [r"\bradars?\b"],
    "Optronics / Optics": [r"\boptronic", r"\boptics?\b", r"\bsights?\b", r"\bgoggles?\b", r"\bjumelles?\b", r"\bnight[\-_ ]?vision\b", r"\bthermal imag"],
    "Software / Platforms": [r"\bsoftware platform\b", r"\bsoftware\b", r"\bSaaS\b", r"\bedit(eur|or)\b", r"\bsoftware[\-_ ]?as[\-_ ]?a[\-_ ]?service\b"],
    "Tactical radios": [r"\btactical radios?\b", r"\bsoftware[\-_ ]?defined[\-_ ]?radios?\b", r"\bSDR\b", r"\bsatcom\b"],
    "Embedded systems": [r"\bembedded (systems?|electronics?|computing)\b", r"\bcomputing platforms?\b"],
    "Electronic components": [r"\belectronic components\b", r"\bPCBs?\b", r"\bASICs?\b", r"\bFPGAs?\b", r"\bsemiconductors?\b"],
    "Soldier equipment": [r"\bhelmets?\b", r"\bcasques?\b", r"\buniforms?\b", r"\bcombat gear\b", r"\bdismounted soldier\b"],
    "Weapons": [r"\bweapon systems?\b", r"\bsmall arms\b", r"\bfirearms?\b", r"\bcanon(s)?\b", r"\bartillery\b", r"\bmortars?\b"],
    "Ammunition": [r"\bammunitions?\b", r"\bmunitions?\b", r"\bcartridges?\b", r"\bshells?\b", r"\bwarheads?\b"],
    "Ballistic protection": [r"\bballistic (protection|panels?|plates?)\b", r"\bbody armou?r\b", r"\barmou?r plates?\b"],
    "Simulators / Training": [r"\bsimulators?\b", r"\btraining systems?\b", r"\blive virtual constructive\b", r"\bLVC\b", r"\bvirtual reality\b"],
    "AI platforms": [r"\bAI platform", r"\bartificial intelligence platform", r"\bdata fusion (platform|engine)\b"],
    "Robotics / UGV": [r"\bUGV(s)?\b", r"\bunmanned ground vehicles?\b", r"\brobots?\b", r"\bautonomous (platforms?|systems?)\b"],
    "Cybersecurity solutions": [r"\bcyber( |-)?security (solution|product|tool)", r"\bSIEM\b", r"\bSOC platform\b", r"\bencryption (module|appliance)\b"],
    "Logistics equipment": [r"\blogistics (equipment|systems?)\b", r"\bcargo handling\b", r"\bcontainer\b"],
    "Energy / batteries": [r"\bbatteries?\b", r"\bpower (supply|systems?)\b", r"\bUPS\b", r"\benergy storage\b"],
    "Composite materials": [r"\bcomposite materials?\b", r"\bcarbon fib(re|er)\b", r"\bkevlar\b", r"\baramide\b"],
    "Mechanical parts": [r"\bmechanical parts?\b", r"\bprecision machining\b", r"\bmachining\b", r"\businage\b"],
    "Engines / propulsion": [r"\bengines?\b", r"\bpropulsion\b", r"\bturbines?\b", r"\bmotors?\b"],
    "Communications networks": [r"\bnetwork(s)?\b", r"\b5G\b", r"\bmesh network", r"\btactical network"],
    "Satellites / Space": [r"\bsatellites?\b", r"\bspace systems?\b", r"\bground stations?\b"],
    "Naval systems": [r"\bnaval systems?\b", r"\bships?\b", r"\bsubmarines?\b", r"\bsonars?\b"],
    "NRBC / CBRN": [r"\bCBRN\b", r"\bNRBC\b", r"\bchemical (detection|protection)\b", r"\bgas mask"],
    "Medical / health": [r"\bcombat medical\b", r"\bm[ée]dical de combat\b", r"\bmedical kits?\b"],
}

# ---------------------------------------------------------------------------
# What they SELL — verbs/phrases identifying offering type
# ---------------------------------------------------------------------------

OFFERING_VERBS: dict[str, list[str]] = {
    "Finished products": [r"\bwe (manufacture|produce|design and (build|manufacture)|build)\b", r"\bproduct (range|portfolio)\b", r"\bnos? produits?\b"],
    "Sub-systems / Components": [r"\bsub[\-_ ]?systems?\b", r"\bcomponents?\b", r"\bmodules?\b"],
    "Integration services": [r"\bsystems? integration\b", r"\bint[ée]gration de syst[èe]mes?\b", r"\bintegrate(s)?\b"],
    "Maintenance / MRO": [r"\bMRO\b", r"\bmaintenance services\b", r"\boverhaul\b", r"\bsustainment\b", r"\bMCO\b"],
    "Engineering / consulting": [r"\bconsulting\b", r"\bconseil\b", r"\bengineering services\b", r"\bbureau d'[ée]tudes?\b"],
    "Training": [r"\btraining (services?|programs?|courses?)\b", r"\bformation\b"],
    "Software licenses": [r"\bsoftware licen(s|c)e", r"\bsubscription\b", r"\bSaaS\b"],
    "Cloud / data services": [r"\bcloud (services?|hosting|platform)\b", r"\bdata services\b", r"\bmanaged services\b"],
    "Operational support": [r"\bin[\-_ ]?service support\b", r"\boperational support\b", r"\bsupport op[ée]rationnel\b"],
    "Distribution / agency": [r"\bdistributor\b", r"\bagent / reseller\b", r"\brepresentative\b"],
    "Sub-contracting": [r"\bsubcontracting\b", r"\bsous[\-_ ]?traitance\b", r"\bcontract manufacturing\b"],
    "Certification / testing": [r"\btesting (services?|laboratory)\b", r"\bcertification\b", r"\bqualification\b"],
}

BUSINESS_MODEL_HINTS: list[tuple[str, list[str]]] = [
    ("OEM", [r"\boriginal equipment manufacturer\b", r"\bOEM\b", r"\bwe (manufacture|produce|design and build)"]),
    ("System integrator", [r"\bsystems? integrator\b", r"\bint[ée]grateur de syst[èe]mes?\b"]),
    ("Distributor / reseller", [r"\bdistributor\b", r"\breseller\b", r"\brepresentative\b", r"\bagent\b"]),
    ("Sub-contractor", [r"\bsubcontract(ing|or)\b", r"\bsous[\-_ ]?traitance\b"]),
    ("Software / SaaS vendor", [r"\bSaaS\b", r"\bsoftware (vendor|company|publisher|editor|editeur)\b"]),
    ("Engineering services", [r"\bengineering services\b", r"\bbureau d'[ée]tudes?\b"]),
    ("Equipment manufacturer", [r"\bequipment manufacturer\b", r"\b[ée]quipementier\b"]),
    ("Consulting firm", [r"\bconsulting firm\b", r"\badvisory services\b", r"\bcabinet de conseil\b"]),
    ("Materials / parts supplier", [r"\bmaterials supplier\b", r"\bparts supplier\b", r"\braw materials\b"]),
]

COMPANY_TYPE_HINTS: list[tuple[str, list[str]]] = [
    ("Manufacturer", [r"\bmanufactur(er|ing)\b", r"\bproduction\b", r"\bproduce(r|s)\b"]),
    ("Software / SaaS", [r"\bsoftware\b", r"\bSaaS\b", r"\bplatform\b", r"\bsoftware editor\b"]),
    ("Service company", [r"\bservices?\b", r"\bconsulting\b", r"\bMRO\b", r"\bmaintenance\b"]),
    ("Distributor", [r"\bdistributor\b", r"\breseller\b", r"\bagent\b"]),
    ("Research / lab", [r"\bresearch institute\b", r"\bR&D\b", r"\bnational laboratory\b", r"\binstitut de recherche\b"]),
    ("Engineering firm", [r"\bengineering firm\b", r"\bbureau d'[ée]tudes?\b"]),
]

# ---------------------------------------------------------------------------
# Technologies — recognisable techno keywords
# ---------------------------------------------------------------------------

TECHNOLOGIES: dict[str, list[str]] = {
    "Artificial intelligence": [r"\bartificial intelligence\b", r"\bAI[\-_ ]?powered\b", r"\bmachine learning\b", r"\bdeep learning\b"],
    "Computer vision": [r"\bcomputer vision\b", r"\bobject detection\b", r"\bimage recognition\b"],
    "Edge computing": [r"\bedge computing\b", r"\bedge AI\b"],
    "Cloud": [r"\bcloud\b", r"\bAWS\b", r"\bAzure\b", r"\bGCP\b"],
    "5G / wireless": [r"\b5G\b", r"\bLTE\b", r"\bwireless\b"],
    "Cryptography": [r"\bcryptograph(y|ie)\b", r"\bencryption\b", r"\bchiffrement\b"],
    "RF / microwave": [r"\bRF\b", r"\bmicrowave\b", r"\bantennas?\b"],
    "GNSS / GPS": [r"\bGNSS\b", r"\bGPS\b"],
    "Lidar / Laser": [r"\blidar\b", r"\blasers?\b"],
    "Thermal imaging": [r"\bthermal imag"],
    "Composites": [r"\bcomposite\b", r"\bcarbon fib(re|er)\b"],
    "Additive manufacturing": [r"\b3D[\-_ ]?print(ing|ed)\b", r"\badditive manufacturing\b"],
    "Robotics / autonomy": [r"\brobots?\b", r"\bautonom(y|ous)\b"],
    "Cybersecurity": [r"\bcybersecurity\b", r"\bcyberdefense\b", r"\bSIEM\b"],
    "Simulation": [r"\bsimulation\b", r"\bdigital twin\b"],
    "Big data / analytics": [r"\bbig data\b", r"\bdata analytics?\b", r"\bdata science\b"],
}

# ---------------------------------------------------------------------------
# Target clients & markets
# ---------------------------------------------------------------------------

TARGET_CLIENTS: dict[str, list[str]] = {
    "Land forces": [r"\barmy\b", r"\barm[ée]e de terre\b", r"\bground forces?\b", r"\bland forces?\b"],
    "Air forces": [r"\bair force\b", r"\barm[ée]e de l'air\b"],
    "Navy": [r"\bnavy\b", r"\bnaval forces?\b", r"\bmarine nationale\b"],
    "Special forces": [r"\bspecial forces?\b", r"\bforces sp[ée]ciales\b", r"\bSOF\b"],
    "Police / law enforcement": [r"\bpolice\b", r"\blaw enforcement\b", r"\bgendarmerie\b"],
    "Border / customs": [r"\bborder (control|guards?|security)\b", r"\bcustoms\b", r"\bdouanes?\b"],
    "Civil security / fire": [r"\bcivil security\b", r"\bs[ée]curit[ée] civile\b", r"\bfire (services|brigade|fighting)\b", r"\bpompiers?\b"],
    "Intelligence agencies": [r"\bintelligence (agency|services?)\b", r"\bagence de renseignement\b"],
    "Defense ministries": [r"\bministry of defen[cs]e\b", r"\bMOD\b", r"\bDGA\b", r"\bDoD\b", r"\bDLA\b"],
    "Critical infrastructure": [r"\bcritical infrastructure\b", r"\butilities\b", r"\bairports?\b", r"\bports\b"],
    "Aerospace primes": [r"\baerospace\b", r"\bairbus\b", r"\bboeing\b", r"\blockheed\b", r"\braytheon\b", r"\bBAE\b", r"\bsystem primes?\b"],
}

MARKETS: list[str] = [
    "Europe", "France", "Germany", "United Kingdom", "United States", "Asia", "Middle East",
    "Africa", "NATO", "Eastern Europe", "Latin America", "Asia-Pacific", "GCC", "India",
]
MARKET_REGEX = {m: re.compile(r"\b" + re.escape(m) + r"\b", re.IGNORECASE) for m in MARKETS}

# ---------------------------------------------------------------------------
# Buying-need inference — derived from what they build/sell
# ---------------------------------------------------------------------------

# Each entry: built_label -> list of likely supplier categories (canonical)
BUYING_NEEDS_BY_BUILT: dict[str, list[str]] = {
    "Vehicles": ["Engines / propulsion", "Composite materials", "Mechanical parts", "Sensors", "Communications networks", "Ballistic protection", "Optronics / Optics"],
    "UAV / drones": ["Energy / batteries", "Engines / propulsion", "Sensors", "Optronics / Optics", "Communications networks", "Composite materials", "Embedded systems"],
    "Counter-UAV systems": ["Radars", "Sensors", "RF / microwave", "Embedded systems", "AI platforms", "Communications networks"],
    "Sensors": ["Electronic components", "Embedded systems", "Composite materials"],
    "Radars": ["Electronic components", "Mechanical parts", "Composite materials", "Embedded systems"],
    "Optronics / Optics": ["Electronic components", "Composite materials", "Mechanical parts"],
    "Software / Platforms": ["Cloud / data services", "Cybersecurity solutions", "AI platforms"],
    "Tactical radios": ["Electronic components", "RF / microwave", "Embedded systems", "Cybersecurity solutions"],
    "Embedded systems": ["Electronic components", "Cybersecurity solutions"],
    "Electronic components": ["Industrial subcontracting / machining", "Materials"],
    "Soldier equipment": ["Composite materials", "Textile / industrial fabrics", "Optronics / Optics", "Energy / batteries"],
    "Weapons": ["Mechanical parts", "Composite materials", "Optronics / Optics", "Ammunition components"],
    "Ammunition": ["Energetic materials", "Mechanical parts", "Packaging"],
    "Ballistic protection": ["Composite materials", "Textile / industrial fabrics", "Certification / testing"],
    "Simulators / Training": ["Software", "AI platforms", "Hardware (display, motion platforms)"],
    "AI platforms": ["Cloud / data services", "Compute hardware (GPU)", "Annotation / data services"],
    "Robotics / UGV": ["Electronic components", "Sensors", "Energy / batteries", "Engines / propulsion", "Composite materials"],
    "Cybersecurity solutions": ["Cloud / data services", "Hardware appliances", "Threat intelligence feeds"],
    "Logistics equipment": ["Mechanical parts", "Materials", "Communications networks"],
    "Energy / batteries": ["Materials (Li-ion, electrolytes)", "Mechanical parts"],
    "Composite materials": ["Raw materials (carbon fiber, resins)", "Curing equipment"],
    "Mechanical parts": ["Raw materials (steel, aluminium)", "Industrial subcontracting / machining"],
    "Engines / propulsion": ["Mechanical parts", "Materials (high temperature alloys)", "Embedded systems"],
    "Communications networks": ["RF / microwave components", "Embedded systems", "Cybersecurity solutions"],
    "Satellites / Space": ["Composite materials", "Sensors", "Embedded systems", "Launch services"],
    "Naval systems": ["Sensors", "Communications networks", "Composite materials", "Engines / propulsion"],
    "NRBC / CBRN": ["Sensors", "Filtration materials", "Personal protective equipment"],
    "Medical / health": ["Medical devices certification", "Pharmaceuticals", "Logistics"],
}

# Universal needs every defense exhibitor likely has
UNIVERSAL_BUYING_NEEDS = [
    "Industrial subcontracting / machining",
    "Quality / certification services (ITAR, NATO AQAP, ISO 9100)",
    "Export financing / insurance",
    "Logistics & freight forwarding",
    "Cybersecurity audits",
    "Marketing / event representation",
]


# ---------------------------------------------------------------------------
# Output container
# ---------------------------------------------------------------------------


@dataclass
class IntelligenceResult:
    activity_summary: Optional[str] = None
    built_products: list[str] = field(default_factory=list)
    sold_offerings: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    target_clients: list[str] = field(default_factory=list)
    markets_served: list[str] = field(default_factory=list)
    business_model: Optional[str] = None
    company_type: Optional[str] = None
    probable_buying_needs: list[str] = field(default_factory=list)
    buying_need_confidence: str = "low"
    buying_need_reasoning: Optional[str] = None
    field_sources: dict[str, str] = field(default_factory=dict)
    field_confidence: dict[str, str] = field(default_factory=dict)
    fields_to_verify: list[str] = field(default_factory=list)
    extraction_method: str = "rules"
    evidence: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hits(patterns: list[str], text: str) -> list[str]:
    out: list[str] = []
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            out.append(m.group(0))
    return out


def _label_hits(
    catalogue: dict[str, list[str]],
    pages: list[tuple[str, str, str]],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """For each label in ``catalogue``, count regex hits across all pages.

    Returns (label -> evidence list, label -> source_url)."""
    evidence: dict[str, list[str]] = defaultdict(list)
    source_for: dict[str, str] = {}
    for kind, url, text in pages:
        if not text:
            continue
        for label, patterns in catalogue.items():
            hits = _hits(patterns, text)
            if hits:
                evidence[label].extend(hits[:3])
                source_for.setdefault(label, url)
    # de-dup evidence
    return ({k: list(dict.fromkeys(v))[:5] for k, v in evidence.items()}, source_for)


def _hit_first_label(
    hints: list[tuple[str, list[str]]], text: str
) -> tuple[Optional[str], Optional[str]]:
    for label, patterns in hints:
        for p in patterns:
            m = re.search(p, text, flags=re.IGNORECASE)
            if m:
                return label, m.group(0)
    return None, None


def _confidence(evidence_count: int) -> str:
    if evidence_count >= 3:
        return "high"
    if evidence_count >= 1:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


_FUNCTION_WORDS = {
    "the", "a", "an", "we", "our", "is", "are", "was", "were", "be", "been",
    "of", "to", "in", "on", "for", "with", "and", "by", "as", "at", "from",
    "that", "this", "their", "its", "they", "us", "have", "has", "company",
}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"\u201C\u00AB])")


def _looks_like_keyword_stuffing(text: str) -> bool:
    """A summary that's mostly comma-separated noun phrases doesn't read as prose.

    Two heuristics — both must trigger to flag as stuffing:
    * ratio of function words to total words is very low (< 0.10), AND
    * very few sentence-ending punctuation marks vs commas (commas/periods > 3).

    Genuine short prose (e.g. "NT Service produces and delivers RF disruption
    systems") has ~0.15 function words but is dominated by periods, not
    commas — we let it through.
    """
    if not text:
        return True
    words = re.findall(r"[A-Za-z]+", text.lower())
    if len(words) < 8:
        return True
    fw = sum(1 for w in words if w in _FUNCTION_WORDS)
    fw_ratio = fw / len(words)
    if fw_ratio >= 0.10:
        return False
    # Below 0.10 — could still be prose with many proper nouns. Check punctuation.
    commas = text.count(",")
    periods = text.count(".") + text.count("!") + text.count("?")
    if periods == 0:
        return True
    return commas / periods > 3


def _pick_prose_summary(
    pages: list[tuple[str, str, str]], company_name_hint: Optional[str] = None
) -> Optional[str]:
    """Pick up to three usable prose sentences from the crawled pages.

    Prefers sentences from homepage / about, mentioning the company or a defense
    keyword.  Skips sentences that are too short, too long, or clearly menu /
    cookie / nav text.
    """
    DEFENSE_HINTS = re.compile(
        r"\b(defen[cs]e|defense|military|security|aerospace|naval|tactical|"
        r"surveillance|reconnaissance|combat|missile|radar|drone|UAV|cyber|"
        r"protection|intelligence|simulation)\b",
        re.IGNORECASE,
    )
    NAV_NOISE = re.compile(
        r"\b(cookie|privacy|gdpr|menu|toggle|navigation|copyright|all rights "
        r"reserved|sign up|subscribe|newsletter|search|home page|loading)\b",
        re.IGNORECASE,
    )
    company_re = (
        re.compile(re.escape(company_name_hint), re.IGNORECASE)
        if company_name_hint else None
    )

    candidates: list[tuple[int, str]] = []  # (priority, sentence)
    for kind, _url, text in pages:
        if not text or kind == "eurosatory":
            continue
        prio_bonus = 0 if kind in ("homepage", "about", "industry", "defense") else 1
        for sent in _SENTENCE_SPLIT.split(text)[:80]:
            sent = " ".join(sent.split())
            if not sent or len(sent) < 60 or len(sent) > 320:
                continue
            if NAV_NOISE.search(sent):
                continue
            score = 5
            if company_re and company_re.search(sent):
                score -= 2
            if DEFENSE_HINTS.search(sent):
                score -= 1
            candidates.append((score + prio_bonus, sent))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    seen = set()
    picked: list[str] = []
    for _, sent in candidates:
        key = sent.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        picked.append(sent)
        if len(picked) == 3:
            break
    if not picked:
        return None
    return " ".join(picked)


def extract_intelligence(
    pages: Iterable[tuple[str, str, str]],
    base_summary: Optional[str] = None,
    company_name_hint: Optional[str] = None,
) -> IntelligenceResult:
    """Run the rule-based extractor on a sequence of ``(kind, url, text)`` pages.

    ``base_summary`` is the official Eurosatory description (always trustworthy).
    """
    pages = list(pages)
    result = IntelligenceResult()

    # Pick the best activity_summary: prefer the official Eurosatory description
    # when it reads as prose; otherwise use up to 3 prose sentences from the homepage.
    if base_summary and not _looks_like_keyword_stuffing(base_summary):
        result.activity_summary = base_summary[:1500]
    else:
        prose = _pick_prose_summary(pages, company_name_hint)
        if prose:
            result.activity_summary = prose[:1500]
        elif base_summary:
            # last resort: keep the keyword stuffing rather than have nothing
            result.activity_summary = base_summary[:1500]

    if base_summary:
        pages.append(("eurosatory", "eurosatory", base_summary))

    if not pages:
        result.fields_to_verify.extend(
            ["built_products", "sold_offerings", "services", "technologies", "target_clients", "business_model"]
        )
        return result

    full_text = "\n\n".join(t for _, _, t in pages if t)

    # built products
    built_ev, built_src = _label_hits(BUILT_PRODUCTS, pages)
    if built_ev:
        result.built_products = list(built_ev.keys())
        result.evidence["built_products"] = sum(built_ev.values(), [])
        for lbl in result.built_products:
            result.field_sources[f"built::{lbl}"] = built_src.get(lbl, "")
        result.field_confidence["built_products"] = _confidence(sum(len(v) for v in built_ev.values()))
    else:
        result.fields_to_verify.append("built_products")

    # sold offerings
    sold_ev, sold_src = _label_hits(OFFERING_VERBS, pages)
    if sold_ev:
        result.sold_offerings = list(sold_ev.keys())
        for lbl in result.sold_offerings:
            result.field_sources[f"sold::{lbl}"] = sold_src.get(lbl, "")
        result.field_confidence["sold_offerings"] = _confidence(sum(len(v) for v in sold_ev.values()))

    # services subset
    result.services = [
        lbl for lbl in result.sold_offerings
        if lbl in {"Maintenance / MRO", "Engineering / consulting", "Training",
                   "Cloud / data services", "Operational support",
                   "Distribution / agency", "Sub-contracting", "Certification / testing"}
    ]

    # technologies
    tech_ev, tech_src = _label_hits(TECHNOLOGIES, pages)
    if tech_ev:
        result.technologies = list(tech_ev.keys())
        for lbl in result.technologies:
            result.field_sources[f"tech::{lbl}"] = tech_src.get(lbl, "")
        result.field_confidence["technologies"] = _confidence(sum(len(v) for v in tech_ev.values()))

    # target clients
    client_ev, client_src = _label_hits(TARGET_CLIENTS, pages)
    if client_ev:
        result.target_clients = list(client_ev.keys())
        for lbl in result.target_clients:
            result.field_sources[f"client::{lbl}"] = client_src.get(lbl, "")
        result.field_confidence["target_clients"] = _confidence(sum(len(v) for v in client_ev.values()))

    # markets
    markets_found = []
    market_src = ""
    for kind, url, text in pages:
        for m, regex in MARKET_REGEX.items():
            if regex.search(text):
                if m not in markets_found:
                    markets_found.append(m)
                if not market_src:
                    market_src = url
    result.markets_served = markets_found[:8]
    if markets_found:
        result.field_sources["markets_served"] = market_src
        result.field_confidence["markets_served"] = _confidence(len(markets_found))

    # business model & company type
    bm_label, bm_match = _hit_first_label(BUSINESS_MODEL_HINTS, full_text)
    if bm_label:
        result.business_model = bm_label
        result.field_confidence["business_model"] = "medium"
    elif result.built_products:
        result.business_model = "OEM"  # default reasonable inference
        result.field_confidence["business_model"] = "low"
        result.fields_to_verify.append("business_model")
    else:
        result.fields_to_verify.append("business_model")

    ct_label, _ = _hit_first_label(COMPANY_TYPE_HINTS, full_text)
    if ct_label:
        result.company_type = ct_label
    elif result.built_products:
        result.company_type = "Manufacturer"

    # buying needs derived from built products
    needs: list[str] = []
    for built in result.built_products:
        for need in BUYING_NEEDS_BY_BUILT.get(built, []):
            if need not in needs:
                needs.append(need)
    needs.extend(n for n in UNIVERSAL_BUYING_NEEDS if n not in needs)
    result.probable_buying_needs = needs[:18]

    if result.built_products:
        result.buying_need_confidence = (
            "high" if result.field_confidence.get("built_products") == "high" else "medium"
        )
        result.buying_need_reasoning = (
            "Inferred from built_products="
            + ", ".join(result.built_products)
            + ". Universal needs (subcontracting, certification, export financing, logistics) added."
        )
    else:
        result.buying_need_confidence = "low"
        result.buying_need_reasoning = (
            "No clear built product evidence found in public sources — buying needs are speculative."
        )
        result.fields_to_verify.append("probable_buying_needs")

    # missing-field detection
    for f in ("built_products", "sold_offerings", "technologies", "target_clients"):
        if not getattr(result, f):
            result.fields_to_verify.append(f)
    # de-dup verify list
    result.fields_to_verify = list(dict.fromkeys(result.fields_to_verify))

    return result
