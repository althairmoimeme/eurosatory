"""CSV / XLSX exports targeted at the sales team."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import EXPORT_DIR
from app.database import (
    Exhibitor,
    ExhibitorClassification,
    ExhibitorIntelligence,
    ExhibitorTag,
    session_scope,
)


SALES_COLUMNS = [
    "id",
    "company_name",
    "country_iso2",
    "country_name",
    "city",
    "website_url",
    "contact_email",
    "generic_sales_email",
    "phone",
    "linkedin_url",
    "priority_level",
    "commercial_relevance_score",
    "taxonomy_labels",
    "tags",
    "status",
    "needs_review",
    "short_presentation",
    "stand",
    "last_scraped_at",
    "last_enriched_at",
    "email_confidence",
    "email_source",
    "linkedin_confidence",
]


def _row_to_dict(s: Session, exh: Exhibitor) -> dict:
    labels = sorted(
        {
            row.label
            for row in s.execute(
                select(ExhibitorClassification).where(ExhibitorClassification.exhibitor_id == exh.id)
            ).scalars()
        }
    )
    tags = sorted(
        {
            row.tag
            for row in s.execute(
                select(ExhibitorTag).where(ExhibitorTag.exhibitor_id == exh.id)
            ).scalars()
        }
    )
    stands = exh.stands or []
    stand = "; ".join(
        f"{s.get('Hall','?')} / {s.get('Name','?')}" for s in stands if s
    ) or None
    fc = exh.field_confidence or {}
    fs = exh.field_sources or {}
    return {
        "id": exh.id,
        "company_name": exh.company_name,
        "country_iso2": exh.country_iso2,
        "country_name": exh.country_name,
        "city": exh.city,
        "website_url": exh.website_url,
        "contact_email": exh.contact_email,
        "generic_sales_email": exh.generic_sales_email,
        "phone": exh.phone,
        "linkedin_url": exh.linkedin_url,
        "priority_level": exh.priority_level,
        "commercial_relevance_score": exh.commercial_relevance_score,
        "taxonomy_labels": "; ".join(labels),
        "tags": "; ".join(tags),
        "status": exh.status,
        "needs_review": exh.needs_review,
        "short_presentation": exh.short_presentation,
        "stand": stand,
        "last_scraped_at": exh.last_scraped_at,
        "last_enriched_at": exh.last_enriched_at,
        "email_confidence": fc.get("contact_email"),
        "email_source": fs.get("contact_email"),
        "linkedin_confidence": fc.get("linkedin_url"),
    }


def export_dataframe(filters: Optional[dict] = None) -> pd.DataFrame:
    with session_scope() as s:
        q = select(Exhibitor)
        if filters:
            if cs := filters.get("country_iso2"):
                q = q.where(Exhibitor.country_iso2.in_(cs))
            if pls := filters.get("priority_level"):
                q = q.where(Exhibitor.priority_level.in_(pls))
            if statuses := filters.get("status"):
                q = q.where(Exhibitor.status.in_(statuses))
            if (mn := filters.get("min_score")) is not None:
                q = q.where(Exhibitor.commercial_relevance_score >= mn)
        rows = [_row_to_dict(s, e) for e in s.execute(q).scalars()]
    df = pd.DataFrame(rows, columns=SALES_COLUMNS)
    return df


def export_csv(filters: Optional[dict] = None, path: Optional[Path] = None) -> Path:
    df = export_dataframe(filters)
    p = path or EXPORT_DIR / f"eurosatory_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    df.to_csv(p, index=False)
    return p


def export_xlsx(filters: Optional[dict] = None, path: Optional[Path] = None) -> Path:
    df = export_dataframe(filters)
    p = path or EXPORT_DIR / f"eurosatory_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Exhibitors")
        # priority sheets
        for prio in ("A", "B"):
            sub = df[df["priority_level"] == prio]
            if not sub.empty:
                sub.to_excel(w, index=False, sheet_name=f"Priority {prio}")
    return p


# ---------------------------------------------------------------------------
# Premium exports — defense intelligence layer
# ---------------------------------------------------------------------------


INTEL_COLUMNS = [
    "id",
    "company_name",
    "country_iso2",
    "country_name",
    "city",
    "website_url",
    "eurosatory_profile_url",
    "linkedin_url",
    "contact_email",
    "phone",
    "defense_priority_level",
    "defense_commercial_score",
    "defense_maturity_score",
    "commercial_interest_level",
    "commercial_target_type",
    "defense_categories",
    "built_products",
    "built_product_summary",
    "sold_offerings",
    "services",
    "technologies",
    "target_clients",
    "markets_served",
    "business_model",
    "company_type",
    "company_size_estimate",
    "probable_buying_needs",
    "buying_need_confidence",
    "supplier_needs",
    "partnership_opportunities",
    "interest_reason",
    "recommended_sales_angle",
    "recommended_pitch",
    "probable_objections",
    "prospecting_keywords",
    "summary_for_sales",
    "fields_to_verify",
    "extraction_method",
    "tags",
    "status",
    "needs_review",
    "last_crawled_at",
    "last_analyzed_at",
]


def _intel_row(s, exh: Exhibitor, intel: Optional[ExhibitorIntelligence]) -> dict:
    tags = sorted(
        {
            t.tag
            for t in s.execute(
                select(ExhibitorTag).where(ExhibitorTag.exhibitor_id == exh.id)
            ).scalars()
        }
    )
    if intel is None:
        return {
            "id": exh.id,
            "company_name": exh.company_name,
            "country_iso2": exh.country_iso2,
            "country_name": exh.country_name,
            "city": exh.city,
            "website_url": exh.website_url,
            "linkedin_url": exh.linkedin_url,
            "contact_email": exh.contact_email,
            "phone": exh.phone,
            "tags": "; ".join(tags),
            "status": exh.status,
            "needs_review": exh.needs_review,
        }

    def joined(values, sep="; ", limit=None):
        if not values:
            return ""
        if limit:
            values = values[:limit]
        return sep.join(map(str, values))

    return {
        "id": exh.id,
        "company_name": exh.company_name,
        "country_iso2": exh.country_iso2,
        "country_name": exh.country_name,
        "city": exh.city,
        "website_url": exh.website_url,
        "eurosatory_profile_url": intel.eurosatory_profile_url,
        "linkedin_url": exh.linkedin_url,
        "contact_email": exh.contact_email,
        "phone": exh.phone,
        "defense_priority_level": intel.defense_priority_level,
        "defense_commercial_score": intel.defense_commercial_score,
        "defense_maturity_score": intel.defense_maturity_score,
        "commercial_interest_level": intel.commercial_interest_level,
        "commercial_target_type": joined(intel.commercial_target_type),
        "defense_categories": joined(intel.defense_categories),
        "built_products": joined(intel.built_products),
        "built_product_summary": intel.built_product_summary,
        "sold_offerings": joined(intel.sold_offerings),
        "services": joined(intel.services),
        "technologies": joined(intel.technologies),
        "target_clients": joined(intel.target_clients),
        "markets_served": joined(intel.markets_served),
        "business_model": intel.business_model,
        "company_type": intel.company_type,
        "company_size_estimate": intel.company_size_estimate,
        "probable_buying_needs": joined(intel.probable_buying_needs),
        "buying_need_confidence": intel.buying_need_confidence,
        "supplier_needs": joined(intel.supplier_needs),
        "partnership_opportunities": joined(intel.partnership_opportunities),
        "interest_reason": intel.interest_reason,
        "recommended_sales_angle": intel.recommended_sales_angle,
        "recommended_pitch": intel.recommended_pitch,
        "probable_objections": joined(intel.probable_objections),
        "prospecting_keywords": joined(intel.prospecting_keywords, sep=", "),
        "summary_for_sales": intel.summary_for_sales,
        "fields_to_verify": joined(intel.fields_to_verify),
        "extraction_method": intel.extraction_method,
        "tags": "; ".join(tags),
        "status": exh.status,
        "needs_review": exh.needs_review,
        "last_crawled_at": intel.last_crawled_at,
        "last_analyzed_at": intel.last_analyzed_at,
    }


def export_intelligence_dataframe(filters: Optional[dict] = None) -> pd.DataFrame:
    with session_scope() as s:
        q = select(Exhibitor, ExhibitorIntelligence).join(
            ExhibitorIntelligence,
            ExhibitorIntelligence.exhibitor_id == Exhibitor.id,
            isouter=True,
        )
        if filters:
            if cs := filters.get("country_iso2"):
                q = q.where(Exhibitor.country_iso2.in_(cs))
            if pls := filters.get("defense_priority_level"):
                q = q.where(ExhibitorIntelligence.defense_priority_level.in_(pls))
            if (mn := filters.get("min_defense_score")) is not None:
                q = q.where(ExhibitorIntelligence.defense_commercial_score >= mn)
            if cats := filters.get("defense_categories"):
                # SQLite JSON1 contains check; portable for our scale
                from sqlalchemy import or_
                ors = [ExhibitorIntelligence.defense_categories.like(f'%"{c}"%') for c in cats]
                q = q.where(or_(*ors))
            if interest := filters.get("commercial_interest_level"):
                q = q.where(ExhibitorIntelligence.commercial_interest_level.in_(interest))
            if filters.get("only_with_intelligence"):
                q = q.where(ExhibitorIntelligence.id.is_not(None))
        rows = [_intel_row(s, exh, intel) for exh, intel in s.execute(q).all()]
    df = pd.DataFrame(rows, columns=INTEL_COLUMNS)
    return df


def export_intelligence_xlsx(filters: Optional[dict] = None, path: Optional[Path] = None) -> Path:
    df = export_intelligence_dataframe(filters)
    p = path or EXPORT_DIR / f"eurosatory_intelligence_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Intelligence")
        for prio in ("A+", "A", "B"):
            sub = df[df["defense_priority_level"] == prio]
            if not sub.empty:
                sub.to_excel(w, index=False, sheet_name=f"Priority {prio}")
    return p


def export_intelligence_csv(filters: Optional[dict] = None, path: Optional[Path] = None) -> Path:
    df = export_intelligence_dataframe(filters)
    p = path or EXPORT_DIR / f"eurosatory_intelligence_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    df.to_csv(p, index=False)
    return p


def export_airtable_csv(filters: Optional[dict] = None, path: Optional[Path] = None) -> Path:
    """CSV with Airtable-friendly column names (capitalised, multi-select compatible)."""
    df = export_intelligence_dataframe(filters)
    rename = {
        "company_name": "Company",
        "country_iso2": "Country",
        "city": "City",
        "website_url": "Website",
        "eurosatory_profile_url": "Eurosatory Profile",
        "linkedin_url": "LinkedIn",
        "contact_email": "Email",
        "phone": "Phone",
        "defense_priority_level": "Priority",
        "defense_commercial_score": "Score",
        "commercial_interest_level": "Interest",
        "commercial_target_type": "Target Type",
        "defense_categories": "Defense Categories",
        "built_products": "Builds",
        "sold_offerings": "Sells",
        "technologies": "Technologies",
        "probable_buying_needs": "Probable Buying Needs",
        "business_model": "Business Model",
        "recommended_pitch": "Pitch",
        "summary_for_sales": "Sales Summary",
        "tags": "Tags",
        "status": "Status",
    }
    df = df.rename(columns=rename)[list(rename.values())]
    p = path or EXPORT_DIR / f"eurosatory_airtable_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    df.to_csv(p, index=False)
    return p


def export_crm_csv(filters: Optional[dict] = None, path: Optional[Path] = None) -> Path:
    """Minimal CRM-import CSV (HubSpot / Pipedrive / Salesforce friendly)."""
    df = export_intelligence_dataframe(filters)
    df = df[[
        "company_name", "country_iso2", "city", "website_url", "linkedin_url",
        "contact_email", "phone", "defense_priority_level",
        "defense_commercial_score", "commercial_target_type", "built_products",
        "summary_for_sales", "recommended_pitch",
    ]].rename(columns={
        "company_name": "Company name",
        "country_iso2": "Country",
        "city": "City",
        "website_url": "Website URL",
        "linkedin_url": "LinkedIn",
        "contact_email": "Email",
        "phone": "Phone",
        "defense_priority_level": "Lead status",
        "defense_commercial_score": "Lead score",
        "commercial_target_type": "Lifecycle stage",
        "built_products": "Industry",
        "summary_for_sales": "Notes",
        "recommended_pitch": "Pitch",
    })
    p = path or EXPORT_DIR / f"eurosatory_crm_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    df.to_csv(p, index=False)
    return p
