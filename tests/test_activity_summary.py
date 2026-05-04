from app.processors.intelligence import (
    _looks_like_keyword_stuffing,
    _pick_prose_summary,
    extract_intelligence,
)


def test_keyword_stuffing_detector():
    keyword_stuffed = (
        "ISR aircraft solution, System Integration, Surveillance Aircraft, "
        "Special Mission Aircraft, Multi Mission Aircraft, Surveillance Platform, "
        "Mission Management Unit, Carbon Fibre, EASA Certification, Radomes, Bracket"
    )
    real_prose = (
        "Airborne Technologies designs and manufactures special-mission aircraft "
        "and ISR platforms for European and NATO customers. We integrate sensor "
        "suites, mission-management systems and certified airframe modifications."
    )
    assert _looks_like_keyword_stuffing(keyword_stuffed)
    assert not _looks_like_keyword_stuffing(real_prose)
    assert _looks_like_keyword_stuffing("")
    assert _looks_like_keyword_stuffing("Two short")


def test_pick_prose_summary_prefers_homepage_with_company_mention():
    pages = [
        ("homepage", "https://acme.example/", (
            "Cookie policy applies. ACME Defense designs counter-UAV systems "
            "for homeland security customers worldwide. Our radar-fused effectors "
            "have been deployed in three NATO programmes since 2024. "
            "Subscribe to our newsletter for updates."
        )),
        ("about", "https://acme.example/about", (
            "About ACME Defense. We are headquartered in Toulouse and employ "
            "over 250 engineers focused on RF, optronics and AI for defense markets. "
            "Founded in 1998, we serve customers in 18 countries."
        )),
    ]
    summary = _pick_prose_summary(pages, company_name_hint="ACME Defense")
    assert summary is not None
    assert "ACME Defense" in summary
    assert "Cookie" not in summary
    assert "Subscribe" not in summary


def test_extract_intelligence_uses_prose_when_eurosatory_is_keyword_stuffed():
    eurosatory = (
        "ISR aircraft, System Integration, Surveillance Aircraft, Mission "
        "Management Unit, SCAR-Pod, Carbon Fibre, Workstation, Camera"
    )
    homepage_text = (
        "Airborne Technologies builds special-mission aircraft, certified "
        "modifications and ISR platforms for European armed forces. "
        "Our mission-management systems are deployed in over a dozen countries."
    )
    res = extract_intelligence(
        [("homepage", "https://airbornetech.example/", homepage_text)],
        base_summary=eurosatory,
        company_name_hint="Airborne Technologies",
    )
    assert res.activity_summary is not None
    assert "Airborne Technologies" in res.activity_summary
    # the keyword-stuffed Eurosatory line should NOT have been kept
    assert "SCAR-Pod" not in res.activity_summary
