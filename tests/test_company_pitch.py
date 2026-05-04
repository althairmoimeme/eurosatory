from app.crm.pitch import (
    TO_ENRICH,
    commercial_relevance_summary,
    company_pitch,
    core_business,
    main_products_services,
)


def test_core_business_combines_segment_and_products():
    out = core_business(
        defense_segment_main="Counter-UAV",
        built_products=["Counter-UAV systems", "Sensors"],
    )
    assert out == "Systèmes anti-drones (systèmes anti-drones et capteurs)"


def test_core_business_segment_only_when_no_products():
    out = core_business(
        defense_segment_main="Cybersecurity",
        built_products=[],
    )
    assert out == "Cybersécurité défense"


def test_core_business_to_enrich_when_nothing():
    out = core_business(defense_segment_main=None, built_products=[])
    assert out == TO_ENRICH

    out2 = core_business(defense_segment_main="Other", built_products=[])
    assert out2 == TO_ENRICH


def test_main_products_services_renders_chunks():
    out = main_products_services(
        built_products=["UAV / drones", "Sensors"],
        sold_offerings=[],
        services=["Maintenance / MRO", "Training"],
    )
    assert "Produits : drones, capteurs" in out
    assert "Services : maintenance / MCO, formation" in out


def test_main_products_services_empty_falls_back():
    assert main_products_services(built_products=[], sold_offerings=[], services=[]) == TO_ENRICH


def test_commercial_relevance_summary_supplier():
    out = commercial_relevance_summary(
        target_type_fr="Fournisseur potentiel",
        buying_need_main="Radars",
        defense_segment_main="Counter-UAV",
        priority_level="A",
    )
    assert "Fournisseur potentiel" in out
    assert "priorité élevée" in out
    assert "anti-drone" in out


def test_commercial_relevance_summary_buyer_with_need():
    out = commercial_relevance_summary(
        target_type_fr="Acheteur potentiel",
        buying_need_main="Energy / batteries",
        defense_segment_main="UAV / drones",
        priority_level="A+",
    )
    assert "Acheteur potentiel" in out
    assert "priorité maximale" in out
    # FR-localised buying need
    assert "énergie embarquée" in out


def test_company_pitch_full_record():
    # ``country_fr`` is the noun form (e.g. "France"), as produced by
    # ``app.crm.normalizers.country_fr`` and used by the transformer.
    pitch = company_pitch(
        account_name="ACME Defense",
        country_fr="France",
        defense_segment_main="Counter-UAV",
        defense_segments_secondary_list=["Electronic warfare", "Homeland security"],
        built_products=["Counter-UAV systems", "Radars", "Sensors"],
        sold_offerings=["Sub-systems / Components", "Integration services"],
        services=[],
        company_type="Manufacturer",
        target_type_fr="Fournisseur potentiel",
        buying_need_main="RF / microwave components",
    )
    lines = pitch.split("\n")
    assert len(lines) == 4
    assert "ACME Defense est une société basée en France" in lines[0]
    assert "spécialisée dans systèmes anti-drones." in lines[0]
    assert "Elle fabrique" in lines[1]
    assert "Ses cœurs de métier couvrent" in lines[2]
    assert "Commercialement" in lines[3]
    assert "fournisseur" in lines[3].lower()


def test_company_pitch_minimal_falls_back():
    pitch = company_pitch(
        account_name="Mystery Corp", country_fr=None,
        defense_segment_main=None, defense_segments_secondary_list=[],
        built_products=[], sold_offerings=[], services=[],
        company_type=None, target_type_fr="À vérifier",
        buying_need_main=None,
    )
    assert "À enrichir" in pitch


def test_company_pitch_segment_only_no_products():
    pitch = company_pitch(
        account_name="Cyber Ltd", country_fr="Royaume-Uni",
        defense_segment_main="Cybersecurity",
        defense_segments_secondary_list=["AI / data"],
        built_products=[], sold_offerings=[], services=[],
        company_type=None, target_type_fr="Partenaire industriel",
        buying_need_main=None,
    )
    assert "Cyber Ltd est une société basée en Royaume-Uni" in pitch
    assert "cybersécurité défense" in pitch
    assert "Ses cœurs de métier" in pitch  # secondary segment kicks in
    assert "partenariats" in pitch
