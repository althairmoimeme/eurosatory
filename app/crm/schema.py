"""Canonical list of CRM columns + their order.

This is the single source of truth for every export and for the UI table.
"""
from __future__ import annotations

# Identification
IDENTIFICATION = [
    "account_id",
    "account_name",
    "country",
    "country_iso2",
    "city",
    "website_url",
    "linkedin_company_url",
    "eurosatory_profile_url",
    "booth_number",
    "company_size",
    "founding_year",
    "company_type",
    "business_model",
    "headline",
    "description_short",
    "source_urls",
    "last_checked_at",
]

# Defense qualification
QUALIFICATION = [
    "defense_segment_main",
    "defense_segments_secondary",
    "defense_subsegment",
    "core_business",
    "main_products_services",
    "certifications",
    "industry_associations",
    "parent_group",
    "additional_offices",
    "products_built",
    "products_sold",
    "services_sold",
    "technologies",
    "target_clients",
    "markets_served",
    "maturity_defense_level",
]

# Commercial targeting
TARGETING = [
    "target_type",
    "all_target_types",
    "commercial_interest_level",
    "lead_score",
    "priority_level",
    "ideal_seller_profile",
    "buying_need_main",
    "buying_needs_secondary",
    "buying_need_confidence",
    "supplier_opportunity",
    "partnership_opportunity",
    "integration_opportunity",
    "distribution_opportunity",
    "recommended_sales_angle",
    "short_pitch",
    "company_pitch",
    "commercial_relevance_summary",
    "objections_probables",
    "prospecting_keywords",
]

# CRM pipeline
PIPELINE = [
    "lead_status",
    "crm_stage",
    "next_best_action",
    "next_action_date",
    "owner",
    "sales_team",
    "is_favorite",
    "custom_list",
    "tags",
    "notes",
    "last_contact_date",
    "follow_up_status",
]

# Data quality
QUALITY = [
    "data_confidence",
    "fields_to_verify",
    "missing_critical_fields",
    "extraction_method",
    "source_confidence",
    "manual_review_required",
]

# Eurosatory 2026 targeting profile (output of the 6-field pipeline) ;
# loaded from ``data/exports/targeting_profiles_final.json`` and merged
# into the dataframe in ``app/ui/streamlit_app.py::load_crm``.
# Intentionally NOT part of CRM_COLUMNS — these come from the JSON merge,
# not from the SQL dataframe (so no NaN-vs-merged conflict).
TARGETING_PROFILE_2026 = [
    "activity_1liner",
    "supply_chain_tier",        # NEW — OEM / Tier 1 / Tier 2 / Tier 3 / Tier 4 / N/A
    "products_specific",
    "products_categories",
    "services_specific",
    "services_categories",
    "target_buyers",
    "technologies_specific",
    "technologies_categories",
    "why_target",
    "targeting_score",
    "targeting_source",
]

CRM_COLUMNS: list[str] = (
    IDENTIFICATION + QUALIFICATION + TARGETING + PIPELINE + QUALITY
)

# Subsets used by exports / UI

DEFAULT_TABLE_COLUMNS = [
    "account_name",
    "website_url",
    "country",
    "booth_number",             # Hall + N° de stand ("Hall 4 / G325")
    # Smart-merged dimension : OEM / Intégrateur / Équipementier-Tier 1 /
    # Sous-traitant industriel / Distributeur / Éditeur logiciel / Société
    # de services / Bureau d'ingénierie. Replaces the old
    # ``supply_chain_tier`` column on the buyer-facing table.
    "company_type",
    "activity_1liner",          # Eurosatory targeting profile (FR)
    # Removed : "products_categories" (trop imprécis, source d'erreurs côté client)
    # Removed : "targeting_score"
    "target_buyers",            # 5-label closed taxonomy
    "why_target",               # actionable angle for the rep
]


# Columns kept in any "raw dump" export (full CSV/XLSX, filtered download,
# topbar quick-export). Mirrors what the buyer actually sees in the
# Companies table + the Eurosatory 2026 targeting profile fields, plus
# the priority/lead score that drive sorting. Internal/diagnostic fields
# (``company_size``, ``founding_year``, ``eurosatory_profile_url``,
# ``core_business``, ``description_short``, ``source_urls``, …) are
# intentionally excluded — they were never surfaced in the consultable
# table, so they should not appear in the buyer-facing exports either.
EXPORT_VISIBLE_COLUMNS = [
    # Identification — same five fields as the visible table
    "account_name",
    "website_url",
    "country",
    "booth_number",
    "company_type",
    # Eurosatory 2026 targeting profile (the value-add we sell)
    "activity_1liner",
    "products_specific",
    "products_categories",
    "services_specific",
    "services_categories",
    "target_buyers",
    "technologies_specific",
    "technologies_categories",
    "why_target",
    "targeting_score",
    "targeting_source",
    # Scoring / priority shown in the table header
    "priority_level",
    "lead_score",
]
