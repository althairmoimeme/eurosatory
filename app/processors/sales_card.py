"""Sales-card generator — turns the structured intelligence into a one-page,
human-readable, copy-paste-ready output for the sales team.

Pitch is *contextual*: it reads the company's primary defense category, its
strongest built_product, its top 2 probable buying needs and its target type,
then composes a specific angle and pitch.  No more templated boilerplate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Mapping rules — what intelligence shape implies what target type
# ---------------------------------------------------------------------------

INTEGRATOR_BUSINESS_MODELS = {"OEM", "System integrator", "Equipment manufacturer"}
SUPPLIER_BUSINESS_MODELS = {
    "Sub-contractor", "Materials / parts supplier", "Software / SaaS vendor",
    "Engineering services",
}
SERVICE_BUSINESS_MODELS = {"Consulting firm", "Engineering services"}
DISTRIBUTOR_BUSINESS_MODELS = {"Distributor / reseller"}


# Strategic defense categories (high-priority for sales) — order matters: first
# match wins as the "primary" category for pitch composition.
PRIMARY_CATEGORY_PRIORITY = [
    "Counter-UAV",
    "UAV / drones",
    "Space / satellite",        # moved up: when Satellites / Space is built, the
                                # company is genuinely a space player (ArianeGroup).
    "Cybersecurity",
    "AI / data",
    "Electronic warfare",
    "Intelligence / ISR",
    "C4ISR",
    "Radar / sensors",
    "Optics / optronics",
    "Communications",
    "Armored vehicles",
    "Weapons",
    "Ammunition",
    "Soldier systems",
    "Ballistic protection",
    "Simulation / training",
    "Logistics / MRO",
    "Engineering services",
    "Industrial subcontracting",
    "Naval defense",
    "Air defense",
    "Land defense",
    "Homeland security",
    "NRBC / CBRN",
    "Dual-use technology",
    "Export / distribution",
    "Other",
]

# Per-category business-relevant phrasing.  Keys are (category, target_role) where
# target_role is one of: supplier, buyer, partner, distributor, qualify.
CATEGORY_ANGLES: dict[str, dict[str, str]] = {
    "Counter-UAV": {
        "supplier": "Their counter-UAV stack relies on RF, radar, optronics and embedded systems — exactly what we supply. Talk to procurement / chief engineer.",
        "buyer": "Defense end-users buying C-UAS solutions are tightening specs every year. Position our offer as a complement to {company}'s sensor fusion / kinetic effector chain.",
        "partner": "{company} would benefit from a partnership on detection-effector integration or AI classification of drone threats. Talk to head of engineering / R&D.",
        "distributor": "Use {company} as a channel for our anti-drone modules in {markets}.",
        "qualify": "Counter-UAV market moves fast — confirm where {company} sits (sensor, effector, integrator) before pitching.",
    },
    "UAV / drones": {
        "supplier": "UAV builds need batteries, motors, optronics, RF links and composites. Map their bill of materials with the supply chain lead.",
        "buyer": "UAV programs are scaling — {company} is buying. Position [your sub-system] as a qualified alternative on their next platform.",
        "partner": "Joint UAV programs and dual-use civil/military offer scale. Engineering / programme manager is the entry point.",
        "distributor": "{company}'s UAV portfolio is a fit for {markets}. Pitch an exclusivity / representation agreement.",
        "qualify": "Confirm whether {company} builds airframes, payloads or services before pitching.",
    },
    "Cybersecurity": {
        "supplier": "Cyber-defense vendors source threat intel feeds, hardware appliances, cloud and certification services. Procurement / CISO are entry points.",
        "buyer": "{company} sells cyber to defense — they also need NDA-friendly suppliers for components, cloud and audits. Lead with compliance pedigree.",
        "partner": "Co-developing managed-detection or sovereign-cloud offers with {company} can unlock new tenders. Talk to BU lead.",
        "distributor": "Cyber resale into {markets} is a fit. Pitch a country-licensing or MSP agreement.",
        "qualify": "Cyber is broad — qualify SOC vs SIEM vs OT vs sovereign cloud before pitching.",
    },
    "AI / data": {
        "supplier": "AI platforms consume GPUs, cloud, data annotation services and cybersecurity hardening. Talk to CTO / head of platform.",
        "buyer": "{company} needs sovereign data infrastructure and ML ops — ITAR-friendly supplier list is short. Lead with compliance.",
        "partner": "Joint defense-AI use cases (ISR triage, decision-support) are the angle. Engineering lead is the buyer.",
        "distributor": "AI platform resale into {markets} fits — pitch a country licensing or MSP partnership.",
        "qualify": "AI is overloaded — confirm if {company} does platform, model, or services before pitching.",
    },
    "Electronic warfare": {
        "supplier": "EW systems source RF amplifiers, signal-processing chips, embedded computing and antennas. Talk to RF system lead.",
        "buyer": "EW upgrades are a current priority for armed forces — {company} buys components and certification. Procurement is the entry point.",
        "partner": "Co-developing jamming / spoofing modules with {company} can fill gaps in their portfolio.",
        "distributor": "EW exports are tightly controlled — confirm dual-use vs ITAR scope before pitching.",
        "qualify": "EW is sensitive — confirm export-control posture before approaching.",
    },
    "Intelligence / ISR": {
        "supplier": "ISR platforms aggregate sensors, comms and AI. Map {company}'s integration partners.",
        "buyer": "{company}'s ISR offer needs sensor primes and data-fusion suppliers. Engineering / programme is the buyer.",
        "partner": "Joint ISR offers with sensor / AI primes win tenders. Talk to head of programmes.",
        "distributor": "ISR exports require export-control screening — confirm before pitching distribution.",
        "qualify": "ISR can mean sensor, processing, or services — qualify scope first.",
    },
    "C4ISR": {
        "supplier": "C4ISR primes integrate hundreds of components — the sourcing surface is huge. Procurement / supply chain is the entry point.",
        "buyer": "{company} pitches end-to-end C4ISR; they need sub-systems, software components and compliance services.",
        "partner": "Joint bids on national C4ISR tenders are the angle. Talk to head of business development.",
        "distributor": "C4ISR resale needs deep technical pre-sales — qualify before pitching.",
        "qualify": "C4ISR is broad — confirm if {company} integrates, builds, or operates before pitching.",
    },
    "Radar / sensors": {
        "supplier": "Radar / sensor lines need specialised electronics, signal processing chips and composite housings. Talk to chief engineer.",
        "buyer": "{company} sells radars; they buy front-ends, processing modules and field testing. Engineering buys.",
        "partner": "Joint sensor-fusion offers (radar + EO/IR + RF) win bigger contracts. Talk to head of engineering.",
        "distributor": "Sensor exports are export-controlled — confirm before pitching distribution.",
        "qualify": "Sensor scope varies wildly — qualify before pitching.",
    },
    "Optics / optronics": {
        "supplier": "Optronics suppliers buy specialty glass, detectors and precision mechanics. Talk to procurement and engineering.",
        "buyer": "{company} sells optronics, they need detector and electronics suppliers. Lead with quality data.",
        "partner": "Joint optronics + AI offers (smart sights, autonomous targeting) are the new wave.",
        "distributor": "Optronics export is controlled — confirm regulation posture before pitching.",
        "qualify": "Confirm if {company} makes complete sights, sub-systems, or detectors only.",
    },
    "Communications": {
        "supplier": "Tactical comms vendors source RF, encryption modules and certifications. Talk to RF / security architect.",
        "buyer": "{company} ships radios; they need crypto, RF and integration partners. Engineering buys.",
        "partner": "Joint waveform / interoperability offers with {company} unlock cross-NATO tenders.",
        "distributor": "Tactical comms export is restricted — confirm scope before pitching.",
        "qualify": "Confirm scope: tactical, satcom, or backbone before pitching.",
    },
    "Armored vehicles": {
        "supplier": "Armored vehicle OEMs buy engines, composites, ballistic plates, optronics, comms and specialised mechanics in volume.",
        "buyer": "{company} buys for next-gen platforms — engineering and supply chain are the buyers.",
        "partner": "Sub-system primes (turret, optronics, drive train) build long-term relationships with vehicle OEMs.",
        "distributor": "Armored vehicles rarely use distributors — confirm before pitching.",
        "qualify": "Confirm if {company} is OEM, sub-system or upgrade specialist.",
    },
    "Weapons": {
        "supplier": "Weapon manufacturers source precision machining, materials, optronics and ammunition components — heavy export control.",
        "buyer": "{company} is a regulated buyer — lead with ITAR/AQAP/EN9100 credentials.",
        "partner": "Joint upgrade kits (smart sights, modular accessories) with {company} are an angle.",
        "distributor": "Weapons distribution is heavily licensed — confirm legal posture before pitching.",
        "qualify": "Weapons span small arms to artillery — qualify scope.",
    },
    "Ammunition": {
        "supplier": "Ammunition makers buy energetic materials, mechanical parts, packaging and certification services.",
        "buyer": "{company} buys raw materials and machining — long lead times. Procurement is the buyer.",
        "partner": "Joint smart-munition or guidance-kit offers grow margin. Engineering is the entry point.",
        "distributor": "Ammunition export is licensed — confirm scope before pitching.",
        "qualify": "Ammunition spans small calibre to large — qualify scope.",
    },
    "Soldier systems": {
        "supplier": "Soldier system vendors source textiles, composites, optronics and energy. Lead with NATO / military pedigree.",
        "buyer": "{company} sells to forces — they need certified materials and ergonomic sub-systems.",
        "partner": "Joint dismounted-soldier programmes (HUD, exoskeleton, comms-on-the-move) are open.",
        "distributor": "Soldier kit distribution into {markets} is plausible — pitch a representation agreement.",
        "qualify": "Confirm if {company} sells textiles, optronics, comms or full kit.",
    },
    "Ballistic protection": {
        "supplier": "Ballistic protection vendors buy aramid, ceramic, composites and certification services.",
        "buyer": "{company} sells armour — they need composites and certified test-houses. Procurement is the buyer.",
        "partner": "Joint armour + sensor / electronics offers win infantry-vehicle tenders.",
        "distributor": "Body-armour exports are dual-use controlled — confirm before pitching distribution.",
        "qualify": "Confirm if {company} sells body armour, vehicle armour or panels.",
    },
    "Simulation / training": {
        "supplier": "Simulator builders source visual / motion / haptics hardware and AI components. Engineering is the buyer.",
        "buyer": "{company} builds simulators — they need content, AI and hardware suppliers.",
        "partner": "Joint LVC (live-virtual-constructive) bids unlock training-services tenders.",
        "distributor": "Training systems can be resold via local agents in {markets}.",
        "qualify": "Confirm if {company} makes simulators or training services.",
    },
    "Logistics / MRO": {
        "supplier": "MRO providers source spare parts, calibration tools and certification services. Procurement is the buyer.",
        "buyer": "{company} performs MRO — they need parts and engineering support. Direct procurement angle.",
        "partner": "Joint MRO + sustainment offers (PBL, performance-based logistics) win long contracts.",
        "distributor": "MRO is rarely distributed — confirm scope before pitching.",
        "qualify": "Confirm if {company} does MRO, sustainment or only spares.",
    },
    "Engineering services": {
        "supplier": "Engineering shops buy CAD licences, simulation tools and certification services.",
        "buyer": "{company} sells engineering hours — they buy tooling. Limited supplier upside.",
        "partner": "Sub-contracting engineering bandwidth on defense programmes is a fit.",
        "distributor": "Engineering services aren't distributed — qualify before pitching.",
        "qualify": "Confirm if {company} works on defense programmes or only civil.",
    },
    "Industrial subcontracting": {
        "supplier": "Sub-contractors buy raw materials, cutting tools and quality services.",
        "buyer": "{company} is a sub-contractor — lead with cost, lead-time and certification.",
        "partner": "Joint capacity offers (network of sub-contractors) win volume tenders.",
        "distributor": "Sub-contracting isn't distributed — qualify before pitching.",
        "qualify": "Confirm if {company} machines, sheets or assembles.",
    },
    "Naval defense": {
        "supplier": "Naval primes buy radars, sonars, comms, weapons and sustainment in long cycles.",
        "buyer": "{company} sells naval kit — long sales cycle. Programme office is the buyer.",
        "partner": "Joint naval combat-system bids open large frame contracts.",
        "distributor": "Naval exports rarely use distributors — confirm before pitching.",
        "qualify": "Confirm naval scope: shipbuilder, sub-system or services.",
    },
    "Air defense": {
        "supplier": "Air defense systems buy radars, missile components, comms and electronics.",
        "buyer": "{company} sells SAM / AAA systems — long programmes. Programme office is the buyer.",
        "partner": "Layered air-defense bids (sensor + effector) win frame contracts.",
        "distributor": "Air defense rarely uses distributors — confirm before pitching.",
        "qualify": "Confirm if {company} integrates or supplies sub-systems.",
    },
    "Land defense": {
        "supplier": "Land defense primes integrate huge supply chains. Procurement is the buyer.",
        "buyer": "{company} sells to land forces — they need sub-systems, composites and certification.",
        "partner": "Joint vehicle / soldier-system offers fit large army tenders.",
        "distributor": "Land defense exports use licensed agents — confirm scope.",
        "qualify": "Confirm scope: vehicle, soldier or weapon system.",
    },
    "Homeland security": {
        "supplier": "Homeland security vendors buy sensors, comms, software and integration services.",
        "buyer": "{company} sells to police / border — they need fast-deployable kits and certified comms.",
        "partner": "Joint border-monitoring offers (drone + radar + AI) win EU-funded tenders.",
        "distributor": "Homeland security is sold via licensed agents — pitch a country agreement for {markets}.",
        "qualify": "Confirm scope: police, border, fire, intelligence agency.",
    },
    "Space / satellite": {
        "supplier": "Space primes source composites, sensors, comms and launch services.",
        "buyer": "{company} builds space hardware — they need certified components and test services.",
        "partner": "Joint satellite-as-a-service bids unlock defense communications tenders.",
        "distributor": "Space products rarely use distributors — confirm before pitching.",
        "qualify": "Confirm scope: satellite, ground segment or services.",
    },
    "NRBC / CBRN": {
        "supplier": "CBRN vendors buy detection sensors, filtration materials and certified PPE.",
        "buyer": "{company} sells CBRN kit — they need certified materials and detection.",
        "partner": "Joint detection + decontamination offers fit homeland tenders.",
        "distributor": "CBRN can be distributed via licensed agents in {markets}.",
        "qualify": "Confirm scope: detection, protection or training.",
    },
    "Dual-use technology": {
        "supplier": "Dual-use vendors balance civil and defense — supply both lines.",
        "buyer": "{company} adapts civil tech for defense — they need MIL-grade sub-suppliers.",
        "partner": "Joint defense-grade variants of civil products win contracts.",
        "distributor": "Dual-use distribution into {markets} is plausible.",
        "qualify": "Confirm defense traction vs civil revenue mix.",
    },
    "Export / distribution": {
        "supplier": "Distributors buy from primes — confirm if you'd be an upstream supplier they could carry.",
        "buyer": "{company} resells defense kit — they need new vendors with export-friendly licences.",
        "partner": "{company} can become a channel partner for our portfolio in their territory.",
        "distributor": "Mutual distribution alignment — pitch a portfolio swap or co-marketing.",
        "qualify": "Confirm regions and existing portfolio before pitching.",
    },
    "Other": {
        "supplier": "Public data is thin — qualify the activity before pitching supply.",
        "buyer": "Qualify the activity before pitching.",
        "partner": "Qualify the activity before pitching.",
        "distributor": "Qualify the activity before pitching distribution.",
        "qualify": "Public sources too thin — discovery call first, focused pitch second.",
    },
}


@dataclass
class CommercialTargeting:
    target_types: list[str]
    interest_level: str  # very_high | high | medium | low
    interest_reason: str
    recommended_sales_angle: str
    recommended_pitch: str
    probable_objections: list[str]
    prospecting_keywords: list[str]
    summary_for_sales: str


def _interest_level_from_score(score: Optional[float]) -> str:
    if score is None:
        return "low"
    if score >= 90:
        return "very_high"
    if score >= 75:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def _ascii_join(items: list[str], limit: int = 4) -> str:
    if not items:
        return "—"
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" (+{len(items) - limit})"


def _summary_block(lines: list[str]) -> str:
    return "\n".join(f"- {l}" for l in lines if l)


def _pick_primary_category(
    defense_categories: list[str], built_products: Optional[list[str]] = None
) -> str:
    """Pick the most representative defense category.

    Strategy: for each candidate category, count how many of its anchor
    ``built_products`` are present.  Sort by ``(anchor_hits desc, priority asc)``
    so that a category strongly evidenced by manufactured products beats one
    that only matched on a passing keyword.  If no candidate has anchors, fall
    back to the priority order.
    """
    from app.processors.defense_taxonomy import CATEGORY_ANCHORS

    cats = list(dict.fromkeys(defense_categories or []))
    if not cats:
        return "Other"
    builts = set(built_products or [])

    def _anchor_hits(cat: str) -> int:
        anchors = CATEGORY_ANCHORS.get(cat) or set()
        if not anchors:
            return 0
        return len(anchors & builts)

    def _priority_index(cat: str) -> int:
        try:
            return PRIMARY_CATEGORY_PRIORITY.index(cat)
        except ValueError:
            return len(PRIMARY_CATEGORY_PRIORITY)

    cats.sort(key=lambda c: (-_anchor_hits(c), _priority_index(c)))
    return cats[0]


def _primary_role(target_types: list[str]) -> str:
    """Map the list of target_types to one primary role used to pick a pitch."""
    if "potential_supplier" in target_types or "subcontractor" in target_types:
        return "supplier"
    if "potential_buyer" in target_types or "industrial_partner" in target_types or "prime_contractor" in target_types:
        return "buyer"
    if "technology_integrator" in target_types or "integrator" in target_types:
        return "partner"
    if "distributor" in target_types:
        return "distributor"
    return "qualify"


def _format_template(tmpl: str, *, company: str, markets: str, products: str) -> str:
    return (
        tmpl.replace("{company}", company)
        .replace("{markets}", markets if markets != "—" else "their region")
        .replace("{products}", products if products != "—" else "their build line")
    )


def _build_pitch(
    *, company: str, role: str, primary_category: str, built_products: list[str],
    top_buying_needs: list[str], target_clients: list[str], markets: list[str],
) -> tuple[str, str]:
    """Return (sales_angle, pitch) — both grounded in concrete facts."""
    angle_template = CATEGORY_ANGLES.get(primary_category, CATEGORY_ANGLES["Other"]).get(
        role, CATEGORY_ANGLES["Other"]["qualify"]
    )
    products_str = _ascii_join(built_products, 2)
    markets_str = _ascii_join(markets, 2)
    sales_angle = _format_template(
        angle_template, company=company, markets=markets_str, products=products_str
    )

    # Build a concrete, fact-laden pitch
    if role == "supplier":
        if top_buying_needs:
            need_focus = ", ".join(top_buying_needs[:3])
            pitch = (
                f"Hi — I noticed {company} builds {products_str} for {primary_category}. "
                f"Companies like yours typically source {need_focus} on long lead times. "
                f"We supply {top_buying_needs[0].lower()} qualified for ITAR/AQAP/EN9100 and would "
                f"like to align around your 2026 sourcing plan. 20 minutes around Eurosatory?"
            )
        else:
            pitch = (
                f"Hi — {company}'s {primary_category} portfolio caught my eye. "
                "We supply qualified components for similar OEMs and would like to map "
                "where we'd fit in your supply chain. 20 minutes around Eurosatory?"
            )
    elif role == "buyer":
        clients = _ascii_join(target_clients, 2) if target_clients else "defense customers"
        pitch = (
            f"Hi — {company} ships {products_str} to {clients}. "
            f"We work with {primary_category} primes on [your offering] and have shaved "
            f"X% off [pain] on similar programmes. Worth a 25-minute deep-dive at Eurosatory?"
        )
    elif role == "partner":
        pitch = (
            f"Hi — {company}'s {primary_category} stack and our [your offering] cover "
            "complementary parts of the kill / decision chain. A joint offer would unlock "
            f"larger {primary_category} tenders. 30 minutes at Eurosatory to scope?"
        )
    elif role == "distributor":
        market_str = _ascii_join(markets, 1) if markets else "your region"
        pitch = (
            f"Hi — we're scouting an exclusive distributor for {market_str}. "
            f"Your {primary_category} portfolio fits ours. Interested in a quick alignment "
            "call during Eurosatory?"
        )
    else:  # qualify
        pitch = (
            f"Hi — I noticed {company} at Eurosatory. Could you share a quick overview of "
            "what your team is showcasing this year? We work with similar "
            f"{primary_category} vendors and may have an angle for you."
        )
    return sales_angle, pitch


def _build_objections(
    *, role: str, primary_category: str, built_products: list[str],
    technologies: list[str], interest: str,
) -> list[str]:
    out: list[str] = []
    if role == "supplier":
        out.append("Existing approved-vendor list is closed — you need to qualify in.")
        out.append("Long ITAR / EN9100 / AQAP qualification cycles delay first PO.")
    elif role == "buyer":
        out.append("Decision lives at programme level, not at corporate — map stakeholders.")
        out.append("Capex sign-off can be 6–12 months on defense programmes.")
    elif role == "partner":
        out.append("Joint IP and export-control posture must be aligned upfront.")
        out.append("Both sides will demand reference customers.")
    elif role == "distributor":
        out.append("Existing distributor agreements may be exclusive in target region.")
        out.append("Margin split and end-user-screening responsibility need to be clarified.")
    else:
        out.append("Public data is thin — qualify before investing time.")

    if primary_category in {"Cybersecurity", "AI / data", "Electronic warfare", "Counter-UAV"}:
        out.append("Highly sensitive context — NDA / clearance often required before deeper talks.")
    if primary_category in {"Weapons", "Ammunition", "Naval defense", "Air defense"}:
        out.append("Heavy export-control scrutiny — pre-clear destination jurisdictions.")
    if "Cybersecurity solutions" in built_products or "Cybersecurity" in technologies:
        out.append("Cyber-sensitive context — NDA / security clearance often required.")
    if interest == "low":
        out.append("Public data is thin — risk of low-fit approach; do discovery first.")
    return out[:5]


def derive_commercial_targeting(
    *,
    company_name: str,
    country_iso2: Optional[str],
    business_model: Optional[str],
    built_products: list[str],
    sold_offerings: list[str],
    technologies: list[str],
    target_clients: list[str],
    markets_served: list[str],
    probable_buying_needs: list[str],
    activity_summary: Optional[str],
    defense_categories: list[str],
    defense_score: Optional[float],
) -> CommercialTargeting:
    bm = business_model or ""
    target_types: list[str] = []

    if bm in INTEGRATOR_BUSINESS_MODELS or any(p in built_products for p in ("Vehicles", "UAV / drones", "Counter-UAV systems", "AI platforms")):
        target_types.extend(["potential_buyer", "industrial_partner"])
    if bm in SUPPLIER_BUSINESS_MODELS or any(p in built_products for p in ("Electronic components", "Sensors", "Composite materials", "Mechanical parts")):
        target_types.append("potential_supplier")
    if bm in SERVICE_BUSINESS_MODELS or any(p in sold_offerings for p in ("Engineering / consulting", "Maintenance / MRO", "Training")):
        target_types.append("subcontractor")
    if bm in DISTRIBUTOR_BUSINESS_MODELS or "Distribution / agency" in sold_offerings:
        target_types.append("distributor")
    if "AI platforms" in built_products or "Cybersecurity solutions" in built_products:
        target_types.append("technology_integrator")
    if not target_types:
        target_types.append("to_qualify")

    target_types = list(dict.fromkeys(target_types))

    interest = _interest_level_from_score(defense_score)
    primary_category = _pick_primary_category(defense_categories, built_products)
    role = _primary_role(target_types)

    interest_reason_parts: list[str] = []
    interest_reason_parts.append(f"primary defense fit: {primary_category}")
    if built_products:
        interest_reason_parts.append(f"builds {_ascii_join(built_products, 3)}")
    if probable_buying_needs:
        interest_reason_parts.append(f"likely buys {_ascii_join(probable_buying_needs, 3)}")
    interest_reason = "; ".join(interest_reason_parts)

    sales_angle, pitch = _build_pitch(
        company=company_name, role=role, primary_category=primary_category,
        built_products=built_products,
        top_buying_needs=probable_buying_needs,
        target_clients=target_clients, markets=markets_served,
    )

    objections = _build_objections(
        role=role, primary_category=primary_category,
        built_products=built_products, technologies=technologies, interest=interest,
    )

    keywords = list(
        dict.fromkeys(
            (built_products or [])
            + (technologies or [])
            + (defense_categories or [])
            + (markets_served or [])
        )
    )[:14]

    summary_for_sales = _summary_block(
        [
            f"Activity: {(activity_summary or '').strip()[:240] or '—'}",
            f"Key offering: builds {_ascii_join(built_products)}; sells {_ascii_join(sold_offerings)}",
            f"Buying angle: {_ascii_join(probable_buying_needs, 5)}",
            f"Why a target ({interest}, primary={primary_category}, role={role}): {interest_reason}",
            f"Approach: {sales_angle}",
        ]
    )

    return CommercialTargeting(
        target_types=target_types,
        interest_level=interest,
        interest_reason=interest_reason,
        recommended_sales_angle=sales_angle,
        recommended_pitch=pitch,
        probable_objections=objections,
        prospecting_keywords=keywords,
        summary_for_sales=summary_for_sales,
    )


def render_sales_card(
    *,
    company_name: str,
    country_iso2: Optional[str],
    activity_summary: Optional[str],
    built_products: list[str],
    sold_offerings: list[str],
    probable_buying_needs: list[str],
    interest_reason: str,
    sales_angle: str,
    pitch: str,
    priority_level: Optional[str],
    fields_to_verify: list[str],
    field_sources: dict[str, str],
) -> str:
    """Render the verbatim sales-team card."""
    src_lines = [f"  - {k}: {v}" for k, v in list(field_sources.items())[:8]]
    return (
        f"SOCIÉTÉ : {company_name} ({country_iso2 or '—'})\n"
        f"ACTIVITÉ : {(activity_summary or '—').strip()[:300]}\n"
        f"CE QU'ILS FABRIQUENT : {', '.join(built_products) or '—'}\n"
        f"CE QU'ILS VENDENT : {', '.join(sold_offerings) or '—'}\n"
        f"CE QU'ILS ACHÈTENT PROBABLEMENT : {', '.join(probable_buying_needs[:8]) or '—'}\n"
        f"POURQUOI C'EST UNE CIBLE : {interest_reason}\n"
        f"ANGLE D'APPROCHE : {sales_angle}\n"
        f"PITCH CONSEILLÉ : {pitch}\n"
        f"PRIORITÉ : {priority_level or '—'}\n"
        f"DONNÉES À VÉRIFIER : {', '.join(fields_to_verify) or '(none)'}\n"
        f"SOURCES :\n" + ("\n".join(src_lines) if src_lines else "  - (none)")
    )
