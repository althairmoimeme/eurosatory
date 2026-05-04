from app.processors.deduplicator import (
    cluster_by_name_country,
    cluster_by_website,
    find_duplicates,
)


SAMPLE = [
    {"id": 1, "company_name": "ACME Defense", "website_url_normalized": "acme.com", "country_iso2": "FR"},
    {"id": 2, "company_name": "ACME Defense Ltd", "website_url_normalized": "acme.com", "country_iso2": "FR"},
    {"id": 3, "company_name": "Globex", "website_url_normalized": "globex.io", "country_iso2": "DE"},
    {"id": 4, "company_name": "Acme  Defense", "website_url_normalized": None, "country_iso2": "FR"},
    {"id": 5, "company_name": "Globex GmbH", "website_url_normalized": None, "country_iso2": "DE"},
]


def test_website_clusters_pick_up_collisions():
    clusters = cluster_by_website(SAMPLE)
    assert any({1, 2}.issubset(set(c.members)) for c in clusters)


def test_name_country_clusters_collapse_variants():
    clusters = cluster_by_name_country(SAMPLE)
    member_sets = [set(c.members) for c in clusters]
    assert any({1, 4}.issubset(s) for s in member_sets) or any({2, 4}.issubset(s) for s in member_sets)


def test_find_duplicates_combines_methods():
    clusters = find_duplicates(SAMPLE)
    methods = {c.method for c in clusters}
    assert "website" in methods
    # at least one fuzzy or name+country match for Globex variants
    assert any(c.method in ("fuzzy_name", "name+country") and 5 in c.members for c in clusters)
