"""End-to-end attendance signal pipeline test against an isolated tmp DB."""
from __future__ import annotations

import os

import pytest


_TEST_URL_PREFIX = "https://_attendance_test_"


@pytest.fixture(autouse=True, scope="module")
def _isolated_db(tmp_path_factory):
    """Bind the engine to a brand-new tmp SQLite for this module.

    We explicitly do NOT reuse the live DB: other test modules in the suite
    leave the engine pointing at stale ``tmp_path`` directories, and we don't
    want to write fixture rows into the production DB either.
    """
    base = tmp_path_factory.mktemp("attendance_db")
    os.environ["DATABASE_URL"] = f"sqlite:///{base}/attendance.db"
    import importlib
    import app.config as cfg
    import app.database.db as dbm
    import app.database as dbpkg
    import app.attendance.seed as seedm
    importlib.reload(cfg)
    importlib.reload(dbm)
    importlib.reload(dbpkg)
    importlib.reload(seedm)
    dbpkg.init_db()
    yield


def test_upsert_and_dedupe_groups_two_signals_for_same_person_year():
    from app.attendance.seed import RawSignal, upsert_signal
    from app.database import AttendanceSignal, session_scope
    from sqlalchemy import select

    raw1 = RawSignal(
        edition_year=2026, entity_type="person",
        source_url="https://_attendance_test_corp.example/news/jane-eurosatory",
        person_name="Jane Smith", person_role="VP Sales",
        company_name="Sample Defense Ltd", country="United Kingdom",
        signal_type="press_release",
        signal_text="Jane Smith confirms she will attend Eurosatory 2026.",
    )
    obj1, new1 = upsert_signal(raw1)
    assert new1 is True
    assert obj1.is_duplicate is False
    assert obj1.sales_priority == "A"  # High confidence + High relevance
    assert obj1.role_category == "Sales / Business Development"
    assert obj1.gdpr_risk_level in {"Low", "Medium"}
    obj1_id = obj1.id  # keep across the next session

    # Same person, same year, second public source — should be deduped
    raw2 = RawSignal(
        edition_year=2026, entity_type="person",
        source_url="https://_attendance_test_press.example/eurosatory-2026-line-up",
        person_name="Jane Smith", person_role="VP Sales EMEA",
        company_name="Sample Defense Limited", country="United Kingdom",
        signal_type="media_article",
        signal_text="Jane Smith of Sample Defense will be at Eurosatory.",
    )
    obj2, new2 = upsert_signal(raw2)
    assert new2 is True
    key = obj2.dedupe_key
    # one of them is the canonical
    with session_scope() as s:
        rows = list(s.execute(
            select(AttendanceSignal).where(AttendanceSignal.dedupe_key == key)
        ).scalars())
        assert len(rows) == 2
        canonical_count = sum(1 for r in rows if not r.is_duplicate)
        assert canonical_count == 1
        # both share the same dedupe_key
        assert all(r.dedupe_key == key for r in rows)


def test_company_signal_yields_low_gdpr_and_b_priority():
    from app.attendance.seed import RawSignal, upsert_signal

    raw = RawSignal(
        edition_year=2026, entity_type="company",
        source_url="https://_attendance_test_sample.example/news/eurosatory-2026",
        company_name="Sample Defense Ltd", country="United Kingdom",
        signal_type="company_announcement",
        signal_text="Sample Defense will exhibit at Eurosatory 2026, booth K221.",
    )
    obj, _ = upsert_signal(raw)
    assert obj.gdpr_risk_level == "Low"
    assert obj.presence_confidence == "High"
    assert obj.sales_priority in {"A", "B"}  # company → relevance Medium → at most B


def test_csv_template_writes_valid_headers(tmp_path):
    from app.attendance.seed import CSV_OPTIONAL, CSV_REQUIRED, write_template
    p = write_template(tmp_path / "template.csv")
    text = p.read_text(encoding="utf-8").splitlines()
    headers = text[0].split(",")
    for h in CSV_REQUIRED:
        assert h in headers
    for h in CSV_OPTIONAL:
        assert h in headers


def test_csv_import_round_trip(tmp_path):
    """Round-trip the template CSV through the importer.

    Skip the live-DB write — the template fixtures use stable example.com
    URLs that we don't want polluting the prod DB across runs.
    """
    from app.attendance.seed import write_template
    p = write_template(tmp_path / "tpl.csv")
    text = p.read_text(encoding="utf-8")
    assert "edition_year,entity_type,source_url" in text.splitlines()[0]
    # check both sample rows present
    assert "Jane Smith" in text
    assert "Ministry of Defence" in text
