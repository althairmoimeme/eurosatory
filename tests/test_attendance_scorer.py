from app.attendance.scorer import (
    UNKNOWN_ROLE,
    commercial_relevance,
    compute_presence_score,
    gdpr_risk_level,
    initial_validation_status,
    meeting_potential,
    next_best_action,
    presence_confidence,
    role_category,
    sales_priority,
)


def test_role_category_normalises_titles():
    assert role_category("Chief Executive Officer") == "CEO / Founder"
    assert role_category("VP Sales EMEA") == "Sales / Business Development"
    assert role_category("Procurement Manager") == "Procurement / Purchasing"
    assert role_category("Head of Partnerships") == "Partnerships"
    assert role_category("Senior Program Manager") == "Program / Project Manager"
    assert role_category("Colonel, French Army") == "Defense / Military"
    assert role_category("Senior Software Engineer") == "Engineering / Technical"
    assert role_category("Marketing Director") == "Marketing / Communications"
    # "Defense Attaché" matches Defense / Military first — that's acceptable
    assert role_category("Defense Attaché") in {"Defense / Military", "Institutional / Government"}
    assert role_category("Cultural Attaché") == "Institutional / Government"
    assert role_category("Defense Reporter") == "Media / Press"
    assert role_category(None) == "Unknown"


def test_presence_score_explicit_attendance():
    p = compute_presence_score(
        signal_text="We will attend Eurosatory 2026 — meet us at booth K221!",
        signal_type="personal_linkedin_post",
    )
    assert p.score == 95
    assert "Explicit" in p.reason


def test_presence_score_past_presence():
    p = compute_presence_score(
        signal_text="Thank you for visiting our stand! Great week at Eurosatory.",
        signal_type="company_announcement",
    )
    assert p.score == 90
    assert "Past-presence" in p.reason


def test_presence_score_official_delegation():
    # When the snippet has no explicit-attendance phrase, the official
    # delegation flag pins the floor at 80.
    p = compute_presence_score(
        signal_text="Press release: UK delegation at Eurosatory 2026.",
        signal_type="official_delegation",
        is_official_delegation=True,
    )
    assert p.score == 80


def test_presence_score_company_only():
    p = compute_presence_score(
        signal_text="Visit our latest UAV at the Defense Show.",
        signal_type="company_announcement",
        person_name=None,
    )
    assert p.score == 65


def test_presence_score_repost():
    p = compute_presence_score(
        signal_text="Shared this post about Eurosatory",
        signal_type="social_post",
    )
    assert p.score == 45


def test_presence_score_hashtag_only():
    p = compute_presence_score(
        signal_text="#Eurosatory2026", signal_type="social_post",
    )
    assert p.score == 20


def test_presence_confidence():
    assert presence_confidence(95) == "High"
    assert presence_confidence(80) == "High"
    assert presence_confidence(65) == "Medium"
    assert presence_confidence(40) == "Low"


def test_commercial_relevance():
    assert commercial_relevance("CEO / Founder", "person") == "High"
    assert commercial_relevance("Procurement / Purchasing", "person") == "High"
    assert commercial_relevance("Engineering / Technical", "person") == "Medium"
    assert commercial_relevance("Media / Press", "person") == "Low"
    assert commercial_relevance("Unknown", "company") == "Medium"


def test_sales_priority_matrix():
    assert sales_priority("High", "High") == "A"
    assert sales_priority("High", "Medium") == "B"
    assert sales_priority("Medium", "High") == "B"
    assert sales_priority("Medium", "Medium") == "C"
    assert sales_priority("Low", "Low") == "D"


def test_meeting_potential():
    assert meeting_potential("A", "CEO / Founder") == "High"
    assert meeting_potential("B", "Engineering / Technical") == "Medium"
    assert meeting_potential("D", "Unknown") == "Low"


def test_next_best_action_decision_tree():
    assert next_best_action("High", "High", "person") == "Add to pre-show outreach list"
    assert next_best_action("High", "Medium", "company") == "Find decision maker and qualify"
    assert next_best_action("Medium", "Medium", "person") == "Validate attendance manually"
    assert next_best_action("Low", "Low", "person") == "Keep in watchlist"


def test_gdpr_risk_level_rules():
    assert gdpr_risk_level("company", 95) == "Low"
    assert gdpr_risk_level("delegation", 95) == "Low"
    assert gdpr_risk_level("person", 80) == "Medium"
    assert gdpr_risk_level("person", 30) == "High"


def test_initial_validation_status_for_high_gdpr():
    assert initial_validation_status("High") == "Review required"
    assert initial_validation_status("Low") == "Pending"
