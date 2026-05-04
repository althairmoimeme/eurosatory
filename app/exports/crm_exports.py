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


def _build(df: pd.DataFrame, filters: Optional[dict]) -> pd.DataFrame:
    if filters:
        df = apply_filters(df, **filters)
    return df


def _scoped_df(filters: Optional[dict], list_id: Optional[int],
               base_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Return the dataframe an export should operate on.

    Priority: ``base_df`` (caller provided) > ``list_id`` (load list members
    only) > full ``crm_dataframe()`` with ``filters`` applied.
    """
    if base_df is not None:
        df = base_df
    elif list_id is not None:
        df = crm_dataframe(list_id=list_id)
    else:
        df = crm_dataframe()
    return _build(df, filters)


# ---------------------------------------------------------------------------
# Full export
# ---------------------------------------------------------------------------


def export_full_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                    list_id: Optional[int] = None) -> Path:
    df = _scoped_df(filters, list_id)
    p = path or EXPORT_DIR / f"crm_full_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    df.to_csv(p, index=False)
    return p


def export_full_xlsx(filters: Optional[dict] = None, path: Optional[Path] = None,
                     list_id: Optional[int] = None) -> Path:
    df = _scoped_df(filters, list_id)
    p = path or EXPORT_DIR / f"crm_full_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Accounts")
        for prio in ("A+", "A", "B"):
            sub = df[df["priority_level"] == prio]
            if not sub.empty:
                sub.to_excel(w, index=False, sheet_name=f"Priority {prio}")
    return p


# ---------------------------------------------------------------------------
# Microsoft Dynamics-ready
# ---------------------------------------------------------------------------

DYNAMICS_RENAME: dict[str, str] = {
    "account_name": "Account Name",
    "website_url": "Website",
    "country": "Country/Region",
    "city": "City",
    "linkedin_company_url": "LinkedIn URL",
    "priority_level": "Lead Rating",
    "lead_status": "Status",
    "defense_segment_main": "Industry",
    "defense_subsegment": "Segment",
    "description_short": "Description",
    "next_best_action": "Next Step",
    "notes": "Notes",
    "owner": "Owner",
    "ideal_seller_profile": "Topic",
}
DYNAMICS_LEAD_SOURCE = "Eurosatory 2026 Catalog"


def export_dynamics_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                        list_id: Optional[int] = None) -> Path:
    df = _scoped_df(filters, list_id)
    out = df.rename(columns=DYNAMICS_RENAME).copy()
    out["Lead Source"] = DYNAMICS_LEAD_SOURCE
    cols = [
        "Account Name", "Website", "Country/Region", "City", "LinkedIn URL",
        "Lead Source", "Lead Rating", "Status", "Topic", "Description",
        "Industry", "Segment", "Owner", "Next Step", "Notes",
    ]
    for c in cols:
        if c not in out.columns:
            out[c] = None
    out = out[cols]
    p = path or EXPORT_DIR / f"crm_dynamics_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    out.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Salesforce-friendly export
# ---------------------------------------------------------------------------


def export_salesforce_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                          list_id: Optional[int] = None) -> Path:
    df = _scoped_df(filters, list_id)
    rename = {
        "account_name": "Name",
        "website_url": "Website",
        "country": "BillingCountry",
        "city": "BillingCity",
        "linkedin_company_url": "LinkedIn__c",
        "defense_segment_main": "Industry",
        "company_type": "Type",
        "lead_score": "Lead_Score__c",
        "priority_level": "Lead_Rating__c",
        "lead_status": "Status",
        "ideal_seller_profile": "Description",
        "next_best_action": "Next_Step__c",
        "buying_need_main": "Buying_Need_Main__c",
    }
    cols = list(rename.values()) + ["Lead_Source__c"]
    out = df.rename(columns=rename).copy()
    out["Lead_Source__c"] = DYNAMICS_LEAD_SOURCE
    for c in cols:
        if c not in out.columns:
            out[c] = None
    out = out[cols]
    p = path or EXPORT_DIR / f"crm_salesforce_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    out.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# HubSpot-friendly
# ---------------------------------------------------------------------------


def export_hubspot_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                       list_id: Optional[int] = None) -> Path:
    df = _scoped_df(filters, list_id)
    rename = {
        "account_name": "Company name",
        "website_url": "Website URL",
        "country": "Country",
        "city": "City",
        "linkedin_company_url": "LinkedIn",
        "defense_segment_main": "Industry",
        "company_type": "Type",
        "lead_score": "Lead score",
        "priority_level": "Lead rating",
        "lead_status": "Lifecycle stage",
        "buying_need_main": "Buying need",
        "ideal_seller_profile": "Notes",
        "short_pitch": "Pitch",
        "next_best_action": "Next step",
        "owner": "Owner",
    }
    cols = list(rename.values())
    out = df.rename(columns=rename).copy()
    for c in cols:
        if c not in out.columns:
            out[c] = None
    out = out[cols]
    p = path or EXPORT_DIR / f"crm_hubspot_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    out.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Sales prospecting (lean)
# ---------------------------------------------------------------------------


def export_prospecting_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                           list_id: Optional[int] = None) -> Path:
    df = _scoped_df(filters, list_id)
    cols = [
        "account_name", "country", "priority_level", "lead_score",
        "target_type", "buying_need_main", "ideal_seller_profile",
        "recommended_sales_angle", "short_pitch", "website_url",
        "linkedin_company_url", "lead_status", "next_best_action", "notes",
    ]
    sub = df[cols].copy()
    p = path or EXPORT_DIR / f"crm_prospecting_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    sub.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Airtable-friendly (clean column names, simple separators)
# ---------------------------------------------------------------------------


def export_airtable_csv(filters: Optional[dict] = None, path: Optional[Path] = None,
                        list_id: Optional[int] = None) -> Path:
    df = _scoped_df(filters, list_id)
    rename = {
        "account_name": "Company",
        "country": "Country",
        "city": "City",
        "website_url": "Website",
        "linkedin_company_url": "LinkedIn",
        "eurosatory_profile_url": "Eurosatory profile",
        "booth_number": "Booth",
        "company_type": "Type",
        "defense_segment_main": "Defense segment",
        "defense_segments_secondary": "Secondary segments",
        "products_built": "Builds",
        "products_sold": "Sells",
        "services_sold": "Services",
        "technologies": "Technologies",
        "target_clients": "Target clients",
        "markets_served": "Markets",
        "target_type": "Target type",
        "buying_need_main": "Buying need",
        "buying_needs_secondary": "Other buying needs",
        "lead_score": "Score",
        "priority_level": "Priority",
        "ideal_seller_profile": "Ideal seller",
        "short_pitch": "Pitch",
        "recommended_sales_angle": "Sales angle",
        "next_best_action": "Next action",
        "lead_status": "Status",
        "tags": "Tags",
        "custom_list": "Lists",
        "data_confidence": "Confidence",
    }
    out = df.rename(columns=rename)[list(rename.values())]
    p = path or EXPORT_DIR / f"crm_airtable_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
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
