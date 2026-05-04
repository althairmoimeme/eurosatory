from app.processors.defense_scorer import score_defense
from app.processors.defense_taxonomy import classify_defense, classify_defense_labels
from app.processors.intelligence import extract_intelligence
from app.processors.sales_card import derive_commercial_targeting


SAMPLE_TEXT = (
    "We design and manufacture battle-proven counter-UAV systems including RF "
    "disruption modules, jammers and anti-drone radars. Our solutions integrate "
    "AI-powered detection, GNSS denial and software defined radios for homeland "
    "security customers across Europe and the Middle East. Engineering services "
    "and MRO are also offered."
)


def test_intelligence_extract_counter_uav_company():
    res = extract_intelligence([("homepage", "https://example.com/", SAMPLE_TEXT)])
    assert "Counter-UAV systems" in res.built_products
    # buying needs derived from built products
    assert any("RF / microwave" in n or "Radars" in n for n in res.probable_buying_needs)
    # any of the manufacturing-leaning models is acceptable; "Engineering services"
    # may match first because the company explicitly offers them.
    assert res.business_model in {
        "OEM", "System integrator", "Manufacturer", "Equipment manufacturer",
        "Engineering services",
    }
    # rule-based may not catch "homeland security customers" verbatim — that's why
    # the LLM refine step exists. We do require MRO to surface from the explicit mention.
    assert "Maintenance / MRO" in res.sold_offerings


def test_defense_taxonomy_classifies_counter_uav():
    labels = classify_defense_labels(SAMPLE_TEXT)
    assert "Counter-UAV" in labels
    assert "AI / data" in labels


def test_defense_scorer_a_plus_for_strong_company():
    rules = extract_intelligence([("homepage", "https://example.com/", SAMPLE_TEXT)])
    labels = classify_defense_labels(SAMPLE_TEXT)
    score = score_defense(
        {
            "website_url": "https://example.com",
            "contact_email": "sales@example.com",
            "phone": "+33123456789",
            "linkedin_url": "https://linkedin.com/company/x",
            "address1": "Defense Park",
            "is_featured": True,
            "employee_range": "501-1000",
            "country_iso2": "DE",
        },
        {
            "defense_categories": labels,
            "built_products": rules.built_products,
            "sold_offerings": rules.sold_offerings,
            "technologies": rules.technologies,
            "markets_served": rules.markets_served,
            "probable_buying_needs": rules.probable_buying_needs,
            "buying_need_confidence": rules.buying_need_confidence,
            "business_model": "OEM",
            "services": rules.services,
            "activity_summary": rules.activity_summary,
        },
    )
    assert score.priority_level in {"A+", "A"}
    assert 70 <= score.total <= 100


def test_sales_card_targeting_handles_thin_input():
    targeting = derive_commercial_targeting(
        company_name="ACME",
        country_iso2="FR",
        business_model=None,
        built_products=[],
        sold_offerings=[],
        technologies=[],
        target_clients=[],
        markets_served=[],
        probable_buying_needs=[],
        activity_summary=None,
        defense_categories=["Other"],
        defense_score=20.0,
    )
    assert "to_qualify" in targeting.target_types
    assert targeting.interest_level == "low"
    assert targeting.summary_for_sales.count("\n") >= 4  # 5 lines


def test_classify_defense_returns_empty_for_unrelated_text():
    assert classify_defense("We sell organic chocolate") == []
