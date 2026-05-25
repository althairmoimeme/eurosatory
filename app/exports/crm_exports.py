"""CRM-specific exports : full, Microsoft Dynamics, Salesforce, HubSpot,
sales-prospecting, Airtable, custom-list."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from app.config import EXPORT_DIR
from app.crm.repository import apply_filters, crm_dataframe
from app.crm.schema import CRM_COLUMNS

# ``EXPORT_VISIBLE_COLUMNS`` was added to ``app.crm.schema`` in the same
# release as this file. Streamlit Cloud occasionally serves a stale
# bytecode cache mid-deploy where schema.py is the old version while
# this module is the new one — that combination would raise an
# ``ImportError`` on module load and break the whole app. Fall back to
# an inline definition so the module always loads, regardless of which
# build state the Cloud is in.
try:
    from app.crm.schema import EXPORT_VISIBLE_COLUMNS
except ImportError:  # pragma: no cover — defensive only
    EXPORT_VISIBLE_COLUMNS = [
        "account_name", "website_url", "country", "booth_number",
        "company_type",
        "activity_1liner", "products_specific", "products_categories",
        "services_specific", "services_categories", "target_buyers",
        "technologies_specific", "technologies_categories", "why_target",
        "targeting_score", "targeting_source",
        "priority_level", "lead_score",
    ]


def visible_columns_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the columns that appear in the on-screen Companies table.

    Drops internal/diagnostic columns the buyer never sees (``company_size``,
    ``founding_year``, ``eurosatory_profile_url``, ``core_business``,
    ``description_short``, ``source_urls``, etc.) so raw exports stay
    aligned with what the user reads in the UI.

    Columns are returned in ``EXPORT_VISIBLE_COLUMNS`` order ; any column
    listed there but missing from the input dataframe is silently
    skipped (keeps the helper robust to schema drift).
    """
    kept = [c for c in EXPORT_VISIBLE_COLUMNS if c in df.columns]
    return df[kept].copy()


def _build(df: pd.DataFrame, filters: Optional[dict]) -> pd.DataFrame:
    if filters:
        df = apply_filters(df, **filters)
    return df


def _load_targeting_profiles_for_export() -> dict[int, dict]:
    """Load the Eurosatory 2026 targeting profile JSON and index it by
    exhibitor id. Includes BOTH the FR and EN variants of each text field
    so the caller can pick the right language at merge time.

    Returns an empty dict if the JSON is missing — exports degrade
    gracefully without the targeting profile columns.
    """
    import json as _json
    profiles_path = EXPORT_DIR / "targeting_profiles_final.json"
    if not profiles_path.exists():
        return {}
    try:
        raw = _json.loads(profiles_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    by_eid: dict[int, dict] = {}
    for r in raw:
        try:
            eid = int(r.get("exhibitor_id"))
        except (TypeError, ValueError):
            continue
        by_eid[eid] = r
    return by_eid


def _merge_targeting_profile(df: pd.DataFrame, lang: str) -> pd.DataFrame:
    """Merge the Eurosatory 2026 targeting profile columns
    (``activity_1liner``, ``products_specific``, …, ``targeting_score``,
    ``targeting_source``) into the CRM dataframe. Picks the FR or EN
    variant of each text field according to ``lang``.

    Mirrors the same merge that ``app.ui.streamlit_app.load_crm`` does
    for the on-screen table — keeps the exports column-aligned with the
    UI.
    """
    by_eid = _load_targeting_profiles_for_export()
    if not by_eid or "account_id" not in df.columns:
        return df

    def _eid(account_id: str) -> Optional[int]:
        if not isinstance(account_id, str):
            return None
        try:
            return int(account_id.replace("ESY26-", ""))
        except ValueError:
            return None

    def _pick(r: dict, fr_key: str, en_key: str):
        if lang == "en":
            v = r.get(en_key)
            if v:
                return v
        return r.get(fr_key)

    rows_a1, rows_pspec, rows_pcat = [], [], []
    rows_sspec, rows_scat = [], []
    rows_tb, rows_tspec, rows_tcat = [], [], []
    rows_wt, rows_tscore, rows_tsrc = [], [], []
    for account_id in df["account_id"].tolist():
        eid = _eid(account_id)
        r = by_eid.get(eid, {}) if eid is not None else {}
        rows_a1.append(_pick(r, "activity_1liner", "activity_1liner_en") or "")
        rows_pspec.append(" · ".join(_pick(r, "products", "products_en") or []))
        rows_pcat.append(" · ".join(r.get("products_categories") or []))
        rows_sspec.append(" · ".join(_pick(r, "services", "services_en") or []))
        rows_scat.append(" · ".join(r.get("services_categories") or []))
        rows_tb.append(", ".join(_pick(r, "target_buyers", "target_buyers_en") or []))
        rows_tspec.append(" · ".join(_pick(r, "technologies", "technologies_en") or []))
        rows_tcat.append(" · ".join(r.get("technologies_categories") or []))
        rows_wt.append(_pick(r, "why_target", "why_target_en") or "")
        rows_tscore.append(int(r.get("completeness_score") or 0))
        rows_tsrc.append(r.get("data_source_strength") or "")

    out = df.copy()
    out["activity_1liner"] = rows_a1
    out["products_specific"] = rows_pspec
    out["products_categories"] = rows_pcat
    out["services_specific"] = rows_sspec
    out["services_categories"] = rows_scat
    out["target_buyers"] = rows_tb
    out["technologies_specific"] = rows_tspec
    out["technologies_categories"] = rows_tcat
    out["why_target"] = rows_wt
    out["targeting_score"] = rows_tscore
    out["targeting_source"] = rows_tsrc
    return out


def _scoped_df(filters: Optional[dict], list_id: Optional[int],
               base_df: Optional[pd.DataFrame] = None,
               lang: str = "fr") -> pd.DataFrame:
    """Return the dataframe an export should operate on.

    Priority: ``base_df`` (caller provided — assumed already localized and
    profile-merged) > ``list_id`` (load list members only) > full
    ``crm_dataframe()`` with ``filters`` applied.

    ``lang`` ("fr"/"en") controls language-specific localization of the
    text columns. When "en", we swap in the EN values from the static
    targeting profile JSON so the exported file matches what the user
    sees in EN mode. For the non-``base_df`` paths, we ALSO merge in the
    Eurosatory 2026 targeting profile columns so the exports carry the
    same value-add the user reads in the on-screen table.
    """
    if base_df is not None:
        df = base_df
    else:
        if list_id is not None:
            df = crm_dataframe(list_id=list_id)
        else:
            df = crm_dataframe()
        df = _merge_targeting_profile(df, lang)
    return _build(df, filters)


# ---------------------------------------------------------------------------
# Full export
# ---------------------------------------------------------------------------


def export_full_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                    list_id: Optional[int] = None, lang: str = "fr") -> Path:
    df = _scoped_df(filters, list_id, lang=lang)
    df = visible_columns_only(df)
    suffix = "_en" if lang == "en" else ""
    p = path or EXPORT_DIR / f"crm_full{suffix}_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    df.to_csv(p, index=False)
    return p


def export_full_xlsx(filters: Optional[dict] = None, path: Optional[Path] = None,
                     list_id: Optional[int] = None, lang: str = "fr") -> Path:
    df = _scoped_df(filters, list_id, lang=lang)
    df = visible_columns_only(df)
    suffix = "_en" if lang == "en" else ""
    p = path or EXPORT_DIR / f"crm_full{suffix}_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Accounts")
        if "priority_level" in df.columns:
            for prio in ("A+", "A", "B"):
                sub = df[df["priority_level"] == prio]
                if not sub.empty:
                    sub.to_excel(w, index=False, sheet_name=f"Priority {prio}")
    return p


# ---------------------------------------------------------------------------
# Microsoft Dynamics-ready
# ---------------------------------------------------------------------------

DYNAMICS_RENAME: dict[str, str] = {
    # Trimmed to fields actually surfaced in the on-screen Companies
    # table + Eurosatory 2026 targeting profile. Internal-only fields
    # (city, linkedin_company_url, description_short, defense_segment_main,
    # …) were dropped — buyers should not see them as columns in their
    # Dynamics import.
    "account_name": "Account Name",
    "website_url": "Website",
    "country": "Country/Region",
    "booth_number": "Eurosatory Booth",
    "company_type": "Type",
    "activity_1liner": "Description",
    "products_specific": "Products",
    "services_specific": "Services",
    "target_buyers": "Target Customer",
    "technologies_specific": "Technologies",
    "why_target": "Sales Angle",
    "priority_level": "Lead Rating",
    "lead_score": "Lead Score",
}
DYNAMICS_LEAD_SOURCE = "Eurosatory 2026 Catalog"


def export_dynamics_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                        list_id: Optional[int] = None, lang: str = "fr") -> Path:
    df = _scoped_df(filters, list_id, lang=lang)
    cols_in = [c for c in DYNAMICS_RENAME if c in df.columns]
    out = df[cols_in].rename(columns=DYNAMICS_RENAME).copy()
    out["Lead Source"] = DYNAMICS_LEAD_SOURCE
    final_cols = [DYNAMICS_RENAME[c] for c in cols_in] + ["Lead Source"]
    out = out[final_cols]
    suffix = "_en" if lang == "en" else ""
    p = path or EXPORT_DIR / f"crm_dynamics{suffix}_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    out.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Salesforce-friendly export
# ---------------------------------------------------------------------------


def export_salesforce_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                          list_id: Optional[int] = None, lang: str = "fr") -> Path:
    df = _scoped_df(filters, list_id, lang=lang)
    # Salesforce column map — trimmed to fields visible in the on-screen
    # Companies table + targeting profile. Drops the internal-only
    # description_short / next_best_action / lead_status / buying_need_main
    # / linkedin / city — they were never shown to the buyer.
    rename = {
        "account_name": "Name",
        "website_url": "Website",
        "country": "BillingCountry",
        "booth_number": "Eurosatory_Booth__c",
        "company_type": "Type",
        "activity_1liner": "Description",
        "products_specific": "Products__c",
        "services_specific": "Services__c",
        "target_buyers": "Target_Customer__c",
        "technologies_specific": "Technologies__c",
        "why_target": "Sales_Angle__c",
        "lead_score": "Lead_Score__c",
        "priority_level": "Lead_Rating__c",
    }
    cols_in = [c for c in rename if c in df.columns]
    out = df[cols_in].rename(columns=rename).copy()
    out["Lead_Source__c"] = DYNAMICS_LEAD_SOURCE
    final_cols = [rename[c] for c in cols_in] + ["Lead_Source__c"]
    out = out[final_cols]
    suffix = "_en" if lang == "en" else ""
    p = path or EXPORT_DIR / f"crm_salesforce{suffix}_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    out.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# HubSpot-friendly
# ---------------------------------------------------------------------------


def export_hubspot_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                       list_id: Optional[int] = None, lang: str = "fr") -> Path:
    df = _scoped_df(filters, list_id, lang=lang)
    # HubSpot column map — trimmed to the columns visible in the
    # on-screen Companies table + targeting profile. Internal-only
    # fields (city, linkedin, lifecycle stage, owner, buying need …)
    # removed.
    rename = {
        "account_name": "Company name",
        "website_url": "Website URL",
        "country": "Country",
        "booth_number": "Eurosatory booth",
        "company_type": "Type",
        "activity_1liner": "Description",
        "products_specific": "Products",
        "services_specific": "Services",
        "target_buyers": "Target customer",
        "technologies_specific": "Technologies",
        "why_target": "Sales angle",
        "lead_score": "Lead score",
        "priority_level": "Lead rating",
    }
    cols_in = [c for c in rename if c in df.columns]
    out = df[cols_in].rename(columns=rename).copy()
    suffix = "_en" if lang == "en" else ""
    p = path or EXPORT_DIR / f"crm_hubspot{suffix}_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    out.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Sales prospecting (lean)
# ---------------------------------------------------------------------------


def export_prospecting_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                           list_id: Optional[int] = None, lang: str = "fr") -> Path:
    df = _scoped_df(filters, list_id, lang=lang)
    # Lean prospecting CSV — same columns the user sees in the on-screen
    # Companies table. Dropped the internal-only fields (target_type,
    # buying_need_main, ideal_seller_profile, recommended_sales_angle,
    # short_pitch, linkedin_company_url, lead_status, next_best_action,
    # notes) — they were never surfaced to the buyer.
    cols = [
        "account_name", "country", "website_url", "booth_number",
        "company_type", "activity_1liner", "products_specific",
        "services_specific", "target_buyers", "technologies_specific",
        "why_target", "priority_level", "lead_score",
    ]
    keep = [c for c in cols if c in df.columns]
    sub = df[keep].copy()
    suffix = "_en" if lang == "en" else ""
    p = path or EXPORT_DIR / f"crm_prospecting{suffix}_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    sub.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Airtable-friendly (clean column names, simple separators)
# ---------------------------------------------------------------------------


def export_airtable_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                        list_id: Optional[int] = None, lang: str = "fr") -> Path:
    df = _scoped_df(filters, list_id, lang=lang)
    # Airtable column map. Only fields that ARE surfaced in the on-screen
    # Companies table or its detail card are kept — internal-only fields
    # (eurosatory_profile_url, defense_segment_main, products_built,
    # markets_served, …) are dropped so the Airtable base mirrors what
    # the buyer sees in the UI.
    rename = {
        "account_name": "Company",
        "country": "Country",
        "website_url": "Website",
        "booth_number": "Booth",
        "company_type": "Type",
        "activity_1liner": "Activity",
        "products_specific": "Products",
        "products_categories": "Product categories",
        "services_specific": "Services",
        "services_categories": "Service categories",
        "target_buyers": "Target customers",
        "technologies_specific": "Technologies",
        "technologies_categories": "Technology categories",
        "why_target": "Why target",
        "targeting_score": "Targeting score",
        "targeting_source": "Source",
        "priority_level": "Priority",
        "lead_score": "Score",
    }
    cols = [c for c in rename if c in df.columns]
    out = df[cols].rename(columns=rename)
    suffix = "_en" if lang == "en" else ""
    p = path or EXPORT_DIR / f"crm_airtable{suffix}_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    out.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Custom list export
# ---------------------------------------------------------------------------


def export_custom_list_csv(
    list_id: int, fmt: str = "csv", path: Optional[Path] = None
) -> Path:
    df = crm_dataframe(list_id=list_id)
    suffix = "csv" if fmt == "csv" else "xlsx"
    p = path or EXPORT_DIR / f"crm_list_{list_id}_{datetime.utcnow():%Y%m%d_%H%M%S}.{suffix}"
    if fmt == "xlsx":
        df.to_excel(p, index=False, sheet_name="List")
    else:
        df.to_csv(p, index=False)
    return p
