"""Regression tests for the LeadForges multi-buyer auth module.

Covers :
  - Buyer dataclass & expiry math
  - Password compare (constant-time)
  - Multi-buyer login attempts
  - Date coercion (TOML / ISO / Python date)
  - Helpers for favorites (session-scoped)
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest


# ── Buyer dataclass ─────────────────────────────────────────────────────


def test_buyer_days_left_future():
    from app.ui.auth import Buyer
    b = Buyer(name="X", expires=date.today() + timedelta(days=30))
    assert b.days_left == 30
    assert not b.is_expired


def test_buyer_days_left_today():
    from app.ui.auth import Buyer
    b = Buyer(name="X", expires=date.today())
    assert b.days_left == 0
    assert not b.is_expired  # today still valid


def test_buyer_expired():
    from app.ui.auth import Buyer
    b = Buyer(name="X", expires=date.today() - timedelta(days=1))
    assert b.is_expired
    assert b.days_left == -1


# ── Date coercion ───────────────────────────────────────────────────────


def test_coerce_date_iso_string():
    from app.ui.auth import _coerce_date
    assert _coerce_date("2026-11-30") == date(2026, 11, 30)


def test_coerce_date_python_date():
    from app.ui.auth import _coerce_date
    d = date(2026, 11, 30)
    assert _coerce_date(d) == d


def test_coerce_date_malformed():
    from app.ui.auth import _coerce_date
    assert _coerce_date("not-a-date") is None
    assert _coerce_date(None) is None
    assert _coerce_date(12345) is None


# ── Multi-buyer login ───────────────────────────────────────────────────


_FAKE_BUYERS = {
    "ValidPassword12345": __import__(
        "app.ui.auth", fromlist=["Buyer"]
    ).Buyer(name="Thales Group", expires=date.today() + timedelta(days=100)),
    "ExpiredPassword678": __import__(
        "app.ui.auth", fromlist=["Buyer"]
    ).Buyer(name="Old Buyer", expires=date.today() - timedelta(days=1)),
}


def test_attempt_login_valid():
    from app.ui import auth
    with patch.object(auth, "_load_buyers", return_value=_FAKE_BUYERS), \
         patch.object(auth, "_legacy_password", return_value=""):
        ok, buyer = auth._attempt_login("ValidPassword12345")
        assert ok is True
        assert buyer is not None
        assert buyer.name == "Thales Group"


def test_attempt_login_invalid():
    from app.ui import auth
    with patch.object(auth, "_load_buyers", return_value=_FAKE_BUYERS), \
         patch.object(auth, "_legacy_password", return_value=""):
        ok, buyer = auth._attempt_login("wrong-password")
        assert ok is False
        assert buyer is None


def test_attempt_login_expired_returns_buyer_but_not_ok():
    """Expired credentials return ``(False, Buyer)`` so the UI can show a
    helpful 'access expired' message instead of generic 'wrong password'."""
    from app.ui import auth
    with patch.object(auth, "_load_buyers", return_value=_FAKE_BUYERS), \
         patch.object(auth, "_legacy_password", return_value=""):
        ok, buyer = auth._attempt_login("ExpiredPassword678")
        assert ok is False
        assert buyer is not None
        assert buyer.is_expired


def test_attempt_login_empty_password():
    from app.ui import auth
    with patch.object(auth, "_load_buyers", return_value=_FAKE_BUYERS), \
         patch.object(auth, "_legacy_password", return_value=""):
        ok, _ = auth._attempt_login("")
        assert ok is False


def test_attempt_login_legacy_password():
    """Legacy single-password fallback used when no [[buyers]] are
    configured (local dev / grandfathered deploys)."""
    from app.ui import auth
    with patch.object(auth, "_load_buyers", return_value={}), \
         patch.object(auth, "_legacy_password", return_value="legacy-dev-pw"):
        ok, buyer = auth._attempt_login("legacy-dev-pw")
        assert ok is True
        assert buyer is not None


# ── Buyer loader (TOML schema parsing) ──────────────────────────────────


def test_load_buyers_skips_malformed():
    """A malformed entry must not take down the whole gate."""
    from app.ui import auth

    bad_secrets = {
        "buyers": [
            {"password": "good-one-1234567", "name": "Good", "expires": "2026-11-30"},
            {"password": "x"},  # missing name + expires
            {"name": "No password"},
            {"password": "short", "name": "Too short pw", "expires": "2026-11-30"},  # <8 chars
            {"password": "another-good-12345", "name": "Also Good", "expires": "2027-01-01"},
        ]
    }
    with patch.object(auth, "_read_secrets", return_value=bad_secrets):
        out = auth._load_buyers()
    # Only the 2 well-formed entries survive
    assert len(out) == 2
    assert "good-one-1234567" in out
    assert "another-good-12345" in out


def test_load_buyers_empty():
    from app.ui import auth
    with patch.object(auth, "_read_secrets", return_value={}):
        assert auth._load_buyers() == {}


# ── Geographic zone ─────────────────────────────────────────────────────


def test_country_to_zone_iso2():
    from app.crm.normalizers import country_to_zone
    assert country_to_zone(iso2="FR") == "Europe"
    assert country_to_zone(iso2="US") == "Amérique du Nord"
    assert country_to_zone(iso2="CN") == "Asie"
    assert country_to_zone(iso2="BR") == "Amérique du Sud"
    assert country_to_zone(iso2="NG") == "Autre"


def test_country_to_zone_nan_safe():
    """Regression : pandas NaN floats must not crash the helper."""
    import math
    from app.crm.normalizers import country_to_zone
    assert country_to_zone(iso2=math.nan, country_name=math.nan) == "Autre"
    assert country_to_zone(iso2=None, country_name=None) == "Autre"


def test_country_to_zone_caucasus_in_asia():
    """Regression : AM, AZ, GE must map to Asia not Other."""
    from app.crm.normalizers import country_to_zone
    assert country_to_zone(iso2="AM") == "Asie"
    assert country_to_zone(iso2="AZ") == "Asie"
    assert country_to_zone(iso2="GE") == "Asie"


def test_country_to_zone_puerto_rico_in_north_america():
    """Regression : PR must map to North America (US territory)."""
    from app.crm.normalizers import country_to_zone
    assert country_to_zone(iso2="PR") == "Amérique du Nord"


# ── Company type derivation ─────────────────────────────────────────────


def test_derive_company_type_nan_safe():
    """Regression : NaN floats from pandas must not crash."""
    import math
    from app.crm.normalizers import derive_company_type
    out = derive_company_type(
        business_model=math.nan,
        supply_chain_tier=math.nan,
        products_categories=None,
        activity_1liner=math.nan,
    )
    # Returns a valid bucket (default "Société de services")
    from app.crm.normalizers import ALLOWED_COMPANY_TYPES
    assert out in ALLOWED_COMPANY_TYPES


def test_derive_company_type_activity_verb_wins():
    """'Édite' verb beats any other signal → Éditeur logiciel."""
    from app.crm.normalizers import derive_company_type
    out = derive_company_type(
        business_model="OEM",
        supply_chain_tier="OEM",
        products_categories=["Véhicules blindés"],
        activity_1liner="Édite des plateformes IA pour la défense.",
    )
    assert out == "Éditeur logiciel"


def test_derive_company_type_distribute_verb():
    from app.crm.normalizers import derive_company_type
    out = derive_company_type(
        activity_1liner="Distribue des composants électroniques durcis.",
    )
    assert out == "Distributeur"


# ── Pipeline smoke test ─────────────────────────────────────────────────


def test_normalizers_imports():
    """Every public symbol must be importable — guards against syntax /
    import regressions in the most-touched module."""
    from app.crm.normalizers import (  # noqa: F401
        derive_company_type, ALLOWED_COMPANY_TYPES,
        country_to_zone, GEOGRAPHIC_ZONES,
        country_fr, company_type_fr,
        target_type_fr, lead_status, crm_stage,
    )


def test_auth_module_imports():
    from app.ui.auth import (  # noqa: F401
        Buyer, current_buyer, is_authenticated,
        render_login_screen, render_access_banner, sign_out,
        get_favorite_ids, set_favorite, clear_favorites,
    )
