"""Unit tests for the CRM transformer + normalizers — fully offline.

We build fake Exhibitor / ExhibitorIntelligence objects (just plain attribute
holders) so we don't need a live DB.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

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
from app.crm.transformer import to_crm


def _fake(exh_attrs: dict, intel_attrs: dict | None):
    """Build mock Exhibitor + Intelligence with sensible defaults."""
    exh = SimpleNamespace(
        id=1,
        company_name="ACME Defense",
        country_iso2="FR",
        country_name="France",
        city="Toulouse",
        website_url="https://acme-defense.fr",
        linkedin_url="https://linkedin.com/company/acme",
        contact_email=None,
        phone=None,
        address1=None,
        stands=[{"Hall": "Hall 5", "Name": "K221"}],
        is_featured=False,
        is_new_exhibitor=False,
        is_lab=False,
        status="new",
        needs_review=False,
        employee_range=None,
        owner=None,
        sales_team=None,
        last_contact_date=None,
        next_action_date=None,
        is_favorite=False,
    )
    for k, v in exh_attrs.items():
        setattr(exh, k, v)

    if intel_attrs is None:
        return exh, None

    intel = SimpleNamespace(
        eurosatory_profile_url="https://eurosatory.finderr.cloud/...",
        headline=None,
        founding_year=None,
        employee_count_hint=None,
        certifications=None,
        industry_associations=None,
        parent_group=None,
        additional_offices=None,
        activity_summary="ACME Defense designs counter-UAV systems.",
        built_products=["Counter-UAV systems", "Sensors"],
        sold_offerings=["Sub-systems / Components"],
        services=[],
        technologies=["RF / microwave"],
        target_clients=["Land forces"],
        markets_served=["Europe"],
        defense_categories=["Counter-UAV"],
        business_model="OEM",
        company_type="Manufacturer",
        company_size_estimate="51-200",
        probable_buying_needs=["Radars", "RF / microwave components"],
        buying_need_confidence="high",
        commercial_target_type=["potential_supplier", "industrial_partner"],
        commercial_interest_level="high",
        defense_commercial_score=85.0,
        defense_priority_level="A",
        recommended_pitch="Hi — your counter-UAV stack uses RF and radars. We supply qualified front-ends.",
        recommended_sales_angle="Position as supplier.",
        probable_objections=["AVL closed", "Long ITAR cycle"],
        prospecting_keywords=["Counter-UAV", "RF"],
        fields_to_verify=[],
        field_sources={"contact_email": "https://acme-defense.fr/contact"},
        field_confidence={"contact_email": "high", "linkedin_url": "high"},
        extraction_method="rules",
        last_analyzed_at=datetime(2026, 4, 28, 9, 0, 0),
    )
    for k, v in intel_attrs.items():
        setattr(intel, k, v)
    return exh, intel


def test_country_fr_known():
    assert country_fr("FR") == "France"
    assert country_fr("DE") == "Allemagne"
    assert country_fr("XX", fallback_name="Latveria") == "Latveria"


def test_company_type_fr():
    assert company_type_fr("OEM") == "OEM"
    assert company_type_fr("Sub-contractor") == "Sous-traitant industriel"
    assert company_type_fr(None) == "À vérifier"
    assert company_type_fr("Something") == "Autre"


def test_target_type_fr():
    assert target_type_fr("potential_buyer") == "Acheteur potentiel"
    assert target_type_fr(None) == "À vérifier"


def test_target_types_to_french_dedupes():
    out = target_types_to_french(["potential_buyer", "industrial_partner", "integrator", "technology_integrator"])
    # technology_integrator and integrator both map to "Intégrateur potentiel"
    assert out.count("Intégrateur potentiel") == 1


def test_lead_status_default_new():
    assert lead_status(None) == "New"
    assert lead_status("contacted") == "Contacted"


def test_crm_stage_by_priority():
    assert crm_stage("A+") == "Priority targeting"
    assert crm_stage("A") == "Priority targeting"
    assert crm_stage("B") == "Qualification"
    assert crm_stage("C") == "Watchlist"
    assert crm_stage("D") == "Low priority"


def test_next_best_action_thresholds():
    assert next_best_action(95) == "Book meeting before Eurosatory"
    assert next_best_action(80) == "Contact sales team and qualify need"
    assert next_best_action(65) == "Review website and validate opportunity"
    assert next_best_action(50) == "Keep in watchlist"
    assert next_best_action(20) == "No immediate action"
    assert next_best_action(None) == "No immediate action"


def test_first_booth_handles_empty():
    assert first_booth(None) is None
    assert first_booth([]) is None
    assert first_booth([{"Hall": "Hall 5a", "Name": "K221"}]) == "Hall 5a / K221"


def test_to_crm_full_row_is_complete():
    exh, intel = _fake({}, {})
    row = to_crm(exh, intel)
    assert row["account_id"] == "ESY26-00001"
    assert row["account_name"] == "ACME Defense"
    assert row["country"] == "France"
    assert row["company_type"] == "OEM"
    assert row["defense_segment_main"] == "Counter-UAV"
    assert row["target_type"] == "Fournisseur potentiel"
    assert "industrial_partner" not in row["all_target_types"]  # mapped to FR
    assert "Partenaire industriel" in row["all_target_types"]
    assert row["lead_score"] == 85.0
    assert row["priority_level"] == "A"
    assert row["crm_stage"] == "Priority targeting"
    assert row["next_best_action"] == "Contact sales team and qualify need"
    assert row["lead_status"] == "New"
    assert row["data_confidence"] == "High"
    assert row["manual_review_required"] is False
    assert row["short_pitch"].startswith("Hi")
    assert row["supplier_opportunity"] == "Yes"
    assert row["partnership_opportunity"] == "Yes"
    assert row["distribution_opportunity"] == "No"
    assert row["booth_number"] == "Hall 5 / K221"


def test_to_crm_handles_missing_intel():
    exh, _ = _fake({"website_url": None}, None)
    row = to_crm(exh, None)
    assert row["defense_segment_main"] == "Other"
    assert row["lead_score"] is None
    assert row["priority_level"] == "D"
    assert row["data_confidence"] == "Low"
    assert row["manual_review_required"] is True
    assert "website" in row["missing_critical_fields"]


def test_to_crm_low_buying_confidence_triggers_review():
    exh, intel = _fake({}, {"buying_need_confidence": "low"})
    row = to_crm(exh, intel)
    assert row["manual_review_required"] is True


def test_to_crm_high_score_low_source_triggers_review():
    # all signals stripped → data_confidence = Low + score >= 75 → review required
    exh, intel = _fake({}, {
        "defense_commercial_score": 80.0,
        "defense_priority_level": "A",
        "built_products": [],
        "probable_buying_needs": [],
        "field_sources": {},
        "field_confidence": {"x": "low"},
    })
    row = to_crm(exh, intel)
    assert row["data_confidence"] == "Low"
    assert row["manual_review_required"] is True


def test_to_crm_other_segment_triggers_review():
    exh, intel = _fake({}, {
        "defense_categories": ["Other"],
        "built_products": [],
    })
    row = to_crm(exh, intel)
    assert row["defense_segment_main"] == "Other"
    assert row["manual_review_required"] is True


def test_to_crm_with_tags_and_lists_renders_them():
    exh, intel = _fake({}, {})
    row = to_crm(
        exh, intel,
        tags=["drone", "buyer"],
        latest_note="First-pass research done.",
        custom_lists=["High priority targets"],
    )
    assert "drone" in row["tags"]
    assert row["notes"] == "First-pass research done."
    assert "High priority targets" in row["custom_list"]
