from app.attendance.dedup import (
    canonical_company_name,
    canonical_person_name,
    dedupe_key,
)


def test_canonical_company_strips_suffixes_and_accents():
    assert canonical_company_name("ACME Defense Ltd") == "acme defense"
    assert canonical_company_name("Schöllhammer GmbH") == "schollhammer"
    assert canonical_company_name("ALPHA AFFINITY GMBH") == "alpha affinity"
    assert canonical_company_name("\"NT SERVICE\" UAB") == "nt service"
    assert canonical_company_name("Société Générale Defense, SAS") == "societe generale defense"
    assert canonical_company_name(None) is None
    assert canonical_company_name("") is None


def test_canonical_person_strips_titles_and_accents():
    assert canonical_person_name("Mr. John Doe") == "john doe"
    assert canonical_person_name("Dr Hélène Müller") == "helene muller"
    assert canonical_person_name("  Capt. James KIRK ") == "james kirk"
    assert canonical_person_name(None) is None


def test_dedupe_key_is_stable_and_distinct():
    k1 = dedupe_key(canonical_person="john doe", canonical_company="acme defense", edition_year=2026)
    k2 = dedupe_key(canonical_person="john doe", canonical_company="acme defense", edition_year=2026)
    k3 = dedupe_key(canonical_person="john doe", canonical_company="acme defense", edition_year=2024)
    k4 = dedupe_key(canonical_person="jane doe", canonical_company="acme defense", edition_year=2026)
    assert k1 == k2
    assert k1 != k3
    assert k1 != k4
    assert len(k1) == 24
