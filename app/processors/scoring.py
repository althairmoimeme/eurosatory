"""Commercial relevance scoring (0-100).

The score breaks into transparent, auditable components so the sales team
can see *why* a company is rated A/B/C/D.  Each component is bounded; the
total is the sum capped at 100.

Components
----------
* data_completeness (0-30): website + email + phone + LinkedIn + address.
* sector_strategic   (0-25): membership in priority Eurosatory categories.
* size              (0-15): employee_range when known.
* international     (0-10): export markets / multi-country presence hints.
* eurosatory_signal (0-10): featured / new / lab / direct booth.
* lead_actionability(0-10): a generic sales email & a phone are present.

priority_level: A>=75, B>=55, C>=35, D otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

PRIORITY_LABELS_A = {
    "Cybersécurité",
    "Drones / UAV",
    "Anti-drones",
    "Intelligence artificielle",
    "Surveillance / renseignement",
    "Robotique",
    "Spatial / satellite",
}
PRIORITY_LABELS_B = {
    "Défense terrestre",
    "Sécurité intérieure",
    "Communication / radio",
    "Optique / vision nocturne",
    "Simulation / formation",
    "Véhicules blindés",
    "Protection balistique",
    "NRBC / CBRN",
    "Santé / médical militaire",
}

EMPLOYEE_BUCKETS = {
    "1-10": 2,
    "11-50": 5,
    "51-200": 8,
    "201-500": 11,
    "501-1000": 13,
    "1000+": 15,
}


@dataclass
class ScoreResult:
    total: float
    breakdown: dict[str, float]
    priority_level: str  # A/B/C/D
    explanation: str


def _data_completeness(exh: dict) -> tuple[float, list[str]]:
    pts, why = 0.0, []
    if exh.get("website_url"):
        pts += 6
        why.append("website")
    if exh.get("contact_email"):
        pts += 8
        why.append("email")
    if exh.get("phone"):
        pts += 5
        why.append("phone")
    if exh.get("linkedin_url"):
        pts += 6
        why.append("linkedin")
    if exh.get("address1") or exh.get("city"):
        pts += 5
        why.append("address")
    return min(pts, 30.0), why


def _sector_strategic(labels: Iterable[str]) -> tuple[float, list[str]]:
    label_set = set(labels)
    pts = 0.0
    matched = []
    for lbl in label_set:
        if lbl in PRIORITY_LABELS_A:
            pts += 12
            matched.append(f"A:{lbl}")
        elif lbl in PRIORITY_LABELS_B:
            pts += 7
            matched.append(f"B:{lbl}")
        elif lbl == "Autre":
            pass
        else:
            pts += 3
            matched.append(lbl)
    return min(pts, 25.0), matched


def _size(exh: dict) -> tuple[float, list[str]]:
    band = exh.get("employee_range")
    if not band:
        return 0.0, []
    pts = float(EMPLOYEE_BUCKETS.get(band, 0))
    return pts, [f"size:{band}"]


def _international(exh: dict) -> tuple[float, list[str]]:
    presentation = " ".join(
        filter(None, [exh.get("presentation") or "", exh.get("short_presentation") or ""])
    )
    pts = 0.0
    why: list[str] = []
    if presentation:
        text = presentation.lower()
        if any(k in text for k in ("export", "international", "worldwide", "global ", "60 countries", "exporting")):
            pts += 6
            why.append("international-text")
    if exh.get("country_iso2") and exh.get("country_iso2") not in {"FR"}:
        pts += 4
        why.append("non-fr")
    return min(pts, 10.0), why


def _eurosatory_signal(exh: dict) -> tuple[float, list[str]]:
    pts = 0.0
    why: list[str] = []
    if exh.get("is_featured"):
        pts += 4
        why.append("featured")
    if exh.get("is_new_exhibitor"):
        pts += 3
        why.append("new")
    if exh.get("is_lab"):
        pts += 2
        why.append("lab")
    stands = exh.get("stands") or []
    if stands and any(s.get("Hall") for s in stands):
        pts += 2
        why.append("hall-assigned")
    return min(pts, 10.0), why


def _lead_actionability(exh: dict) -> tuple[float, list[str]]:
    pts = 0.0
    why: list[str] = []
    if exh.get("generic_sales_email") or (
        exh.get("contact_email") and exh.get("field_confidence", {}).get("contact_email") == "high"
    ):
        pts += 6
        why.append("strong-email")
    if exh.get("phone"):
        pts += 4
        why.append("phone-ok")
    return min(pts, 10.0), why


def score_exhibitor(exh: dict, classification_labels: Iterable[str]) -> ScoreResult:
    parts = {}
    why_all: list[str] = []
    for name, fn, arg in (
        ("data_completeness", _data_completeness, exh),
        ("sector_strategic", _sector_strategic, classification_labels),
        ("size", _size, exh),
        ("international", _international, exh),
        ("eurosatory_signal", _eurosatory_signal, exh),
        ("lead_actionability", _lead_actionability, exh),
    ):
        pts, why = fn(arg)  # type: ignore[arg-type]
        parts[name] = round(pts, 2)
        if why:
            why_all.append(f"{name}=" + "+".join(why))

    total = round(min(sum(parts.values()), 100.0), 2)
    priority = (
        "A" if total >= 75 else "B" if total >= 55 else "C" if total >= 35 else "D"
    )
    explanation = " | ".join(why_all) or "no signals"
    return ScoreResult(
        total=total,
        breakdown=parts,
        priority_level=priority,
        explanation=explanation,
    )
