"""Lightweight i18n for the LeadForges Streamlit app.

Detection
─────────
  Language is read PER REQUEST from ``st.query_params["lang"]`` — no env
  vars, no module-level state. Streamlit Cloud runs all users in the
  same process, so any session-scoped switching MUST go through query
  params or st.session_state (we use query params so the language
  survives full page reloads / shared URLs).

Usage
─────
    from app.ui.i18n import t, get_lang
    st.title(t("page.companies.title"))
    if get_lang() == "en":
        ...

Adding a new translation
────────────────────────
  Add the French key as the canonical version, then provide the EN
  translation. Untranslated keys fall back to the French version.

Convention for data fields
──────────────────────────
  When reading from a row that has both ``foo`` and ``foo_en`` columns
  (e.g. ``activity_1liner`` + ``activity_1liner_en``), call
  ``localized(row, "activity_1liner")`` which picks ``foo_en`` if the
  current language is en AND the EN value is non-empty, else falls back
  to ``foo``.
"""

from __future__ import annotations

from typing import Any

import streamlit as st


# ─── LANGUAGE DETECTION ───────────────────────────────────────────────

DEFAULT_LANG = "fr"
SUPPORTED_LANGS = ("fr", "en")


def get_lang() -> str:
    """Return the current request's language code (``"fr"`` or ``"en"``).

    Reads ``st.query_params["lang"]`` on every call. Defaults to French
    if no/invalid value.
    """
    try:
        qp = dict(st.query_params)
    except Exception:  # noqa: BLE001
        try:
            qp = {k: v[0] if isinstance(v, list) else v
                  for k, v in st.experimental_get_query_params().items()}
        except Exception:  # noqa: BLE001
            qp = {}
    val = str(qp.get("lang", "")).strip().lower()
    if val in SUPPORTED_LANGS:
        return val
    return DEFAULT_LANG


def is_en() -> bool:
    return get_lang() == "en"


def with_lang_param(href: str) -> str:
    """Append ``?lang=<current>`` (or ``&lang=…``) to a URL when EN is
    active. Used to preserve language when generating internal links.
    """
    if not is_en():
        return href
    sep = "&" if "?" in href else "?"
    return f"{href}{sep}lang=en"


# ─── TRANSLATION DICTIONARY ──────────────────────────────────────────
# Convention : keys are dotted paths describing the UI element.
# When in doubt, prefer slightly verbose keys to avoid collisions.
#
# Only the EN dict is required — French is the canonical key.

_EN: dict[str, str] = {
    # — Login screen —
    "login.title": "Sign in to LeadForges",
    "login.subtitle": "Enter the password you received by email after purchase.",
    "login.password_label": "Password",
    "login.submit": "Sign in",
    "login.error_invalid": "Incorrect password.",
    "login.error_expired": "Your access has expired. Contact us to renew.",
    "login.error_locked": "Too many failed attempts. Try again in {seconds}s.",
    "login.attempts_remaining": "{n} attempts remaining",

    # — Access banner —
    "banner.welcome": "Welcome, {name}",
    "banner.expires_in": "Access expires in {days} days",
    "banner.signout": "Sign out",

    # — Demo banner —
    "demo.label": "DEMO VERSION",
    "demo.subtitle": "50 exhibitors × 50 signals · representative sample",
    "demo.cta": "Access the full database (€2,000) →",

    # — Tabs / main sections —
    "tab.companies": "Companies",
    "tab.attendance": "Attendance Signals",
    "tab.lists": "Target lists",
    "tab.exports": "Exports",

    # — Companies tab —
    "companies.subtitle": "The 2,580 official Eurosatory 2026 exhibitors with multi-axis filtering.",
    "companies.column.company_name": "Company",
    "companies.column.country": "Country",
    "companies.column.company_type": "Type",
    "companies.column.activity_1liner": "Activity (1 line)",
    "companies.column.products_specific": "Products",
    "companies.column.services_specific": "Services",
    "companies.column.contact_email": "Email",
    "companies.column.linkedin_url": "LinkedIn",
    "companies.column.website_url": "Website",
    "companies.column.zone": "Geographic zone",

    # — Attendance tab —
    "attendance.title": "ATTENDANCE SIGNALS — who will potentially attend",
    "attendance.subtitle": "Enriched list of people and companies detected via public OSINT (corporate sites, press releases, indexed posts).",
    "attendance.filter.year": "Year",
    "attendance.filter.zone": "Zone",
    "attendance.filter.country": "Country",
    "attendance.filter.company_type": "Company type",
    "attendance.filter.search": "Free search (person / company / text)",
    "attendance.filter.include_exhibitors": "Include exhibitors",
    "attendance.view.grouped": "Grouped by company",
    "attendance.view.list": "List",
    "attendance.kpi.signals": "signals",
    "attendance.kpi.contacts": "contacts",

    # — Sidebar filters —
    "sidebar.identification": "Identification",
    "sidebar.geographic_zone": "Geographic zone",
    "sidebar.country": "Country",
    "sidebar.defense_segment": "Defense segment",
    "sidebar.find_targets": "Find targets",
    "sidebar.products_made": "Products made",
    "sidebar.services_sold": "Services sold",
    "sidebar.certifications": "Certifications",
    "sidebar.reset_filters": "Reset filters",

    # — Detail card —
    "detail.section.identity": "1 · Identity",
    "detail.section.what_they_do": "2 · What they do",
    "detail.section.intelligence": "3 · Commercial intelligence",
    "detail.section.contacts": "4 · Contacts",
    "detail.section.signals": "5 · Attendance signals",
    "detail.section.notes": "6 · CRM notes",
    "detail.field.core_business": "Core business",
    "detail.field.main_products_services": "Main products & services",
    "detail.field.activity_1liner": "Activity (1 line)",
    "detail.field.why_target": "Why target this company",
    "detail.field.founded": "Founded",
    "detail.field.last_checked": "Last checked",
    "detail.field.official_contacts": "Official contacts (catalogue)",
    "detail.field.generic_email": "(generic)",
    "detail.field.inferred_email": "(inferred · verify)",

    # — Pagination —
    "pagination.rows_per_page": "Rows per page",
    "pagination.page": "Page",
    "pagination.of": "of",
    "pagination.total": "total",
    "pagination.showing": "showing",

    # — Lists tab —
    "lists.title": "Target lists",
    "lists.create_new": "Create a new list",
    "lists.name": "List name",
    "lists.load": "Load these filters",
    "lists.delete": "Delete list",
    "lists.empty": "No list saved yet. Compose your filters on the Companies tab, then save them here.",

    # — Exports tab —
    "exports.title": "Exports",
    "exports.caption": "Exports take the current sidebar filters into account. On-disk exports land in `data/exports/`.",
    "exports.deliverable.title": "🎯 LeadForges commercial deliverable",
    "exports.deliverable.caption": "The file sold to customers. 2,580 companies, 15 columns, Excel autofilter enabled on every column, companies sorted by decreasing score. Canonical categories for filtering (75 products · 23 services · 31 technologies · 5 targets).",
    "exports.deliverable.xlsx_label": "⬇ XLSX deliverable (recommended)",
    "exports.deliverable.xlsx_help": "The file sold. 2 sheets : main + long-tail to retreat.",
    "exports.deliverable.xlsx_caption": "📦 {kb} kB · last generated by the pipeline.",
    "exports.deliverable.xlsx_missing": "Missing : {path}.  \nRun `.venv/bin/python scripts/export_xlsx.py` to regenerate it.",
    "exports.deliverable.csv_label": "⬇ CSV deliverable (CRM import)",
    "exports.deliverable.csv_help": "Flat format — flat columns, ready to import into Salesforce / HubSpot / Dynamics / Airtable.",
    "exports.deliverable.csv_missing": "Missing : {path}",
    "exports.deliverable.json_label": "⬇ JSON (integration / API)",
    "exports.deliverable.json_help": "Hierarchical format — native Python lists (products, categories, etc.).",
    "exports.deliverable.json_missing": "Missing : {path}",
    "exports.companion_pdfs": "**📕 Sales companion PDFs**",
    "exports.pdf.dico_label": "⬇ Data dictionary (PDF)",
    "exports.pdf.dico_help": "Reference document delivered to the customer : methodology, every column explained, score formula, Excel filter how-to.",
    "exports.pdf.uc_label": "⬇ 10 commercial use-cases (PDF)",
    "exports.pdf.uc_help": "10 typical commercial questions with the exact filters to apply + 3 example companies + commercial angle.",
    "exports.pdf.missing": "Missing : `{name}`. Run `.venv/bin/python {script}`",
    "exports.regenerate.button": "🔄 Regenerate the deliverable now",
    "exports.regenerate.help": "Re-runs the full pipeline : rule-based + merge manual + categories + XLSX + 2 companion PDFs. ~10 sec.",
    "exports.regenerate.starting": "Starting…",
    "exports.regenerate.step": "⏳ {label}…",
    "exports.regenerate.failed": "Failed on **{label}** :\n```\n{err}\n```",
    "exports.regenerate.done": "✅ Regeneration complete",
    "exports.regenerate.success": "Deliverable regenerated. Reload the page to see the new scores.",
    "exports.legacy.title": "**CRM exports (legacy)**",
    "exports.legacy.dynamics_header": "**For Microsoft Dynamics**",
    "exports.legacy.dynamics_button": "Export CSV Dynamics",
    "exports.legacy.salesforce_header": "**For Salesforce**",
    "exports.legacy.salesforce_button": "Export CSV Salesforce",
    "exports.legacy.hubspot_header": "**For HubSpot**",
    "exports.legacy.hubspot_button": "Export CSV HubSpot",
    "exports.legacy.airtable_header": "**For Airtable**",
    "exports.legacy.airtable_button": "Export CSV Airtable",
    "exports.legacy.prospecting_header": "**Sales prospecting (lean)**",
    "exports.legacy.prospecting_button": "Export prospecting CSV",
    "exports.legacy.full_header": "**Full database**",
    "exports.legacy.full_csv_button": "Export full CSV",
    "exports.legacy.full_xlsx_button": "Export full XLSX",
    "exports.legacy.written": "written : {path}",
    "exports.filtered.title": "**Direct download (filtered result)**",
    "exports.filtered.csv": "⬇ CSV (filters applied)",
    "exports.filtered.xlsx": "⬇ XLSX (filters applied)",
    "exports.csv": "Download CSV",
    "exports.excel": "Download Excel",

    # — Attendance signals export (sub-section of the Exports tab) —
    "exports.attendance.title": "### 📡 Attendance signals (prospection)",
    "exports.attendance.caption": "Enriched list of **people** and **companies** detected via public OSINT (corporate sites, press releases, indexed posts). Ready for Outlook / Lemlist / mail-merge — one row per contact with name, role, company, email, phone, LinkedIn and the source URL that justifies the signal.",
    "exports.attendance.stats": "**{total} contacts** · {with_email} with a direct email · {with_phone} with phone · {with_linkedin} with LinkedIn.",
    "exports.attendance.csv_label": "⬇ CSV (attendance signals)",
    "exports.attendance.csv_help": "One contact per row — name, role, company, country, email, phone, LinkedIn, source URL. Ready for Outlook / Lemlist / mail-merge.",
    "exports.attendance.xlsx_label": "⬇ XLSX (attendance signals)",
    "exports.attendance.xlsx_help": "Same columns as the CSV but as an Excel file — autofilter enabled on every column.",
    "exports.attendance.empty": "No attendance signals are loaded yet — open the **📡 Attendance Signals** tab to ingest some, then come back here to export.",

    # — Common actions —
    "action.save": "Save",
    "action.cancel": "Cancel",
    "action.delete": "Delete",
    "action.confirm": "Confirm",
    "action.export": "Export",
    "action.copy": "Copy",
    "action.search": "Search",
    "action.apply": "Apply",
    "action.clear": "Clear",
    "action.close": "Close",
    "action.back": "Back",

    # — Status badges —
    "badge.high": "High",
    "badge.medium": "Medium",
    "badge.low": "Low",
    "badge.unknown": "Unknown",
    "badge.priority_a_plus": "A+",
    "badge.priority_a": "A",
    "badge.priority_b": "B",
    "badge.priority_c": "C",

    # — Geographic zones —
    "zone.europe": "Europe",
    "zone.north_america": "North America",
    "zone.asia": "Asia",
    "zone.south_america": "South America",
    "zone.other": "Other (Africa, Oceania, ...)",

    # — Common strings —
    "common.loading": "Loading...",
    "common.no_data": "No data available.",
    "common.error": "An error occurred.",
    "common.yes": "Yes",
    "common.no": "No",
    "common.unknown": "—",
}


# Optional FR overrides (when the canonical FR text differs from the key
# itself). Keep this sparse — most FR strings ARE the key already.
_FR: dict[str, str] = {
    # Keep empty unless you discover a key whose French value should be
    # different from the key path itself. We use canonical short FR text
    # below as fallback when no key match is found.
}


# ─── PUBLIC API ───────────────────────────────────────────────────────

def t(key: str, /, **kwargs: Any) -> str:
    """Translate ``key`` for the current request language.

    Falls back to the key itself when no translation exists (useful
    during development — you'll spot untranslated strings visually).

    ``**kwargs`` are passed to ``str.format`` for placeholder
    interpolation, e.g. ``t("login.attempts_remaining", n=4)``.
    """
    lang = get_lang()
    if lang == "en":
        val = _EN.get(key)
    else:
        val = _FR.get(key)
    if val is None:
        val = key
    if kwargs:
        try:
            val = val.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return val


# Display-time mapping for the closed company-type taxonomy.
# Values in the DB are canonical FR — we keep them as-is for filtering
# (so the stored values never break) and translate ONLY for display.
COMPANY_TYPE_EN: dict[str, str] = {
    "OEM": "OEM",
    "Intégrateur": "Integrator",
    "Équipementier / Tier 1": "Tier-1 supplier",
    "Sous-traitant industriel": "Industrial subcontractor",
    "Distributeur": "Distributor",
    "Éditeur logiciel": "Software vendor",
    "Société de services": "Services company",
    "Bureau d'ingénierie": "Engineering office",
}


def display_company_type(fr_value: str) -> str:
    """Return the EN label when ``?lang=en`` is active, else the FR
    canonical value. Use as ``format_func=display_company_type`` on
    Streamlit widgets — the selected value stays FR (so filtering keeps
    working against the canonical FR values in the DB)."""
    if not fr_value:
        return ""
    if is_en():
        return COMPANY_TYPE_EN.get(fr_value, fr_value)
    return fr_value


# Display-time mapping for geographic zones.
GEOGRAPHIC_ZONE_EN: dict[str, str] = {
    "Europe": "Europe",
    "Amérique du Nord": "North America",
    "Asie": "Asia",
    "Amérique du Sud": "South America",
    "Autre (Afrique, Océanie, autres)": "Other (Africa, Oceania, other)",
}


def display_zone(fr_value: str) -> str:
    """Same pattern as ``display_company_type`` for the 5 geographic zones."""
    if not fr_value:
        return ""
    if is_en():
        return GEOGRAPHIC_ZONE_EN.get(fr_value, fr_value)
    return fr_value


def localized(row: Any, field: str, fallback: str = "") -> str:
    """For DB rows with parallel FR/EN columns (e.g. ``activity_1liner``
    + ``activity_1liner_en``), return the EN version when ``?lang=en``
    is active and the EN value is non-empty ; else fall back to FR ;
    else ``fallback``.

    Works with dict-like (``row["foo"]``) and attribute-like
    (``row.foo``) accessors transparently.
    """
    def _get(name: str) -> Any:
        try:
            return row[name]
        except (KeyError, TypeError):
            return getattr(row, name, None)

    if is_en():
        en_val = _get(f"{field}_en")
        if en_val is not None and str(en_val).strip():
            return str(en_val)
    fr_val = _get(field)
    if fr_val is not None and str(fr_val).strip():
        return str(fr_val)
    return fallback
