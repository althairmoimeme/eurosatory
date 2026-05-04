from app.processors.scoring import score_exhibitor


def test_priority_a_for_complete_strategic_company():
    exh = {
        "website_url": "https://acme.com",
        "contact_email": "sales@acme.com",
        "phone": "+33123456789",
        "linkedin_url": "https://linkedin.com/company/acme",
        "address1": "1 rue de la Défense",
        "city": "Paris",
        "is_featured": True,
        "is_new_exhibitor": True,
        "is_lab": True,
        "stands": [{"Hall": "Hall 5", "Name": "K100"}],
        "field_confidence": {"contact_email": "high"},
        "generic_sales_email": "sales@acme.com",
        "presentation": "We deliver AI-driven worldwide solutions to 50 countries.",
        "country_iso2": "DE",
        "employee_range": "1000+",
    }
    s = score_exhibitor(exh, ["Cybersécurité", "Intelligence artificielle"])
    assert s.total >= 75, s.breakdown
    assert s.priority_level == "A"


def test_priority_d_for_empty_record():
    exh = {}
    s = score_exhibitor(exh, [])
    assert s.priority_level == "D"
    assert s.total < 35


def test_score_breakdown_components_are_present():
    s = score_exhibitor({"website_url": "x"}, [])
    assert set(s.breakdown.keys()) == {
        "data_completeness",
        "sector_strategic",
        "size",
        "international",
        "eurosatory_signal",
        "lead_actionability",
    }
