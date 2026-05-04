"""Turn an ``Exhibitor`` + ``ExhibitorIntelligence`` pair into one CRM-ready row.

We never invent: if a value is missing we leave it empty (or ``"To enrich"`` for
a few user-facing string fields).  Derived values (``ideal_seller_profile``,
``short_pitch``, ``next_best_action``, ``data_confidence``,
``manual_review_required``) are computed deterministically from the inputs.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Optional

from app.crm.normalizers import (
    company_type_fr,
    country_fr,
    crm_stage,
    first_booth,
    lead_status,
    next_best_action,
    target_type_fr,
    target_types_to_french,
)
from app.crm.pitch import (
    commercial_relevance_summary as _commercial_relevance_summary,
    company_pitch as _company_pitch,
    core_business as _core_business,
    main_products_services as _main_products_services,
)
from app.processors.sales_card import _pick_primary_category

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TO_ENRICH = "To enrich"


def _join(values: Optional[Iterable[Any]], sep: str = "; ") -> str:
    if not values:
        return ""
    return sep.join(str(v) for v in values if v)


def _truncate_sentence(text: Optional[str], max_chars: int = 200) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.strip().split())
    # take the first sentence ending with . / ! / ? — but keep the punctuation
    m = re.search(r"^.{20,400}?[.!?](?=\s|$)", cleaned)
    if m:
        s = m.group(0)
    else:
        s = cleaned
    return s if len(s) <= max_chars else s[: max_chars - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# ideal_seller_profile templates per (segment, role) — short and concrete
# ---------------------------------------------------------------------------

SELLER_PROFILE_BY_SEGMENT_ROLE: dict[tuple[str, str], str] = {
    ("Counter-UAV", "supplier"): "Fournisseur de RF, radars, optronique et embarqués pour stacks anti-drone.",
    ("Counter-UAV", "buyer"): "Vend une brique du chain detection-effector aux primes anti-drone.",
    ("Counter-UAV", "partner"): "Partenaire intégration capteurs / IA pour offres anti-drone conjointes.",
    ("UAV / drones", "supplier"): "Fournisseur de batteries, moteurs, optronique, RF, composites pour fabricants UAV.",
    ("UAV / drones", "buyer"): "Sub-system / payload pour OEM drones tactiques.",
    ("UAV / drones", "partner"): "Co-développement plateformes et payloads UAV.",
    ("Cybersecurity", "supplier"): "Threat intel, hardware appliances, cloud souverain, audits.",
    ("Cybersecurity", "buyer"): "Outils SOC/SIEM, brique de sécurité défensive pour vendeur cyber.",
    ("Cybersecurity", "partner"): "Partenariat MDR / cloud souverain / co-bid sur tenders.",
    ("AI / data", "supplier"): "GPUs, cloud, data annotation, hardening cyber pour plateforme IA.",
    ("AI / data", "buyer"): "Module IA / fusion de données pour intégration dans la stack du client.",
    ("AI / data", "partner"): "Co-bid IA défense (ISR triage, decision-support).",
    ("Electronic warfare", "supplier"): "Amplis RF, processeurs signal, embarqué, antennes pour EW.",
    ("Intelligence / ISR", "supplier"): "Capteurs, payloads optroniques, communications pour plateformes ISR.",
    ("C4ISR", "supplier"): "Composants, sous-systèmes, software, services pour primes C4ISR.",
    ("Radar / sensors", "supplier"): "Front-ends RF, processeurs, mécanique de précision pour fabricants radars.",
    ("Optics / optronics", "supplier"): "Verre spécial, détecteurs, mécanique de précision pour optronique.",
    ("Communications", "supplier"): "RF, crypto, certification interopérabilité pour radios tactiques.",
    ("Armored vehicles", "supplier"): "Moteurs, composites, plaques balistiques, optronique, comms pour OEM blindés.",
    ("Weapons", "supplier"): "Usinage de précision, matériaux, optronique, composants munitions ITAR-friendly.",
    ("Ammunition", "supplier"): "Matériaux énergétiques, mécanique, packaging, certification.",
    ("Soldier systems", "supplier"): "Textiles, composites, optronique, énergie pour kits soldat.",
    ("Ballistic protection", "supplier"): "Aramide, céramique, composites, certification balistique.",
    ("Simulation / training", "supplier"): "Visuel, motion, haptique, IA pour fabricants de simulateurs.",
    ("Logistics / MRO", "supplier"): "Pièces de rechange, calibration, certification pour acteurs MRO.",
    ("Engineering services", "supplier"): "Outils CAD, simulation, certification pour bureau d'études.",
    ("Industrial subcontracting", "supplier"): "Matières premières, outils coupants, services qualité pour sous-traitants.",
    ("Naval defense", "supplier"): "Radars, sonars, comms, armement, sustainment pour primes navals.",
    ("Air defense", "supplier"): "Radars, missiles, comms, électroniques pour systèmes anti-aériens.",
    ("Land defense", "supplier"): "Sous-systèmes, composites, certification pour primes terrestres.",
    ("Homeland security", "supplier"): "Capteurs, comms, software, intégration pour police / frontière / sécu civile.",
    ("Space / satellite", "supplier"): "Composites, capteurs, comms, lancement pour primes spatiaux.",
    ("NRBC / CBRN", "supplier"): "Capteurs détection, filtration, EPI certifié.",
    ("Other", "supplier"): "Activité à qualifier — discovery call avant pitch précis.",
    # Distributor (any segment)
    ("__any__", "distributor"): "Canal de distribution / représentation pour notre portefeuille.",
    ("__any__", "qualify"): "Qualifier l'activité avant tout pitch ciblé.",
}


def _ideal_seller_profile(segment: str, role: str, builds: list[str]) -> str:
    profile = SELLER_PROFILE_BY_SEGMENT_ROLE.get((segment, role))
    if profile:
        return profile
    # role-only fallback
    if role in ("distributor", "qualify"):
        return SELLER_PROFILE_BY_SEGMENT_ROLE[("__any__", role)]
    if role == "buyer":
        sample = ", ".join(builds[:2]) if builds else "leur build line"
        return f"Vend des sous-systèmes / composants à un OEM construisant {sample}."
    if role == "partner":
        return f"Partenariat conjoint sur la chaîne {segment.lower()}."
    return SELLER_PROFILE_BY_SEGMENT_ROLE.get(("Other", "supplier"))


# ---------------------------------------------------------------------------
# Other derived helpers
# ---------------------------------------------------------------------------


def _primary_role(target_types_en: list[str]) -> str:
    """Mirror of sales_card._primary_role — duplicated here to avoid coupling."""
    if "potential_supplier" in target_types_en or "subcontractor" in target_types_en:
        return "supplier"
    if "potential_buyer" in target_types_en or "industrial_partner" in target_types_en or "prime_contractor" in target_types_en:
        return "buyer"
    if "technology_integrator" in target_types_en or "integrator" in target_types_en:
        return "partner"
    if "distributor" in target_types_en:
        return "distributor"
    return "qualify"


def _data_confidence(
    products_present: bool, buying_present: bool, segment_known: bool,
    sources_present: bool, score: Optional[float],
) -> str:
    score = score or 0
    signals = sum([products_present, buying_present, segment_known, sources_present])
    if signals == 4 and score >= 60:
        return "High"
    if signals >= 2:
        return "Medium"
    return "Low"


def _missing_critical(exh, intel) -> list[str]:
    out: list[str] = []
    if not exh.website_url:
        out.append("website")
    if not (intel and (intel.built_products or [])):
        out.append("products_built")
    if not (intel and (intel.probable_buying_needs or [])):
        out.append("buying_needs")
    if not (intel and intel.activity_summary):
        out.append("activity_summary")
    if not exh.country_iso2:
        out.append("country")
    return out


def _maturity_defense_level(score: Optional[float], confidence: str) -> str:
    if score is None:
        return TO_ENRICH
    if score >= 80 and confidence == "High":
        return "Mature"
    if score >= 60:
        return "Established"
    if score >= 40:
        return "Emerging"
    return "Embryonic"


def _description_short(intel) -> str:
    if not intel:
        return TO_ENRICH
    text = intel.activity_summary or ""
    text = " ".join(text.split())
    return (text[:240] + "…") if len(text) > 240 else (text or TO_ENRICH)


def _short_pitch(intel) -> str:
    """Single-sentence pitch (~200 chars) — first sentence of recommended_pitch."""
    if not intel or not intel.recommended_pitch:
        return TO_ENRICH
    return _truncate_sentence(intel.recommended_pitch, max_chars=200)


def _source_confidence(intel) -> str:
    fc = (intel.field_confidence or {}) if intel else {}
    if not fc:
        return "Low"
    counts = Counter(fc.values())
    most, _ = counts.most_common(1)[0]
    return most.capitalize() if most in {"high", "medium", "low"} else "Low"


def _manual_review_required(
    exh, intel, score: Optional[float], confidence: str, segment: str,
    buying_conf: Optional[str],
) -> bool:
    if exh.needs_review:
        return True
    if not exh.website_url:
        return True
    if not (intel and (intel.built_products or [])):
        return True
    if buying_conf == "low":
        return True
    if (score or 0) >= 75 and confidence == "Low":
        return True
    if segment == "Other":
        return True
    return False


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def to_crm(exh, intel, *, tags: list[str] | None = None,
           latest_note: Optional[str] = None,
           custom_lists: list[str] | None = None) -> dict[str, Any]:
    """Render a single CRM row from an Exhibitor + ExhibitorIntelligence."""
    builds = (intel.built_products if intel else []) or []
    sold = (intel.sold_offerings if intel else []) or []
    services = (intel.services if intel else []) or []
    techs = (intel.technologies if intel else []) or []
    clients = (intel.target_clients if intel else []) or []
    markets = (intel.markets_served if intel else []) or []
    defense_cats = (intel.defense_categories if intel else []) or []
    buying_needs = (intel.probable_buying_needs if intel else []) or []
    target_types = (intel.commercial_target_type if intel else []) or []
    objections = (intel.probable_objections if intel else []) or []
    keywords = (intel.prospecting_keywords if intel else []) or []
    fields_verify = (intel.fields_to_verify if intel else []) or []
    field_sources = (intel.field_sources if intel else {}) or {}

    segment_main = _pick_primary_category(defense_cats, builds) if defense_cats else "Other"
    secondary = [c for c in defense_cats if c != segment_main]
    role = _primary_role(target_types)

    score = intel.defense_commercial_score if intel else None
    priority = (intel.defense_priority_level if intel else None) or "D"
    confidence = _data_confidence(
        products_present=bool(builds),
        buying_present=bool(buying_needs),
        segment_known=segment_main != "Other",
        sources_present=bool(field_sources),
        score=score,
    )

    fr_target_types = target_types_to_french(target_types)
    primary_target_fr = fr_target_types[0] if fr_target_types else "À vérifier"

    booth = first_booth(exh.stands)
    raw_size = (intel.company_size_estimate if intel else None) or exh.employee_range
    emp_hint = intel.employee_count_hint if intel else None
    if emp_hint and raw_size:
        company_size = f"{raw_size} (~{emp_hint} employees)"
    elif emp_hint:
        company_size = f"~{emp_hint} employees"
    elif raw_size:
        company_size = raw_size
    else:
        company_size = TO_ENRICH
    founding_year = (intel.founding_year if intel else None) or None

    # ----- Narrative columns (built from existing fields, never invented) -----
    country_fr_label = country_fr(exh.country_iso2, fallback_name=exh.country_name)
    company_type_fr_label = company_type_fr(intel.business_model if intel else None)
    core_biz = _core_business(
        defense_segment_main=segment_main, built_products=builds,
    )
    products_services_str = _main_products_services(
        built_products=builds, sold_offerings=sold, services=services,
    )
    relevance_summary = _commercial_relevance_summary(
        target_type_fr=primary_target_fr,
        buying_need_main=(buying_needs[0] if buying_needs else None),
        defense_segment_main=segment_main,
        priority_level=priority,
    )
    pitch_full = _company_pitch(
        account_name=exh.company_name,
        country_fr=country_fr_label,
        defense_segment_main=segment_main,
        defense_segments_secondary_list=secondary,
        built_products=builds,
        sold_offerings=sold,
        services=services,
        company_type=intel.company_type if intel else None,
        target_type_fr=primary_target_fr,
        buying_need_main=(buying_needs[0] if buying_needs else None),
    )

    return {
        # IDENTIFICATION
        "account_id": f"ESY26-{exh.id:05d}",
        "account_name": exh.company_name,
        "country": country_fr(exh.country_iso2, fallback_name=exh.country_name),
        "country_iso2": exh.country_iso2,
        "city": exh.city,
        "website_url": exh.website_url,
        "linkedin_company_url": exh.linkedin_url,
        "eurosatory_profile_url": (intel.eurosatory_profile_url if intel else None),
        "booth_number": booth,
        "company_size": company_size,
        "founding_year": founding_year,
        "company_type": company_type_fr(intel.business_model if intel else None),
        "business_model": (intel.business_model if intel else None) or TO_ENRICH,
        "headline": (intel.headline if intel else None) or TO_ENRICH,
        "description_short": _description_short(intel),
        "source_urls": _join(sorted(set(field_sources.values()))[:5], sep=" | "),
        "last_checked_at": (intel.last_analyzed_at if intel else None),

        # QUALIFICATION
        "defense_segment_main": segment_main,
        "defense_segments_secondary": _join(secondary),
        "defense_subsegment": _join(secondary[:2]),
        "core_business": core_biz,
        "main_products_services": products_services_str,
        "certifications": _join((intel.certifications if intel else None) or []),
        "industry_associations": _join((intel.industry_associations if intel else None) or []),
        "parent_group": (intel.parent_group if intel else None) or "",
        "additional_offices": _join((intel.additional_offices if intel else None) or []),
        "products_built": _join(builds),
        "products_sold": _join(sold),
        "services_sold": _join(services),
        "technologies": _join(techs),
        "target_clients": _join(clients),
        "markets_served": _join(markets),
        "maturity_defense_level": _maturity_defense_level(score, confidence),

        # TARGETING
        "target_type": primary_target_fr,
        "all_target_types": _join(fr_target_types),
        "commercial_interest_level": (intel.commercial_interest_level if intel else None) or "low",
        "lead_score": round(score, 1) if score is not None else None,
        "priority_level": priority,
        "ideal_seller_profile": _ideal_seller_profile(segment_main, role, builds),
        "buying_need_main": buying_needs[0] if buying_needs else TO_ENRICH,
        "buying_needs_secondary": _join(buying_needs[1:]),
        "buying_need_confidence": (intel.buying_need_confidence if intel else None) or "low",
        "supplier_opportunity": "Yes" if "potential_supplier" in target_types or "subcontractor" in target_types else "No",
        "partnership_opportunity": "Yes" if "industrial_partner" in target_types else "No",
        "integration_opportunity": "Yes" if {"technology_integrator", "integrator"} & set(target_types) else "No",
        "distribution_opportunity": "Yes" if "distributor" in target_types else "No",
        "recommended_sales_angle": (intel.recommended_sales_angle if intel else None) or TO_ENRICH,
        "short_pitch": _short_pitch(intel),
        "company_pitch": pitch_full,
        "commercial_relevance_summary": relevance_summary,
        "objections_probables": _join(objections),
        "prospecting_keywords": _join(keywords, sep=", "),

        # PIPELINE
        "lead_status": lead_status(exh.status),
        "crm_stage": crm_stage(priority),
        "next_best_action": next_best_action(score),
        "next_action_date": exh.next_action_date,
        "is_favorite": bool(getattr(exh, "is_favorite", False)),
        "owner": exh.owner,
        "sales_team": exh.sales_team,
        "custom_list": _join(custom_lists or []),
        "tags": _join(tags or []),
        "notes": latest_note or "",
        "last_contact_date": exh.last_contact_date,
        "follow_up_status": None,

        # QUALITY
        "data_confidence": confidence,
        "fields_to_verify": _join(fields_verify),
        "missing_critical_fields": _join(_missing_critical(exh, intel)),
        "extraction_method": (intel.extraction_method if intel else None) or "rules",
        "source_confidence": _source_confidence(intel) if intel else "Low",
        "manual_review_required": _manual_review_required(
            exh, intel, score, confidence, segment_main,
            (intel.buying_need_confidence if intel else None),
        ),
    }
