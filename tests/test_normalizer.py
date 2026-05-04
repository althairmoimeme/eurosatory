from app.processors.normalizer import (
    is_generic_email,
    make_slug,
    merge_finderr_detail,
    normalize_company_name,
    normalize_country_code,
    normalize_email,
    normalize_finderr_search_row,
    normalize_phone,
    normalize_website,
    website_canonical_key,
)


def test_normalize_website_strips_quotes_and_adds_scheme():
    assert normalize_website("ntservice.eu").startswith("http://ntservice.eu")
    assert normalize_website("https://www.aselsan.com/").startswith("https://www.aselsan.com")


def test_website_canonical_key_collisions():
    assert website_canonical_key("https://www.aselsan.com/") == "aselsan.com"
    assert website_canonical_key("aselsan.com") == "aselsan.com"
    assert website_canonical_key("http://aselsan.com/about") == "aselsan.com"


def test_normalize_email_and_generic():
    assert normalize_email(" Sales@Foo.com ") == "sales@foo.com"
    assert is_generic_email("contact@foo.com")
    assert is_generic_email("info@foo.com")
    assert not is_generic_email("john.doe@foo.com")


def test_normalize_phone():
    p = normalize_phone(" +33 (0)1 23 45 67 89 ")
    assert p == "+330123456789"
    assert p.startswith("+33")
    assert all(c.isdigit() or c == "+" for c in p)
    assert normalize_phone("12") is None
    assert normalize_phone(None) is None
    assert normalize_phone("") is None


def test_normalize_country_code():
    assert normalize_country_code("fr") == "FR"
    assert normalize_country_code(None) is None


def test_make_slug():
    assert make_slug("\"NT SERVICE\" UAB") == "nt-service-uab"


def test_normalize_finderr_search_row():
    row = {
        "Exhi_Guid": "abc",
        "Exhi_ExternalId": "X1",
        "Exhi_CompanyName": "  ACME Defense Ltd ",
        "Exhi_CompanyName2": "ACME",
        "Exhi_Country_CodeISO2": "FR",
        "Exhi_Phone": " +33 1 ",
        "Exhi_Website": "acme-defense.fr",
        "ShortPresentation": "We make tanks.",
        "OneLiner": "Tanks!",
        "BusinessAreas": ["DEFENSE"],
        "Stands": [{"Hall": "Hall 5", "Name": "K100"}],
        "Categories": [1, 2, 3],
        "Exhi_IsLab": False,
    }
    out = normalize_finderr_search_row(row)
    assert out["finderr_guid"] == "abc"
    assert out["company_name"] == "ACME Defense Ltd"
    assert out["country_iso2"] == "FR"
    assert out["website_url"].startswith("http://acme-defense.fr")
    assert out["website_url_normalized"] == "acme-defense.fr"
    assert out["business_areas"] == ["DEFENSE"]
    assert out["raw_search_payload"]["Exhi_Guid"] == "abc"


def test_merge_finderr_detail_keeps_existing_values():
    target = {"contact_email": "a@b.com", "city": None, "linkedin_url": None}
    detail = {
        "Exhi_ContactEmail": "contact@bar.com",
        "Exhi_City": "Lyon",
        "SocialNetworks": [{"Type": "LINKEDIN", "URL": "https://linkedin.com/x"}],
        "Presentation": "NOT AVAILABLE LANGUAGE",
        "Contacts": [],
        "Categories": [10],
        "CategoriesLabels": ["AI"],
    }
    merged = merge_finderr_detail(target, detail)
    assert merged["contact_email"] == "a@b.com"  # preserved
    assert merged["city"] == "Lyon"
    assert merged["linkedin_url"] == "https://linkedin.com/x"
    assert "presentation" not in merged or not merged.get("presentation")
    assert merged["_categories_labels"] == [(10, "AI")]
