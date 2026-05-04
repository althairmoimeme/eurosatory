"""Defense commercial score (0-100) with A+/A/B/C/D priority levels.

Components (max 100):
* Defense / security fit       0-20
* Product / service clarity    0-15
* Supplier-buying potential    0-20
* Partnership potential        0-15
* Size / maturity              0-10
* International presence       0-10
* Data completeness            0-10
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.processors.defense_taxonomy import OTHER, is_defense_relevant


CORE_DEFENSE_LABELS = {
    "Land defense", "Air defense", "Naval defense", "Cybersecurity",
    "Intelligence / ISR", "C4ISR", "UAV / drones", "Counter-UAV",
    "Armored vehicles", "Weapons", "Ammunition", "Soldier systems",
    "Ballistic protection", "Optics / optronics", "Communications",
    "Electronic warfare", "Radar / sensors", "AI / data",
    "Simulation / training", "Logistics / MRO", "Engineering services",
    "Industrial subcontracting", "Space / satellite", "NRBC / CBRN",
}
DUAL_USE_OR_HOMELAND = {"Dual-use technology", "Homeland security", "Export / distribution"}


PRIORITY_LEVELS = [
    ("A+", 90),
    ("A", 75),
    ("B", 60),
    ("C", 40),
    ("D", 0),
]


@dataclass
class DefenseScore:
    total: float
    breakdown: dict[str, float]
    priority_level: str
    explanation: str
    maturity_score: float


def _level_for(total: float) -> str:
    for label, threshold in PRIORITY_LEVELS:
        if total >= threshold:
            return label
    return "D"


def _defense_fit(defense_categories: Iterable[str]) -> tuple[float, list[str]]:
    cats = list(defense_categories or [])
    pts = 0.0
    why: list[str] = []
    core = [c for c in cats if c in CORE_DEFENSE_LABELS]
    dual = [c for c in cats if c in DUAL_USE_OR_HOMELAND]
    if core:
        pts += min(8 + 3 * len(core), 18)
        why.append(f"core={','.join(core[:4])}")
    if dual:
        pts += 4
        why.append(f"dual-use={','.join(dual[:3])}")
    if not core and not dual and OTHER in cats:
        pts += 1
        why.append("only Other")
    return min(pts, 20.0), why


def _offering_clarity(intel: dict) -> tuple[float, list[str]]:
    pts = 0.0
    why: list[str] = []
    if intel.get("built_products"):
        pts += 8
        why.append(f"built={len(intel['built_products'])}")
    if intel.get("sold_offerings"):
        pts += 5
        why.append(f"sold={len(intel['sold_offerings'])}")
    if intel.get("technologies"):
        pts += 2
        why.append("tech")
    return min(pts, 15.0), why


def _supplier_buying_potential(intel: dict) -> tuple[float, list[str]]:
    needs = intel.get("probable_buying_needs") or []
    pts = 0.0
    why: list[str] = []
    if needs:
        pts += min(8 + len(needs) * 0.6, 18)
        why.append(f"{len(needs)} buying needs")
    if intel.get("buying_need_confidence") == "high":
        pts += 2
        why.append("high-confidence")
    return min(pts, 20.0), why


def _partnership_potential(intel: dict) -> tuple[float, list[str]]:
    pts = 0.0
    why: list[str] = []
    bm = (intel.get("business_model") or "").lower()
    if "system integrator" in bm or "OEM" in bm.upper():
        pts += 6
        why.append("integrator/OEM")
    if "distributor" in bm:
        pts += 4
        why.append("distributor")
    if intel.get("services"):
        pts += 3
        why.append("services")
    if intel.get("markets_served"):
        pts += min(2 + len(intel["markets_served"]) * 0.5, 5)
        why.append(f"markets={len(intel['markets_served'])}")
    return min(pts, 15.0), why


def _size_maturity(exh: dict) -> tuple[float, list[str]]:
    pts = 0.0
    why: list[str] = []
    band = exh.get("employee_range") or ""
    score_by_band = {
        "1-10": 2, "11-50": 4, "51-200": 6, "201-500": 8, "501-1000": 9, "1000+": 10,
    }
    if band in score_by_band:
        pts += score_by_band[band]
        why.append(f"size={band}")
    if exh.get("is_featured"):
        pts += 1
        why.append("featured")
    return min(pts, 10.0), why


def _international(exh: dict, intel: dict) -> tuple[float, list[str]]:
    pts = 0.0
    why: list[str] = []
    markets = intel.get("markets_served") or []
    if len(markets) >= 4:
        pts += 6
        why.append(f"markets≥{len(markets)}")
    elif len(markets) >= 2:
        pts += 4
        why.append("markets≥2")
    text = (intel.get("activity_summary") or "")
    if any(k in text.lower() for k in ("export", "worldwide", "international", "60 countries")):
        pts += 2
        why.append("international-text")
    if exh.get("country_iso2") and exh.get("country_iso2") not in {"FR"}:
        pts += 2
        why.append("non-fr")
    return min(pts, 10.0), why


def _data_completeness(exh: dict, intel: dict) -> tuple[float, list[str]]:
    pts = 0.0
    why: list[str] = []
    if exh.get("website_url"):
        pts += 2
    if exh.get("contact_email"):
        pts += 2
    if exh.get("phone"):
        pts += 1
    if exh.get("linkedin_url"):
        pts += 1
    if exh.get("address1"):
        pts += 1
    if intel.get("activity_summary"):
        pts += 1
    if intel.get("built_products"):
        pts += 1
    if intel.get("technologies"):
        pts += 1
    why.append(f"raw={pts:.0f}")
    return min(pts, 10.0), why


def score_defense(exh: dict, intel: dict) -> DefenseScore:
    parts: dict[str, float] = {}
    explain: list[str] = []

    for name, fn, args in (
        ("defense_fit", _defense_fit, [intel.get("defense_categories") or []]),
        ("offering_clarity", _offering_clarity, [intel]),
        ("supplier_buying_potential", _supplier_buying_potential, [intel]),
        ("partnership_potential", _partnership_potential, [intel]),
        ("size_maturity", _size_maturity, [exh]),
        ("international", _international, [exh, intel]),
        ("data_completeness", _data_completeness, [exh, intel]),
    ):
        pts, why = fn(*args)  # type: ignore[arg-type]
        parts[name] = round(pts, 2)
        if why:
            explain.append(f"{name}=" + "+".join(why))

    total = round(min(sum(parts.values()), 100.0), 2)
    priority = _level_for(total)

    # maturity = combination of size + clarity + tech
    maturity = round(parts["size_maturity"] + parts["offering_clarity"] * 0.5 + parts["data_completeness"] * 0.3, 2)

    return DefenseScore(
        total=total,
        breakdown=parts,
        priority_level=priority,
        explanation=" | ".join(explain) or "no signals",
        maturity_score=maturity,
    )
