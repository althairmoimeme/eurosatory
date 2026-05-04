"""Integration test: ingest a fixture payload through the real pipeline against
an in-memory SQLite database.  No network calls.
"""
from __future__ import annotations

import os
from datetime import datetime

# Force a temp database BEFORE importing the app (settings reads env at import).
os.environ.setdefault("DATABASE_URL", "sqlite:///./_tests/eurosatory_test.db")

import pytest
from sqlalchemy import select


SAMPLE_SEARCH_ROW = {
    "Exhi_Guid": "test-guid-1",
    "Exhi_ExternalId": "X1",
    "Exhi_CompanyName": "Acme Drones",
    "Exhi_CompanyName2": "ACME",
    "Exhi_Country_CodeISO2": "FR",
    "Exhi_Country_Name": "France",
    "Exhi_Phone": "+33123456789",
    "Exhi_Website": "acme-drones.fr",
    "ShortPresentation": "Counter-UAV anti-drone RF disruption systems with AI tracking.",
    "OneLiner": "AI-powered counter-UAV.",
    "BusinessAreas": ["DEFENSE"],
    "Stands": [{"Hall": "Hall 5a", "Name": "K221", "MapPoint": "K221"}],
    "Categories": [1138, 1176],
    "Exhi_IsLab": False,
    "Exhi_IsNewExhibitor": True,
    "IsFeatured": True,
    "Exhi_Logo": "https://logo.example/x.png",
}


@pytest.fixture(autouse=True, scope="module")
def _prepare_db(tmp_path_factory):
    saved_url = os.environ.get("DATABASE_URL")
    base = tmp_path_factory.mktemp("eurodb")
    os.environ["DATABASE_URL"] = f"sqlite:///{base}/eurosatory_test.db"
    import importlib

    import app.config as cfg
    importlib.reload(cfg)
    import app.database.db as dbm
    importlib.reload(dbm)
    import app.database as dbpkg
    importlib.reload(dbpkg)
    dbpkg.init_db()
    yield
    # Restore prior DATABASE_URL and engine so later test modules don't
    # accidentally hit the now-deleted tmp DB.
    if saved_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = saved_url
    importlib.reload(cfg)
    importlib.reload(dbm)
    importlib.reload(dbpkg)


def test_ingest_search_row_then_classify_and_score():
    from app.database import (
        Category,
        Country,
        Exhibitor,
        ExhibitorClassification,
        session_scope,
    )
    from app.pipelines.classify import _classify_one
    from app.pipelines.scrape import _upsert_country, _upsert_exhibitor

    with session_scope() as s:
        _upsert_country(
            s, {"CountryID": 1942, "CodeISO2": "FR", "LabelEN": "France", "LabelFR": "France"}
        )
        s.add(Category(finderr_id=1138, code="N1", label_en="Drones", label_fr="Drones"))
        s.add(Category(finderr_id=1176, code="N2", label_en="Anti-UAV", label_fr="Anti-UAV"))
        s.flush()
        exh = _upsert_exhibitor(s, SAMPLE_SEARCH_ROW, run_id=0)
        assert exh is not None
        exh_id = exh.id

    with session_scope() as s:
        exh = s.get(Exhibitor, exh_id)
        assert exh.company_name == "Acme Drones"
        assert exh.website_url_normalized == "acme-drones.fr"
        assert exh.country_iso2 == "FR"
        _classify_one(s, exh)

    with session_scope() as s:
        exh = s.get(Exhibitor, exh_id)
        labels = [
            r.label
            for r in s.execute(
                select(ExhibitorClassification).where(ExhibitorClassification.exhibitor_id == exh.id)
            ).scalars()
        ]
        assert "Anti-drones" in labels
        assert exh.commercial_relevance_score is not None
        assert exh.priority_level in {"A", "B", "C", "D"}
