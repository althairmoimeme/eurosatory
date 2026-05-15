"""Premium corporate-defense CRM UI for the Eurosatory commercial intelligence base.

Five zones, as briefed:
1. Header — branding + global exports
2. KPI cards
3. Sidebar — filters
4. Main table — selectable rows, badge-styled priority + confidence
5. Detail card — 6 sections, with editable CRM fields (status, owner, lists, tags, notes)

Plus a "Custom lists" tab for sales-team list management.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

# Allow ``streamlit run app/ui/streamlit_app.py`` from the project root.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

# Demo mode detection happens PER REQUEST via ``_is_demo_mode()`` (defined
# further down). We deliberately do NOT set an env var here — Streamlit
# Cloud runs the app in a single long-lived process serving many users,
# and an env var would leak across sessions (a single user with ?demo=1
# could limit ALL other users to 50 rows). Detection must be session-
# scoped, hence read from ``st.query_params`` on every call.
from sqlalchemy import select

from app.crm.normalizers import (
    ALLOWED_COMPANY_TYPES,
    ALLOWED_LEAD_STATUSES,
    ALLOWED_TARGET_TYPES_FR,
)
from app.attendance.queries import queries_for_years
from app.attendance.repository import (
    DEFAULT_TABLE_COLUMNS as ATTEND_DEFAULT_COLS,
    VIEW_PRESETS as ATTEND_VIEWS,
    add_watch as attend_add_watch,
    apply_filters as attend_apply_filters,
    delete_watch as attend_delete_watch,
    find_exhibitor_for_signal as attend_find_exhibitor,
    get_signal as attend_get_signal,
    kpis as attend_kpis,
    list_duplicate_clusters as attend_dup_clusters,
    list_watches as attend_list_watches,
    mark_watch_seen as attend_mark_watch_seen,
    merge_duplicate_cluster as attend_merge_cluster,
    set_signal_validation as attend_set_validation,
    sibling_signals as attend_sibling_signals,
    signals_dataframe,
    signals_for_exhibitor as attend_signals_for_exhibitor,
    watch_unread_signals as attend_watch_unread,
)
from app.attendance.scorer import ROLE_PATTERNS as ATTEND_ROLE_PATTERNS
from app.attendance.seed import RawSignal, upsert_signal
from app.crm.matchmaking import (
    BUYING_NEEDS_VOCAB,
    SELLABLE_OFFERINGS,
    SOURCEABLE_PRODUCTS,
    compute_match_dataframe,
    matched_columns_for_table,
)
from app.crm.repository import (
    add_to_list,
    apply_filters,
    archive_list,
    create_custom_list,
    crm_dataframe,
    delete_list,
    duplicate_list,
    get_custom_list,
    get_member_note,
    kpis,
    list_activity_timeline,
    list_archived_custom_lists,
    list_custom_lists,
    list_member_ids,
    list_member_notes_map,
    list_overlap,
    lists_for_exhibitor,
    merge_lists,
    remove_from_list,
    set_member_note,
    sync_dynamic_list,
    toggle_pin_list,
    unarchive_list,
    update_custom_list,
)
from app.exports.attendance_export import (
    export_attendance_crm_csv,
    export_attendance_full_csv,
)
# PDF export is optional — if fpdf2 is missing (e.g. on a slim cloud
# install) the rest of the app must still work. The button that uses
# ``render_company_pdf`` checks ``_PDF_AVAILABLE`` and degrades silently.
try:
    from app.exports.pdf_fiche import render_company_pdf  # type: ignore
    _PDF_AVAILABLE = True
except ImportError:
    render_company_pdf = None  # type: ignore[assignment]
    _PDF_AVAILABLE = False
from app.crm.schema import DEFAULT_TABLE_COLUMNS
from app.database import (
    ActivityLog,
    CommercialNote,
    CrawledPage,
    CustomList,
    CustomListMember,
    Exhibitor,
    ExhibitorContact,
    ExhibitorIntelligence,
    ExhibitorTag,
    SessionLocal,
)
from app.exports.crm_exports import (
    DYNAMICS_LEAD_SOURCE,
    DYNAMICS_RENAME,
    export_airtable_csv,
    export_custom_list_csv,
    export_dynamics_csv,
    export_full_csv,
    export_full_xlsx,
    export_hubspot_csv,
    export_prospecting_csv,
    export_salesforce_csv,
)
from app.processors.defense_taxonomy import DEFENSE_TAXONOMY


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFENSE_SEGMENTS = sorted(list(DEFENSE_TAXONOMY.keys()) + ["Other"])
CRM_STAGES = ["Priority targeting", "Qualification", "Watchlist", "Low priority"]
INTEREST_LEVELS = ["very_high", "high", "medium", "low"]
DATA_CONFIDENCES = ["High", "Medium", "Low"]

PRIORITY_BADGE = {
    "A+": ("#780000", "#FFFFFF"),
    "A":  ("#C1121F", "#FFFFFF"),
    "B":  ("#D97706", "#FFFFFF"),
    "C":  ("#2F5D7C", "#FFFFFF"),
    "D":  ("#94A3B8", "#FFFFFF"),
}
CONFIDENCE_BADGE = {
    "High":   ("#1F7A4D", "#FFFFFF"),
    "Medium": ("#D97706", "#FFFFFF"),
    "Low":    ("#C1121F", "#FFFFFF"),
}


# ---------------------------------------------------------------------------
# Page setup + corporate CSS
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="LeadForges",
    page_icon="🛡️",
    layout="wide",
)

CUSTOM_CSS = """
<style>
:root {
    --navy: #071A33;
    --navy-soft: #0B2E4A;
    --steel: #2F5D7C;
    --light-bg: #F4F6F8;
    --text: #2B2F33;
    --alert: #C1121F;
    --alert-dark: #780000;
    --success: #1F7A4D;
    --warning: #D97706;
}
html, body, [class*="css"] {
    font-family: "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
}
h1, h2, h3, h4 { color: var(--navy); font-weight: 600; letter-spacing: -0.01em; }
/* Palantir-inspired full-width header — pure black bg, minimalist
   logo (a wireframe sphere with a single orbital line), tight
   typography, no chrome. Uses negative margins to bleed across the
   default Streamlit container padding so it spans edge-to-edge. */
.app-header {
    background: #07070A;
    color: #F2F4F7;
    padding: 1.5rem 2rem;
    margin: -1.5rem -2rem 1.25rem -2rem;
    display: flex; align-items: center; gap: 1.1rem;
    border-bottom: 1px solid #1A1D24;
}
.app-header__logo {
    width: 44px; height: 44px; flex-shrink: 0;
    color: #E2E8F0;
}
.app-header__title {
    font-size: 1.45rem; font-weight: 600; letter-spacing: -0.015em;
    margin: 0; line-height: 1.1; color: #F2F4F7;
}
.app-header__subtitle {
    font-size: 0.78rem; color: #8A93A0; margin: 0.25rem 0 0;
    letter-spacing: 0.04em; text-transform: uppercase; font-weight: 500;
}
.app-header__brand-mark {
    margin-left: auto; font-size: 0.7rem; color: #4A5163;
    letter-spacing: 0.18em; text-transform: uppercase; font-weight: 600;
}
.kpi-card {
    background: var(--light-bg); border-radius: 8px; padding: 0.85rem 1rem;
    border-left: 4px solid var(--navy-soft);
}
.kpi-label { font-size: 0.75rem; color: #4A5158; text-transform: uppercase; letter-spacing: 0.06em; }
.kpi-value { font-size: 1.55rem; font-weight: 600; color: var(--navy); margin-top: 0.15rem; }
.kpi-card.alert     { border-left-color: var(--alert); }
.kpi-card.warning   { border-left-color: var(--warning); }
.kpi-card.success   { border-left-color: var(--success); }
.badge {
    display: inline-block; padding: 0.18rem 0.55rem; border-radius: 4px;
    font-size: 0.78rem; font-weight: 600; letter-spacing: 0.02em;
}
.section-title {
    font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--steel); border-bottom: 1px solid #E2E6EA; padding-bottom: 0.25rem;
    margin: 1rem 0 0.5rem; font-weight: 600;
}
[data-testid="stMetricValue"] { color: var(--navy); }
.stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {
    color: var(--navy); border-bottom-color: var(--alert);
}
.stButton > button[kind="primary"] {
    background: var(--navy-soft); border: none;
}
.stButton > button[kind="primary"]:hover { background: var(--alert-dark); }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Cached data loader
# ---------------------------------------------------------------------------


@st.cache_data(ttl=120)
def _targeting_profile_by_eid() -> dict[int, dict]:
    """Return a {exhibitor_id: profile_dict} index for fast detail lookup.

    The JSON file is the same one as ``_load_targeting_profiles`` —
    this helper just exposes a dict view so ``render_detail`` can grab
    a single exhibitor's profile in O(1).
    """
    import json
    from pathlib import Path
    path = Path("data/exports/targeting_profiles_final.json")
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(r["exhibitor_id"]): r for r in raw}


@st.cache_data(ttl=120)
def _load_targeting_profiles() -> pd.DataFrame:
    """Load the 6-field Eurosatory 2026 targeting profile + canonical
    categories from the merged JSON export.

    The JSON is the output of the pipeline ``run_targeting_profiles.py``
    → ``merge_manual_overrides.py`` → ``normalize_categories.py``.
    Returns an empty DataFrame if the file is missing — the rest of the
    UI then degrades gracefully (cells show "—").
    """
    import json
    from pathlib import Path

    path = Path("data/exports/targeting_profiles_final.json")
    if not path.exists():
        return pd.DataFrame()
    raw = json.loads(path.read_text(encoding="utf-8"))

    rows = []
    for r in raw:
        eid = int(r["exhibitor_id"])
        rows.append({
            "_eid": eid,
            "activity_1liner": r.get("activity_1liner") or "",
            "activity_1liner_en": r.get("activity_1liner_en") or "",
            "supply_chain_tier": r.get("supply_chain_tier") or "N/A",
            "products_specific": " · ".join(r.get("products") or []) or None,
            "products_specific_en": " · ".join(r.get("products_en") or []) or None,
            "products_categories": " · ".join(r.get("products_categories") or []) or None,
            "services_specific": " · ".join(r.get("services") or []) or None,
            "services_specific_en": " · ".join(r.get("services_en") or []) or None,
            "services_categories": " · ".join(r.get("services_categories") or []) or None,
            "target_buyers": ", ".join(r.get("target_buyers") or []) or None,
            "target_buyers_en": ", ".join(r.get("target_buyers_en") or []) or None,
            "technologies_specific": " · ".join(r.get("technologies") or []) or None,
            "technologies_specific_en": " · ".join(r.get("technologies_en") or []) or None,
            "technologies_categories": " · ".join(r.get("technologies_categories") or []) or None,
            "why_target": r.get("why_target") or "",
            "why_target_en": r.get("why_target_en") or "",
            "targeting_score": int(r.get("completeness_score", 0)),
            "targeting_source": r.get("data_source_strength") or "",
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=120)
def load_crm() -> pd.DataFrame:
    """Build the main CRM dataframe + merge in the Eurosatory 2026
    targeting profile (6 fields + 3 canonical category fields + score).
    """
    df = crm_dataframe()
    profiles = _load_targeting_profiles()
    if df.empty or profiles.empty:
        return df

    # Join key : ``account_id`` is "ESY26-{exhibitor_id:05d}".
    df = df.copy()
    df["_eid"] = (
        df["account_id"].fillna("")
        .str.replace("ESY26-", "", regex=False)
        .replace("", "0").astype(int)
    )
    merged = df.merge(profiles, on="_eid", how="left")
    merged.drop(columns=["_eid"], inplace=True)

    # Smart-merge ``business_model`` + ``supply_chain_tier`` + product
    # categories into the new closed-list ``company_type`` taxonomy
    # (8 categories — see app.crm.normalizers.ALLOWED_COMPANY_TYPES).
    from app.crm.normalizers import derive_company_type

    def _split_cat_str(v) -> list[str]:
        if not isinstance(v, str) or not v:
            return []
        return [c.strip() for c in v.split("·") if c.strip()]

    merged["company_type"] = merged.apply(
        lambda r: derive_company_type(
            business_model=r.get("business_model"),
            supply_chain_tier=r.get("supply_chain_tier"),
            products_categories=_split_cat_str(r.get("products_categories")),
            activity_1liner=r.get("activity_1liner"),
        ),
        axis=1,
    )

    # Geographic zone — coarse 5-bucket region label derived from the
    # ISO2 country code (preferred) or the French country name (fallback).
    # Used by the "Zone géographique" sidebar filter.
    from app.crm.normalizers import country_to_zone
    merged["zone"] = merged.apply(
        lambda r: country_to_zone(
            iso2=r.get("country_iso2"),
            country_name=r.get("country"),
        ),
        axis=1,
    )
    return merged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inject_session_favorites(df: pd.DataFrame) -> pd.DataFrame:
    """Overwrite ``df["is_favorite"]`` with the session-scoped favorites.

    Called by every view that reads ``is_favorite``. Keeps the cached
    ``load_crm()`` output free of per-session state — the cache key
    stays stable across users while each session sees its own ⭐.
    """
    if df is None or df.empty:
        return df
    try:
        from app.ui.auth import get_favorite_ids
        fav = get_favorite_ids()
    except Exception:  # noqa: BLE001
        fav = set()
    out = df.copy()
    if "account_id" in out.columns:
        out["_eid_tmp"] = (
            out["account_id"].fillna("")
            .str.replace("ESY26-", "", regex=False)
            .replace("", "0").astype(int)
        )
        out["is_favorite"] = out["_eid_tmp"].isin(fav)
        out.drop(columns=["_eid_tmp"], inplace=True)
    return out


def _localize_df(df: pd.DataFrame) -> pd.DataFrame:
    """When the current request is in EN (``?lang=en``), swap the
    English variants of data columns INTO the FR-named columns. This
    way the rest of the rendering code stays language-agnostic ; the
    EN content is seamlessly displayed wherever a FR column would be.

    Swapped fields :
      - activity_1liner ← activity_1liner_en
      - products_specific ← products_specific_en
      - services_specific ← services_specific_en
      - target_buyers ← target_buyers_en
      - technologies_specific ← technologies_specific_en
      - why_target ← why_target_en

    No-op when lang=fr or no EN column exists.
    """
    from app.ui.i18n import is_en
    if not is_en():
        return df
    out = df.copy()
    pairs = [
        ("activity_1liner", "activity_1liner_en"),
        ("products_specific", "products_specific_en"),
        ("services_specific", "services_specific_en"),
        ("target_buyers", "target_buyers_en"),
        ("technologies_specific", "technologies_specific_en"),
        ("why_target", "why_target_en"),
    ]
    for fr_col, en_col in pairs:
        if en_col in out.columns and fr_col in out.columns:
            # Use EN value when non-empty, else fall back to FR
            en_series = out[en_col].fillna("").astype(str)
            mask = en_series.str.strip() != ""
            out.loc[mask, fr_col] = out.loc[mask, en_col]
    return out


def _badge(label: str, palette: dict) -> str:
    bg, fg = palette.get(label, ("#94A3B8", "#FFFFFF"))
    return f'<span class="badge" style="background:{bg};color:{fg};">{label}</span>'


def _kpi(label: str, value, css_class: str = "") -> None:
    st.markdown(
        f'<div class="kpi-card {css_class}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def render_header(df: pd.DataFrame) -> None:
    """Full-bleed dark header inspired by Palantir's data-platform UIs :
    an inline-SVG wireframe sphere as the logo, tight uppercase
    subtitle, brand mark on the right.
    """
    # Inline SVG : wireframe globe (3 great circles + orbital ring) —
    # evokes "intelligence node", recognisable as a data/network icon
    # without leaning on emoji.
    logo_svg = (
        '<svg class="app-header__logo" viewBox="0 0 64 64" fill="none" '
        'stroke="currentColor" stroke-width="1.2" '
        'stroke-linecap="round" xmlns="http://www.w3.org/2000/svg">'
        # outer circle
        '<circle cx="32" cy="32" r="22"/>'
        # equator (horizontal great circle)
        '<ellipse cx="32" cy="32" rx="22" ry="6"/>'
        # tilted great circle
        '<ellipse cx="32" cy="32" rx="22" ry="6" '
        'transform="rotate(60 32 32)"/>'
        # tilted great circle the other way
        '<ellipse cx="32" cy="32" rx="22" ry="6" '
        'transform="rotate(-60 32 32)"/>'
        # outer orbital ring (looks like a connection halo)
        '<ellipse cx="32" cy="32" rx="28" ry="11" '
        'transform="rotate(-20 32 32)" stroke-opacity="0.4"/>'
        # central node dot
        '<circle cx="32" cy="32" r="2" fill="currentColor" '
        'stroke="none"/>'
        # 4 tiny connection nodes on the orbital ring
        '<circle cx="6" cy="36" r="1.4" fill="currentColor" '
        'stroke="none"/>'
        '<circle cx="58" cy="28" r="1.4" fill="currentColor" '
        'stroke="none"/>'
        '<circle cx="46" cy="14" r="1.4" fill="currentColor" '
        'stroke="none"/>'
        '<circle cx="18" cy="50" r="1.4" fill="currentColor" '
        'stroke="none"/>'
        "</svg>"
    )
    st.markdown(
        '<div class="app-header">'
        + logo_svg
        + '<div>'
        '<div class="app-header__title">LeadForges</div>'
        '<div class="app-header__subtitle">Defense commercial '
        "intelligence · built · sold · buying · scored</div>"
        '</div>'
        '<div class="app-header__brand-mark">LEADFORGES</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_companies_topbar(df: pd.DataFrame, key_prefix: str = "topbar") -> None:
    """Global CRM exports row : CSV / XLSX / Dynamics / Salesforce / HubSpot
    + Reset. Rendered ONLY in tabs where the CRM scope is relevant
    (Companies / Pipeline / Custom lists / Exports). ``key_prefix`` lets
    the same component appear on multiple tabs without
    ``StreamlitDuplicateElementKey``.
    """
    cols = st.columns([1, 1, 1, 1, 1, 0.6])
    with cols[0]:
        st.download_button(
            "⬇ CSV", data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"crm_full_{datetime.utcnow():%Y%m%d_%H%M%S}.csv",
            mime="text/csv", use_container_width=True,
            key=f"{key_prefix}_csv",
        )
    with cols[1]:
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="Accounts")
        buf.seek(0)
        st.download_button(
            "⬇ XLSX", data=buf,
            file_name=f"crm_full_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"{key_prefix}_xlsx",
        )
    with cols[2]:
        if st.button("⬇ Dynamics", use_container_width=True,
                     key=f"{key_prefix}_dynamics"):
            p = export_dynamics_csv()
            st.success(f"écrit : {p}")
    with cols[3]:
        if st.button("⬇ Salesforce", use_container_width=True,
                     key=f"{key_prefix}_salesforce"):
            p = export_salesforce_csv()
            st.success(f"écrit : {p}")
    with cols[4]:
        if st.button("⬇ HubSpot", use_container_width=True,
                     key=f"{key_prefix}_hubspot"):
            p = export_hubspot_csv()
            st.success(f"écrit : {p}")
    with cols[5]:
        if st.button("Reset", use_container_width=True,
                     key=f"{key_prefix}_reset"):
            for k in list(st.session_state.keys()):
                if k.startswith("filter_"):
                    del st.session_state[k]
            st.rerun()


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def render_kpis(df: pd.DataFrame) -> None:
    k = kpis(df)
    cols = st.columns(6)
    with cols[0]: _kpi("Total companies", k["total"])
    with cols[1]: _kpi("A+/A targets", k["a_or_aplus"], css_class="alert")
    with cols[2]: _kpi("Potential buyers", k["potential_buyers"])
    with cols[3]: _kpi("Potential partners", k["potential_partners"], css_class="success")
    with cols[4]: _kpi("To verify", k["to_verify"], css_class="warning")
    with cols[5]: _kpi("Avg lead score", k["avg_lead_score"])


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------


def _collect_split(df: pd.DataFrame, col: str) -> set[str]:
    """Collect all distinct labels from a ``;``-joined column."""
    out: set[str] = set()
    if col not in df.columns:
        return out
    for v in df[col].dropna().unique():
        for chunk in str(v).split(";"):
            for piece in chunk.split(","):
                p = piece.strip()
                if p:
                    out.add(p)
    return out


def _render_language_toggle() -> None:
    """Small flag-style FR/EN toggle at the top of the sidebar.

    Clicking swaps ``?lang=en`` / ``?lang=fr`` in the URL — kept as
    query param so the user can bookmark the URL or share it.
    """
    from app.ui.i18n import get_lang
    current = get_lang()
    # Build target hrefs preserving other query params
    try:
        qp = dict(st.query_params)
    except Exception:  # noqa: BLE001
        qp = {}
    def _link(lang_code: str) -> str:
        new = {**qp, "lang": lang_code}
        # Drop lang if going to default FR for a cleaner URL
        if lang_code == "fr":
            new.pop("lang", None)
        qs = "&".join(f"{k}={v}" for k, v in new.items()) if new else ""
        return ("?" + qs) if qs else "?"

    fr_style = "font-weight: 700; color: #0A0A0A;" if current == "fr" else "color: #71717A;"
    en_style = "font-weight: 700; color: #0A0A0A;" if current == "en" else "color: #71717A;"
    st.sidebar.markdown(
        f"<div style='text-align: right; font-family: monospace; font-size: 12px; margin-bottom: 8px;'>"
        f"<a href='{_link('fr')}' target='_self' style='{fr_style} text-decoration: none;'>FR</a>"
        f" <span style='color: #E4E4E7;'>|</span> "
        f"<a href='{_link('en')}' target='_self' style='{en_style} text-decoration: none;'>EN</a>"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_sidebar(df: pd.DataFrame) -> dict:
    from app.ui.i18n import is_en
    _render_language_toggle()
    st.sidebar.markdown(f"### 🔎 {'Filters' if is_en() else 'Filtres'}")
    with st.sidebar:
        search_text = st.text_input(
            "Free search" if is_en() else "Recherche libre",
            key="filter_search",
        )
        only_website = st.checkbox("Avec site web uniquement", key="filter_only_website")
        # Removed filters : "A+ / A uniquement" and "High confidence uniquement"
        only_priority = False
        only_high_conf = False

        # ============================================================
        # 🎯 Eurosatory 2026 — Ciblage commercial (the database we sell)
        # ============================================================
        # These filters are the value-add of the product : they let a
        # defense rep slice the 2580 exhibitors by what they actually
        # build / sell / buy. The categories are canonical (75 prod /
        # 23 svc / 31 tech) so a multi-select on them works.
        st.markdown("**🎯 Ciblage commercial**")
        st.caption(
            "Filtres sur la donnée enrichie : 75 catégories produits, "
            "23 services, 31 technos, 5 cibles clients. Sélectionne pour "
            "trouver « tous les fabricants de drones », « tous les "
            "fournisseurs d'optronique », etc."
        )
        from app.processors.taxonomy_normalize import (
            PRODUCT_CATEGORIES, SERVICE_CATEGORIES, TECHNOLOGY_CATEGORIES,
        )
        # Drop the catch-all "Autre" from the picker — we don't want
        # users to filter ON it, it's a noise marker.
        prod_cat_options = [c for c in PRODUCT_CATEGORIES if not c.startswith("Autre")]
        svc_cat_options = [c for c in SERVICE_CATEGORIES if not c.startswith("Autre")]
        tech_cat_options = [c for c in TECHNOLOGY_CATEGORIES if not c.startswith("Autre")]
        target_buyer_options = [
            "MoD / Armées", "Primes défense", "Sécurité civile",
            "Industriels défense", "Export / international",
        ]
        # The supply-chain tier filter has been merged into the new
        # closed-list "Type d'entreprise" filter — rendered first because
        # it's the most-used dimension. Internal ``supply_chain_tier`` is
        # still computed for back-end uses (target_lists, exports).
        supply_chain_tier_filter: list[str] = []
        company_types = st.multiselect(
            "Type d'entreprise",
            ALLOWED_COMPANY_TYPES,
            key="filter_company_types",
            help="Taxonomie fermée 8 valeurs :\n"
            "• OEM = vend le système final\n"
            "• Intégrateur = assemble / intègre\n"
            "• Équipementier / Tier 1 = sous-système critique\n"
            "• Sous-traitant industriel = pièces, fabrication\n"
            "• Distributeur = revend marques tierces\n"
            "• Éditeur logiciel = software / SaaS\n"
            "• Société de services = MCO, formation, conseil, "
            "institutionnel, finance\n"
            "• Bureau d'ingénierie = R&D, conseil technique",
        )
        targeting_product_categories = st.multiselect(
            "Catégories produits", sorted(prod_cat_options),
            key="filter_targeting_prod_cats",
            help="Le commercial filtre par bucket métier (drones, optronique, "
            "munitions, …) et trouve toutes les sociétés qui ont au moins "
            "ce bucket dans leurs produits.",
        )
        targeting_service_categories = st.multiselect(
            "Catégories services", sorted(svc_cat_options),
            key="filter_targeting_svc_cats",
            help="MCO, intégration, formation, distribution, sous-traitance, "
            "certif, …",
        )
        # Removed filters : "Catégories technologies", "Score de ciblage min",
        # "Origine fiche".
        targeting_technology_categories: list[str] = []
        min_targeting_score = 0
        targeting_sources: list[str] = []
        target_buyers_filter = st.multiselect(
            "Cibles clients (à qui ils vendent)", target_buyer_options,
            key="filter_target_buyers",
            help="Taxonomie fermée 5 valeurs : MoD/Armées · Primes défense · "
            "Sécurité civile · Industriels défense · Export / international.",
        )

        st.markdown("---")
        st.markdown("**📋 Identification**")
        # Geographic zone : coarse 5-bucket region filter. Combines well
        # with the Pays multiselect (zone narrows the country list).
        from app.crm.normalizers import GEOGRAPHIC_ZONES as _ZONES
        zones = st.multiselect(
            "Zone géographique",
            list(_ZONES),
            key="filter_zones",
            help="Europe · Amérique du Nord · Asie · Amérique du Sud · "
            "Autre (Afrique, Océanie, autres).",
        )
        # If a zone is selected, restrict the country picker to that
        # zone's countries so the user doesn't pick incompatible
        # combinations.
        if zones and "zone" in df.columns:
            country_pool = sorted([
                c for c in df.loc[df["zone"].isin(zones), "country"]
                .dropna().unique()
            ])
        else:
            country_pool = sorted([c for c in df["country"].dropna().unique()])
        countries = st.multiselect(
            "Pays", country_pool,
            key="filter_countries",
        )
        defense_segments = st.multiselect(
            "Segment défense", DEFENSE_SEGMENTS, key="filter_segments",
            help="Segment haut-niveau extrait de la classification défense.",
        )
        # ``company_types`` is rendered up in the "🎯 Ciblage" section.
        # Legacy fields retired with the Pipeline / CRM tab — keep empty
        # defaults so apply_filters() stays a no-op on them. Removed from
        # the UI : Priorité (Defense fit), Type de cible, Lead status,
        # CRM stage, Besoin d'achat principal, Niveau d'intérêt.
        priorities: list[str] = []
        target_types: list[str] = []
        lead_statuses: list[str] = []
        crm_stages: list[str] = []
        buying_needs: list[str] = []
        interest_levels: list[str] = []

        st.markdown("**🎯 Trouver des cibles**")
        st.caption(
            "Chaque option affiche le **nombre de sociétés réellement "
            "matchées** après restriction par type d'entreprise. Les "
            "catégories vides sont masquées."
        )
        buying_needs_full_filter: list[str] = []

        # ----- Compute population-aware counts ----------------------------
        from collections import Counter as _Counter

        _MAKER_TYPES = {
            "OEM", "Intégrateur",
            "Équipementier / Tier 1", "Sous-traitant industriel",
        }
        _SELLER_TYPES = _MAKER_TYPES | {"Distributeur"}
        _SERVICE_TYPES = {
            "Société de services", "Bureau d'ingénierie",
            "Éditeur logiciel", "Intégrateur",
        }

        def _cat_counts(scope_df: pd.DataFrame, col: str) -> _Counter:
            """Count canonical categories within a population subset."""
            counts: _Counter = _Counter()
            if col not in scope_df.columns:
                return counts
            for v in scope_df[col].dropna():
                if not isinstance(v, str):
                    continue
                for c in v.split("·"):
                    c = c.strip()
                    if not c or c.startswith("Autre"):
                        continue
                    counts[c] += 1
            return counts

        if "company_type" in df.columns:
            built_counts = _cat_counts(
                df[df["company_type"].isin(_MAKER_TYPES)], "products_categories"
            )
            sold_counts = _cat_counts(
                df[df["company_type"].isin(_SELLER_TYPES)], "products_categories"
            )
            svc_counts = _cat_counts(
                df[df["company_type"].isin(_SERVICE_TYPES)], "services_categories"
            )
        else:
            built_counts = sold_counts = _cat_counts(df, "products_categories")
            svc_counts = _cat_counts(df, "services_categories")

        # ----- Helper : multiselect with count-formatted options ---------
        def _picker(label: str, counts: _Counter, key: str, helptext: str):
            # Sort options by count descending, hide zero-count
            options = [c for c, _ in counts.most_common() if counts[c] > 0]
            return st.multiselect(
                label, options, key=key,
                format_func=lambda c: f"{c}  ({counts[c]})",
                help=helptext,
            )

        products_built_filter = _picker(
            "PRODUITS FABRIQUÉS",
            built_counts,
            "filter_products_built",
            "Catégorie produit que la société FABRIQUE. Restreint aux "
            "fabricants : OEM · Intégrateur · Équipementier/Tier 1 · "
            "Sous-traitant industriel.",
        )
        products_sold_filter = _picker(
            "PRODUITS VENDUS",
            sold_counts,
            "filter_products_sold",
            "Catégorie produit COMMERCIALISÉE. Restreint aux fabricants "
            "+ distributeurs.",
        )
        services_filter = _picker(
            "SERVICES VENDUS",
            svc_counts,
            "filter_services_sold",
            "Service commercialisé (MCO, intégration, formation, conseil, "
            "ingénierie). Restreint aux types service : Société de "
            "services · Bureau d'ingénierie · Éditeur logiciel · Intégrateur.",
        )

        st.markdown("**🏅 Certifications**")
        # Compute canonical cert counts (deduped + filtered to the closed
        # whitelist so stray scraping artefacts don't leak into the picker).
        _CERT_WHITELIST = {
            "ISO 9001", "ISO 14001", "ISO 45001", "ISO 27001",
            "ISO 13485", "EN 9100", "EN 9110", "EN 9120",
            "AS9100", "AS9120", "AS9110",
            "NATO AQAP", "NATO AQAP 2110", "NATO AQAP 2210",
            "IATF 16949", "ITAR", "EAR", "CMMC", "Cyber Essentials",
            "Common Criteria", "FIPS 140", "VS-NfD", "NCAGE code", "OFAC",
            "BSI",
        }
        cert_counts: _Counter = _Counter()
        if "certifications" in df.columns:
            for v in df["certifications"].dropna():
                if not isinstance(v, str):
                    continue
                for c in v.split(";"):
                    c = c.strip()
                    if c and c in _CERT_WHITELIST:
                        cert_counts[c] += 1
        cert_options = [c for c, n in cert_counts.most_common() if n > 0]
        certs_filter = st.multiselect(
            "Filtrer par certifications",
            cert_options, key="filter_certifications",
            format_func=lambda c: f"{c}  ({cert_counts[c]})",
            help="ISO 9001, EN 9100, AS9100, NATO AQAP, ITAR, CMMC… "
            "Détectées sur les pages publiques de la société. Le compteur "
            "indique le nombre de fiches qui mentionnent la certification "
            "(à confirmer en discovery call avant un contrat).",
        )

        st.markdown("**⭐ Mes vues rapides**")
        only_favorites = st.checkbox(
            "⭐ Mes favoris uniquement", key="filter_only_favorites",
        )
        # Removed filter : "📅 Prochaine action"
        next_action_filter = "(any)"

        st.markdown("**📅 Plages numériques**")
        # Founding year range — only show if at least 1 row has a year
        years_present = (
            df["founding_year"].dropna().astype(int).tolist()
            if "founding_year" in df.columns else []
        )
        if years_present:
            y_min, y_max = min(years_present), max(years_present)
            year_range = st.slider(
                "Année de fondation",
                min_value=int(y_min), max_value=int(y_max),
                value=(int(y_min), int(y_max)), step=1,
                key="filter_year_range",
                help="Filtre sur les sociétés dont la `founding_year` est connue.",
            )
        else:
            year_range = None

        # Employee count
        sizes = (
            df["company_size"].fillna("").astype(str).tolist()
            if "company_size" in df.columns else []
        )
        emp_numbers: list[int] = []
        import re as _re
        for s_ in sizes:
            for m in _re.finditer(r"~(\d{2,6})", s_):
                emp_numbers.append(int(m.group(1)))
        if emp_numbers:
            e_min, e_max = min(emp_numbers), max(emp_numbers)
            emp_range = st.slider(
                "Effectif (employés détectés)",
                min_value=int(e_min), max_value=int(e_max),
                value=(int(e_min), int(e_max)), step=10,
                key="filter_emp_range",
                help="Filtre sur les sociétés dont l'effectif est extrait du site.",
            )
        else:
            emp_range = None

        # Hall (booth_number prefix "Hall 5", "Hall 5a", "Outdoor", ...)
        all_halls: set[str] = set()
        if "booth_number" in df.columns:
            for v in df["booth_number"].dropna().astype(str).unique():
                if "/" in v:
                    all_halls.add(v.split("/", 1)[0].strip())
                elif v.strip():
                    all_halls.add(v.strip())
        halls_filter = (
            st.multiselect("Hall / pavilion", sorted(all_halls), key="filter_halls")
            if all_halls else []
        )

        st.markdown("**🤝 Affiliations & groupe**")
        all_assocs = _collect_split(df, "industry_associations")
        assocs_filter = st.multiselect(
            "Associations industrielles",
            sorted(all_assocs), key="filter_associations",
            help="Membres de GIFAS, ASD, NDIA, AIA, ADS Group, AIAD, etc.",
        )
        parent_present = st.selectbox(
            "Filiale d'un groupe ?",
            ["(any)", "Oui", "Non"], key="filter_parent_present",
            help="Filtrer les sociétés qui sont ou ne sont pas filiales d'un groupe identifié.",
        )

        # Removed section : "Qualité" (Data confidence + Revue manuelle) and
        # "Lead score min".
        confidences: list[str] = []
        manual_review = None
        min_score = 0

        all_lists = list_custom_lists()
        list_names = [l.name for l in all_lists]
        custom_lists_filter = (
            st.multiselect("Listes commerciales", list_names, key="filter_custom_lists")
            if list_names else []
        )

        all_tags_set: set[str] = set()
        for v in df["tags"].dropna().unique():
            for t in (v or "").split(";"):
                t = t.strip()
                if t:
                    all_tags_set.add(t)
        tags_filter = (
            st.multiselect("Tags", sorted(all_tags_set), key="filter_tags")
            if all_tags_set else []
        )

    return {
        "countries": countries,
        "zones": zones,
        "defense_segments": defense_segments,
        "target_types": target_types,
        "priority_levels": priorities,
        "lead_statuses": lead_statuses,
        "crm_stages": crm_stages,
        "company_types": company_types,
        "buying_needs": buying_needs,
        "interest_levels": interest_levels,
        "data_confidences": confidences,
        "custom_lists": custom_lists_filter,
        "tags": tags_filter,
        "products_built_any": products_built_filter,
        "products_sold_any": products_sold_filter,
        "services_sold_any": services_filter,
        "buying_needs_any": buying_needs_full_filter,
        "certifications_any": certs_filter,
        "associations_any": assocs_filter,
        "has_parent_group": (
            None if parent_present == "(any)" else (parent_present == "Oui")
        ),
        "founding_year_range": year_range,
        "employee_count_range": emp_range,
        "halls_any": halls_filter,
        "only_favorites": only_favorites,
        "next_action_filter": next_action_filter,
        "only_with_website": only_website,
        "only_high_confidence": only_high_conf,
        "only_priority_targets": only_priority,
        "manual_review": manual_review,
        "min_lead_score": min_score,
        "search_text": search_text,
        # Eurosatory 2026 targeting filters
        "targeting_product_categories": targeting_product_categories,
        "targeting_service_categories": targeting_service_categories,
        "targeting_technology_categories": targeting_technology_categories,
        "target_buyers_any": target_buyers_filter,
        "min_targeting_score": min_targeting_score,
        "targeting_sources": targeting_sources,
        "supply_chain_tier": supply_chain_tier_filter,
    }


# ---------------------------------------------------------------------------
# Main table
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Matchmaking panel — declarative buyer profile
# ---------------------------------------------------------------------------


def render_matchmaking_panel(df: pd.DataFrame) -> dict:
    """Show the "what I sell / what I'm sourcing" inputs.

    Returns ``{"my_offerings": [...], "my_sourcing": [...], "my_segments": [...],
    "active": bool}``.  The values live in ``st.session_state`` so they survive
    reruns — they're meant to be set once per session by the buyer.
    """
    with st.container():
        st.markdown(
            '<div class="section-title">💼 Matchmaking — qui sont mes cibles ?</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Renseigne **ce que tu vends** (pour trouver des acheteurs probables) "
            "et/ou **ce que tu cherches à sourcer** (pour trouver des fournisseurs). "
            "Le tableau ci-dessous se réordonne par fit avec ton profil. Aucune "
            "donnée envoyée nulle part — ça reste local à ta session."
        )
        c1, c2, c3 = st.columns([3, 3, 2])
        with c1:
            my_offerings = st.multiselect(
                "🎯 Ce que JE VENDS (cherche des acheteurs)",
                SELLABLE_OFFERINGS,
                key="match_offerings",
                help="Sélectionne ce que tu fabriques / vends. On cherche les "
                "exposants dont les besoins d'achat probables matchent.",
            )
        with c2:
            my_sourcing = st.multiselect(
                "🛒 Ce que JE CHERCHE (cherche des fournisseurs)",
                SOURCEABLE_PRODUCTS,
                key="match_sourcing",
                help="Sélectionne ce que tu veux sourcer. On cherche les "
                "exposants qui le fabriquent ou le vendent.",
            )
        with c3:
            my_segments = st.multiselect(
                "🛡️ Segments défense prioritaires",
                DEFENSE_SEGMENTS,
                key="match_segments",
                help="Bonus de 15 pts si la société est dans un de ces segments.",
            )

        active = bool(my_offerings or my_sourcing)
        if active:
            tag_offer = " · ".join(my_offerings) if my_offerings else "—"
            tag_source = " · ".join(my_sourcing) if my_sourcing else "—"
            st.markdown(
                f"<div style='background:#0B2E4A; color:white; padding:0.5rem 0.75rem; "
                f"border-radius:4px; font-size:0.85rem;'>"
                f"<b>Profil actif</b> — Vend: <i>{tag_offer}</i> · "
                f"Source: <i>{tag_source}</i></div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption(
                "💡 Sans profil renseigné, le tableau est trié par "
                "**defense_data_strength** (qualité générique de la fiche, indépendante "
                "de l'acheteur)."
            )

        return {
            "my_offerings": my_offerings,
            "my_sourcing": my_sourcing,
            "my_segments": my_segments,
            "active": active,
        }


_FILTER_LABELS: dict[str, str] = {
    "countries": "Pays",
    "defense_segments": "Segment",
    "target_types": "Cible",
    "priority_levels": "Priorité",
    "lead_statuses": "Status",
    "crm_stages": "CRM stage",
    "company_types": "Type",
    "buying_needs": "Besoin",
    "interest_levels": "Intérêt",
    "data_confidences": "Confiance",
    "custom_lists": "Liste",
    "tags": "Tag",
    "products_built_any": "Fabrique",
    "products_sold_any": "Vend",
    "services_sold_any": "Service",
    "buying_needs_any": "Achat",
    "certifications_any": "Cert.",
    "associations_any": "Asso.",
}


def _filter_session_key(field: str) -> str | None:
    """Find the Streamlit session_state key that holds this filter."""
    mapping = {
        "countries": "filter_countries",
        "defense_segments": "filter_segments",
        "target_types": "filter_target_types",
        "priority_levels": "filter_priorities",
        "lead_statuses": "filter_lead_statuses",
        "crm_stages": "filter_crm_stages",
        "company_types": "filter_company_types",
        "buying_needs": "filter_buying_needs",
        "interest_levels": "filter_interest",
        "data_confidences": "filter_confidence",
        "custom_lists": "filter_custom_lists",
        "tags": "filter_tags",
        "products_built_any": "filter_products_built",
        "products_sold_any": "filter_products_sold",
        "services_sold_any": "filter_services_sold",
        "buying_needs_any": "filter_buying_needs_full",
        "certifications_any": "filter_certifications",
        "associations_any": "filter_associations",
    }
    return mapping.get(field)


def render_company_type_chips(filtered_df: pd.DataFrame) -> None:
    """Quick filters above the Companies table — one chip per
    closed-list ``company_type`` bucket (8 buckets).

    Reads the *currently filtered* dataframe so the counts always reflect
    what the user sees. Click-to-filter toggles the bucket in
    ``filter_company_types`` (the same multiselect available in the
    sidebar).
    """
    if "company_type" not in filtered_df.columns or filtered_df.empty:
        return
    counts = filtered_df["company_type"].fillna("Société de services").value_counts()
    # 2 rows of 4 chips → each chip gets ~25 % of the row width which
    # is enough to display the full short label + count on a single
    # line without wrapping on a 1280 px viewport.
    chips: list[tuple[str, str]] = [
        ("OEM", "OEM"),
        ("Intégrateur", "Intégrateur"),
        ("Équipementier / Tier 1", "Équipementier"),
        ("Sous-traitant industriel", "Sous-traitant"),
        ("Distributeur", "Distributeur"),
        ("Éditeur logiciel", "Éditeur logiciel"),
        ("Société de services", "Services"),
        ("Bureau d'ingénierie", "Ingénierie"),
    ]
    # Global button-row tweak : tight padding + nowrap so even narrow
    # column widths don't trigger word-wrap on long labels.
    st.markdown(
        """
        <style>
        .stButton > button {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            padding-left: 0.5rem;
            padding-right: 0.5rem;
            font-size: 0.85rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    cur = set(st.session_state.get("filter_company_types", []) or [])

    def _toggle_chip(canon: str) -> None:
        """Callback : runs BEFORE the next script execution, so the
        sidebar multiselect re-instantiates with the new value cleanly
        (avoids the 'cannot be modified after instantiation' error)."""
        sel = set(st.session_state.get("filter_company_types") or [])
        if canon in sel:
            sel.discard(canon)
        else:
            sel.add(canon)
        st.session_state["filter_company_types"] = sorted(sel)

    def _render_chip(col, canon: str, short: str) -> None:
        n = int(counts.get(canon, 0))
        active = canon in cur
        n_disp = f"{n:,}".replace(",", " ")
        with col:
            st.button(
                f"{short} · {n_disp}",
                key=f"chip_ct_{canon}",
                use_container_width=True,
                type="primary" if active else "secondary",
                help=f"Filtrer la table sur **{canon}** ({n_disp} société·s).",
                on_click=_toggle_chip,
                args=(canon,),
            )

    # Row 1 : OEM / Intégrateur / Équipementier / Sous-traitant
    row1 = st.columns(4, gap="small")
    for col, (canon, short) in zip(row1, chips[:4]):
        _render_chip(col, canon, short)
    # Row 2 : Distributeur / Éditeur logiciel / Services / Ingénierie
    row2 = st.columns(4, gap="small")
    for col, (canon, short) in zip(row2, chips[4:]):
        _render_chip(col, canon, short)


# Backwards-compat alias — some older code paths may still reference the
# old name. Resolves to the new chips renderer.
render_supply_chain_chips = render_company_type_chips


def render_active_filter_chips(filters: dict, filtered_df: pd.DataFrame) -> None:
    """Show currently-active filters as a row of removable chips, plus a
    one-click "Save as list" button on the current selection."""
    chips: list[tuple[str, str, str]] = []  # (field, value, label)
    for field, label in _FILTER_LABELS.items():
        for v in (filters.get(field) or []):
            chips.append((field, v, f"{label}: {v}"))
    bool_extra = []
    if filters.get("only_with_website"):
        bool_extra.append("Avec site web")
    if filters.get("only_high_confidence"):
        bool_extra.append("High confidence")
    if filters.get("only_priority_targets"):
        bool_extra.append("A+ / A only")
    if filters.get("manual_review") is True:
        bool_extra.append("Manual review = TRUE")
    if filters.get("manual_review") is False:
        bool_extra.append("Manual review = FALSE")
    if filters.get("has_parent_group") is True:
        bool_extra.append("Filiale d'un groupe")
    if filters.get("has_parent_group") is False:
        bool_extra.append("Indépendant")
    if filters.get("min_lead_score"):
        bool_extra.append(f"Score ≥ {filters['min_lead_score']}")
    if filters.get("search_text"):
        bool_extra.append(f"Recherche: {filters['search_text']!r}")

    if not chips and not bool_extra:
        return

    st.markdown(
        '<div class="section-title">🔎 Filtres actifs</div>',
        unsafe_allow_html=True,
    )
    chip_cols = st.columns([5, 1.4])
    with chip_cols[0]:
        chip_html = []
        for field, value, label in chips:
            chip_html.append(
                f"<span class='badge' style='background:#0B2E4A;color:white;"
                f"margin-right:0.3rem;margin-bottom:0.3rem;display:inline-block;'>"
                f"{label}</span>"
            )
        for label in bool_extra:
            chip_html.append(
                f"<span class='badge' style='background:#2F5D7C;color:white;"
                f"margin-right:0.3rem;margin-bottom:0.3rem;display:inline-block;'>"
                f"{label}</span>"
            )
        st.markdown(" ".join(chip_html), unsafe_allow_html=True)

        def _rm_from_filter(key: str, value: str) -> None:
            cur = list(st.session_state.get(key) or [])
            if value in cur:
                cur.remove(value)
                st.session_state[key] = cur
                st.cache_data.clear()

        def _reset_all_filters() -> None:
            for k in list(st.session_state.keys()):
                if k.startswith("filter_"):
                    del st.session_state[k]
            st.cache_data.clear()

        with st.expander(f"❌ Retirer un filtre ({len(chips)} multi + {len(bool_extra)} flags)"):
            for field, value, label in chips:
                key = _filter_session_key(field)
                if not key:
                    continue
                st.button(
                    f"× {label}", key=f"chip_rm_{field}_{value}",
                    on_click=_rm_from_filter, args=(key, value),
                )
            st.button(
                "🗑 Reset all filters", key="chip_reset_all",
                on_click=_reset_all_filters,
            )
    with chip_cols[1]:
        st.markdown(f"**{len(filtered_df)} sociétés**")
        with st.popover("💾 Save as list", use_container_width=True):
            st.caption(f"Crée une liste figée des {len(filtered_df)} sociétés actuellement filtrées.")
            list_name = st.text_input(
                "Nom de la liste *",
                key="save_filters_list_name",
                placeholder="ex: Top FR Counter-UAV",
            )
            owner = st.text_input("Owner", key="save_filters_owner")
            if st.button("Créer la liste", key="save_filters_apply",
                         use_container_width=True):
                if not list_name.strip():
                    st.warning("Nom de liste requis.")
                else:
                    cl_id = create_custom_list(
                        name=list_name.strip(), owner=owner or None,
                        criteria=filters,
                    )
                    ids = [
                        int(str(a).replace("ESY26-", ""))
                        for a in filtered_df["account_id"].tolist()
                    ]
                    added = add_to_list(cl_id, ids)
                    st.cache_data.clear()
                    st.success(f"Liste '{list_name}' créée avec {added} sociétés.")
                    st.rerun()


SORT_OPTIONS: dict[str, tuple[str, bool]] = {
    # Removed obsolete options : "Score décroissant (Data strength)",
    # "Score croissant", "Priorité (A+ → D)" — these belonged to the
    # legacy CRM scoring axis that's no longer surfaced to users.
    "Société (A → Z)": ("account_name", True),
    "Pays (A → Z)": ("country", True),
    "Plus récents (last_checked_at)": ("last_checked_at", False),
    "Manual review d'abord": ("manual_review_required", False),
}


def render_table(df: pd.DataFrame, total_rows: int | None = None,
                 key_prefix: str = "companies",
                 extra_columns: list[str] | None = None) -> tuple[int | None, list[int]]:
    """Render the companies table.

    ``key_prefix`` namespaces every Streamlit widget key so the same function
    can be reused on multiple tabs (Companies, Custom List view, …) without
    ``StreamlitDuplicateElementKey`` errors.

    Returns ``(detail_id, bulk_ids)``:
    * ``detail_id`` — exhibitor id when *exactly one* row is selected.
    * ``bulk_ids`` — list of exhibitor ids when *2+ rows* are selected.
    """
    st.markdown(f'<div class="section-title">Companies</div>', unsafe_allow_html=True)

    # Sort + page-size controls
    sort_col, limit_col, _ = st.columns([2, 1, 3])
    with sort_col:
        sort_label = st.selectbox(
            "Trier par",
            list(SORT_OPTIONS.keys()),
            index=0,
            key=f"{key_prefix}_sort",
            label_visibility="collapsed",
        )
    with limit_col:
        page_size = st.selectbox(
            "Lignes",
            [50, 100, 200, 500, 1000, len(df) if len(df) <= 5000 else 5000],
            index=2,
            key=f"{key_prefix}_page_size",
            label_visibility="collapsed",
        )

    if df.empty:
        st.warning("Aucune société ne correspond à vos filtres.")
        # Suggestion: list the active filters and propose to relax the most
        # likely to be over-restrictive. Sliders at full range are hidden
        # because they don't actually filter anything but visually look
        # active (year_range, emp_range).
        _SLIDER_KEYS = {"filter_year_range", "filter_emp_range"}
        active = []
        for k, v in (st.session_state or {}).items():
            if not k.startswith("filter_"):
                continue
            if k in _SLIDER_KEYS:
                # Skip range sliders — they only filter when narrowed
                # below the data extents, and the actual filter logic
                # in apply_filters() already handles that case as a no-op.
                continue
            if isinstance(v, (list, tuple)) and v:
                active.append((k, v))
            elif isinstance(v, str) and v:
                active.append((k, v))
            elif isinstance(v, bool) and v:
                active.append((k, v))
        if active:
            st.markdown("**💡 Suggestions**")
            st.write(
                "Tu as **{}** filtres actifs. Probablement trop restrictif. "
                "Essaie de retirer :".format(len(active))
            )

            def _reset_one_filter(key: str, kind: str) -> None:
                st.session_state[key] = (
                    [] if kind == "list" else
                    "" if kind == "str" else False
                )
                st.cache_data.clear()

            def _reset_filters_keep_cats() -> None:
                _PRESERVE = {
                    "filter_products_built", "filter_products_sold",
                    "filter_services_sold", "filter_targeting_prod_cats",
                    "filter_targeting_svc_cats",
                }
                for k in list(st.session_state.keys()):
                    if k.startswith("filter_") and k not in _PRESERVE:
                        del st.session_state[k]
                st.cache_data.clear()

            for key, value in active[:6]:
                pretty = key.replace("filter_", "")
                preview = (
                    ", ".join(map(str, value)) if isinstance(value, (list, tuple))
                    else str(value)
                )
                kind = (
                    "list" if isinstance(value, (list, tuple)) else
                    "str" if isinstance(value, str) else "bool"
                )
                st.button(
                    f"❌ Retirer {pretty} ({preview[:40]})",
                    key=f"{key_prefix}_empty_remove_{key}",
                    on_click=_reset_one_filter, args=(key, kind),
                )
            st.button(
                "🗑 Reset all filters (sauf Véhicules / Catégorie courante)",
                key=f"{key_prefix}_empty_reset_all",
                help="Garde le filtre principal de catégorie produit / "
                "service que tu viens de cliquer ; supprime tout le reste.",
                on_click=_reset_filters_keep_cats,
            )
        return None, []

    sort_field, ascending = SORT_OPTIONS[sort_label]
    sort_input = df.copy()
    if sort_field == "priority_level":
        # natural priority order: A+ < A < B < C < D
        order = {p: i for i, p in enumerate(["A+", "A", "B", "C", "D"])}
        sort_input["__pri"] = sort_input["priority_level"].map(order).fillna(99)
        sort_input = sort_input.sort_values("__pri", ascending=ascending).drop(columns=["__pri"])
    else:
        sort_input = sort_input.sort_values(
            sort_field, ascending=ascending, na_position="last",
        )
    sort_input = sort_input.head(int(page_size))

    base_cols = list(DEFAULT_TABLE_COLUMNS)
    if extra_columns:
        for c in extra_columns:
            if c in sort_input.columns and c not in base_cols:
                base_cols.append(c)
    show = sort_input[base_cols].copy()
    # Inject is_favorite as the FIRST column so the user can toggle it
    # directly inline on every row. Falls back to False if missing.
    if "is_favorite" in sort_input.columns:
        show.insert(0, "is_favorite",
                    sort_input["is_favorite"].fillna(False).astype(bool))
    else:
        show.insert(0, "is_favorite", False)
    show.insert(0, "_id", sort_input["account_id"].str.replace("ESY26-", "").astype(int))
    # The "_pick" checkbox column was retired — rows are now opened via
    # the "Ouvrir une fiche" selectbox below, and bulk actions / comparison
    # come from the Custom Lists tab where selection is more deliberate.

    # Enriched caption — three counts + sort label
    n_total = total_rows if total_rows is not None else len(df)
    n_filtered = len(df)
    n_shown = len(show)
    st.caption(
        f"**{n_shown} affichées** / {n_filtered} filtrées / {n_total} total · "
        f"trié par **{sort_label}** · "
        f"💡 Coche **⭐** pour ajouter aux favoris."
    )

    # Snapshot to detect ⭐ toggles between renders (data_editor returns
    # the FULL edited frame each rerun).
    snap_key = f"{key_prefix}_snap"
    if (snap_key not in st.session_state
            or len(st.session_state[snap_key]) != len(show)
            or set(st.session_state[snap_key]["_id"]) != set(show["_id"])):
        st.session_state[snap_key] = show.copy()

    edited = st.data_editor(
        show,
        use_container_width=True,
        height=520,
        key=f"{key_prefix}_data_editor",
        column_config={
            "_id": None,
            "is_favorite": st.column_config.CheckboxColumn(
                "⭐", width="small", default=False,
                help="Coche pour ajouter aux favoris (transverse à toutes "
                "les listes). Décoche pour retirer.",
            ),
            "priority_level": st.column_config.TextColumn(
                "Defense fit", width="small",
                help="A+/A/B/C/D — qualité GÉNÉRIQUE de la fiche défense.",
            ),
            "lead_score": st.column_config.ProgressColumn(
                "Data strength", min_value=0, max_value=100, format="%.0f",
                help="Score 0-100 de qualité de la fiche.",
            ),
            "account_name": st.column_config.TextColumn("Société", width="medium"),
            "website_url": st.column_config.LinkColumn(
                "🌐", width="small", display_text=r"https?://(?:www\.)?([^/]+).*",
                help="Lien direct vers le site officiel.",
            ),
            "country": st.column_config.TextColumn("Pays", width="small"),
            "booth_number": st.column_config.TextColumn(
                "Hall / Stand", width="small",
                help="Localisation sur le salon (ex : 'Hall 4 / G325'). "
                "Source : catalogue officiel du salon.",
            ),
            "company_type": st.column_config.TextColumn(
                "Type", width="small",
                help="Type d'entreprise (taxonomie fermée 8 valeurs) :\n"
                "• OEM = vend le système final au client final militaire\n"
                "• Intégrateur = assemble / intègre des sous-systèmes\n"
                "• Équipementier / Tier 1 = sous-systèmes critiques (radars, "
                "optronique, EW, comms)\n"
                "• Sous-traitant industriel = pièces, matériaux, fabrication\n"
                "• Distributeur = revend des marques / composants tiers\n"
                "• Éditeur logiciel = software / SaaS / cyber\n"
                "• Société de services = MCO, formation, conseil, "
                "institutionnel, finance\n"
                "• Bureau d'ingénierie = R&D sur contrat, conseil "
                "technique, laboratoire",
            ),
            "headline": st.column_config.TextColumn(
                "Headline (site officiel)", width="large",
            ),
            "core_business": st.column_config.TextColumn("Cœur de métier", width="medium"),
            # Eurosatory 2026 targeting profile (6 fields + score + categories).
            # Visible in the table by default — that's the value-add the user
            # is selling.
            "activity_1liner": st.column_config.TextColumn(
                "Activité (1 ligne)", width="large",
                help="Une phrase ≤ 140c, verbe d'action en tête, en français — "
                "ce que la société FAIT vraiment, pas du marketing.",
            ),
            "products_specific": st.column_config.TextColumn(
                "Produits", width="large",
                help="Produits concrets (manufactures / édite). "
                "Sépare avec ' · '.",
            ),
            "products_categories": st.column_config.TextColumn(
                "Catégories produits", width="medium",
                help="Catégories canoniques pour le filtrage Excel — "
                "75 buckets (drones, optronique, munitions, …).",
            ),
            "services_specific": st.column_config.TextColumn(
                "Services", width="medium",
                help="Services commerciaux vendus (MCO, formation, "
                "intégration, …).",
            ),
            "services_categories": st.column_config.TextColumn(
                "Catégories services", width="medium",
                help="23 catégories canoniques pour le filtrage Excel.",
            ),
            "target_buyers": st.column_config.TextColumn(
                "Cibles clients", width="medium",
                help="Taxonomie fermée à 5 valeurs : MoD/Armées · Primes "
                "défense · Sécurité civile · Industriels défense · Export.",
            ),
            "technologies_specific": st.column_config.TextColumn(
                "Technologies", width="medium",
                help="Technologies maîtrisées (IA, RF, composites, …).",
            ),
            "technologies_categories": st.column_config.TextColumn(
                "Catégories technos", width="medium",
                help="31 catégories canoniques pour le filtrage Excel.",
            ),
            "why_target": st.column_config.TextColumn(
                "Pourquoi cibler", width="large",
                help="Angle commercial actionnable : acheteur potentiel / "
                "compétiteur / intégrateur / canal de distribution / cible "
                "export.",
            ),
            "targeting_score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%.0f",
                help="Score de complétude de la fiche de ciblage (0-100). "
                "+15 headline · +25 activité · +20 ≥3 produits · +15 cible · "
                "+15 techno · +10 pourquoi cibler.",
            ),
            "targeting_source": st.column_config.TextColumn(
                "Origine", width="small",
                help="manual = fiche enrichie à la main · high/medium/low/"
                "very_low = qualité des données sources rule-based.",
            ),
            "target_type": "Cible",
            "buying_need_main": "Besoin d'achat principal",
            "next_best_action": "Prochaine action",
            "lead_status": "Status",
            "data_confidence": st.column_config.TextColumn("Conf.", width="small"),
            "list_note": st.column_config.TextColumn(
                "Note (liste)", width="medium",
                help="Note rattachée à la paire (société, liste actuelle).",
            ),
        },
        disabled=[
            "_id", "priority_level", "lead_score", "account_name",
            "website_url", "country", "headline", "core_business",
            "target_type", "buying_need_main", "next_best_action",
            "lead_status", "data_confidence", "list_note",
            # Eurosatory 2026 targeting profile (read-only)
            "activity_1liner", "products_specific", "products_categories",
            "services_specific", "services_categories", "target_buyers",
            "technologies_specific", "technologies_categories",
            "why_target", "targeting_score", "targeting_source",
        ],
        hide_index=True,
    )

    # Diff against snapshot to detect ⭐ toggles. Favorites are
    # session-scoped (stored in st.session_state via app.ui.auth) — we
    # don't touch the DB because the Cloud filesystem is read-only.
    # Buyers export their selection to XLSX/CSV at any time for a
    # durable record outside the app.
    from app.ui.auth import set_favorite
    snap = st.session_state[snap_key]
    snap_indexed = snap.set_index("_id")
    edited_indexed = edited.set_index("_id")
    fav_changes: list[tuple[int, bool]] = []
    for eid in edited_indexed.index:
        if eid not in snap_indexed.index:
            continue
        new_fav = bool(edited_indexed.at[eid, "is_favorite"])
        old_fav = bool(snap_indexed.at[eid, "is_favorite"])
        if new_fav != old_fav:
            fav_changes.append((int(eid), new_fav))
    if fav_changes:
        for eid, fav in fav_changes:
            set_favorite(eid, fav)
        st.session_state[snap_key] = edited.copy()
        st.cache_data.clear()
        added_count = sum(1 for _, f in fav_changes if f)
        removed_count = sum(1 for _, f in fav_changes if not f)
        msg_bits = []
        if added_count:
            msg_bits.append(f"⭐ +{added_count} favori(s)")
        if removed_count:
            msg_bits.append(f"★ -{removed_count} retiré(s)")
        st.toast(" / ".join(msg_bits), icon="⭐")
        st.rerun()

    # Detail-opening UI : a selectbox replaces the retired "_pick" column.
    # The user picks a company by name (or types to search) and the fiche
    # opens below.
    name_to_id = {
        f"{row['account_name']} — {row.get('country') or '-'}": int(row["_id"])
        for _, row in show.iterrows()
    }
    detail_choice = st.selectbox(
        "Ouvrir une fiche",
        ["(aucune)"] + sorted(name_to_id.keys()),
        index=0,
        key=f"{key_prefix}_open_detail",
        help="Sélectionne une société pour afficher sa fiche détaillée.",
    )
    detail_id = name_to_id.get(detail_choice)
    return (detail_id, []) if detail_id else (None, [])


# ---------------------------------------------------------------------------
# Side-by-side comparison — shown when 2-4 rows are selected
# ---------------------------------------------------------------------------


_COMPARE_FIELDS: list[tuple[str, str]] = [
    ("country", "Pays"),
    ("priority_level", "Priorité"),
    ("lead_score", "Data strength"),
    ("data_confidence", "Confiance"),
    ("defense_segment_main", "Segment défense"),
    ("core_business", "Cœur de métier"),
    ("business_model", "Business model"),
    ("company_type", "Type"),
    ("company_size", "Taille"),
    ("founding_year", "Founded"),
    ("target_type", "Target type"),
    ("commercial_interest_level", "Intérêt"),
    ("buying_need_main", "Besoin d'achat principal"),
    ("products_built", "Fabrique"),
    ("products_sold", "Vend"),
    ("services_sold", "Services"),
    ("technologies", "Technos"),
    ("certifications", "Certifs"),
    ("industry_associations", "Associations"),
    ("parent_group", "Parent"),
    ("markets_served", "Marchés"),
    ("lead_status", "Status"),
    ("next_best_action", "Next action"),
]


def render_comparison(exhibitor_ids: list[int]) -> None:
    """Side-by-side comparison of 2-4 companies with row-level highlighting."""
    st.markdown(
        f'<div class="section-title">⚖️ Comparaison côte-à-côte — {len(exhibitor_ids)} sociétés</div>',
        unsafe_allow_html=True,
    )
    df = _localize_df(load_crm())
    sub = df[df["account_id"].str.replace("ESY26-", "").astype(int).isin(exhibitor_ids)]
    if sub.empty:
        st.info("Sélection vide.")
        return

    # Build the comparison dataframe: rows = fields, columns = companies
    cols = sub["account_name"].tolist()
    table_rows = []
    differs_flags = []
    for field, label in _COMPARE_FIELDS:
        if field not in sub.columns:
            continue
        raw = sub[field].tolist()
        norm = [str(v) if v is not None else "" for v in raw]
        all_same = len(set(norm)) <= 1
        differs_flags.append(not all_same)
        # Compact long lists
        compact = [
            (v if not isinstance(v, str) or len(v) <= 80
             else v[:78] + "…")
            for v in raw
        ]
        table_rows.append([label] + list(compact))

    headers = ["Champ"] + cols
    comp_df = pd.DataFrame(table_rows, columns=headers)

    # Highlight differing rows in red — column "Champ" stays steel-blue.
    def _row_highlight(row):
        idx = comp_df.index.get_loc(row.name)
        if differs_flags[idx]:
            return ["background-color: #FFEEEE; color: #780000;"] * len(row)
        return [""] * len(row)

    styled = comp_df.style.apply(_row_highlight, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True, height=720)
    st.caption(
        f"🔴 Lignes en rouge : valeurs qui diffèrent entre les sociétés "
        f"({sum(differs_flags)} / {len(differs_flags)} champs)."
    )

    # Quick links + headlines below the table
    st.markdown("**Headlines & liens rapides**")
    for _, row in sub.iterrows():
        head = (row.get("headline") or "—")
        web = row.get("website_url") or ""
        web_md = f" — [site officiel]({web})" if web else ""
        st.markdown(f"- **{row['account_name']}** ({row['country']}) — *{head}*{web_md}")


# ---------------------------------------------------------------------------
# Bulk operations panel — shown when 2+ rows are selected
# ---------------------------------------------------------------------------


def render_bulk_actions(exhibitor_ids: list[int],
                        from_list_id: int | None = None) -> None:
    """Apply a single change to a batch of selected accounts.

    Supports : add tags (one or many), change lead status, assign owner +
    sales team, add to a custom list, mark / unmark "needs review", export
    the selection.

    When called from a list view, ``from_list_id`` enables a 6th action
    "Retirer de la liste" which removes the selected members from that list.
    """
    n = len(exhibitor_ids)
    title = f'⚡ Bulk actions — {n} sociétés sélectionnées'
    st.markdown(f'<div class="section-title">{title}</div>',
                unsafe_allow_html=True)

    # When inside a list view, surface the "Remove from list" action prominently.
    if from_list_id is not None:
        if st.button(f"➖ Retirer ces {n} sociétés de la liste",
                     key=f"bulk_remove_from_list_{from_list_id}",
                     type="primary"):
            removed = remove_from_list(from_list_id, exhibitor_ids)
            st.cache_data.clear()
            st.success(f"{removed} société(s) retirée(s) de la liste.")
            st.rerun()

        # Move / Copy block — only meaningful when we have a SOURCE list.
        with st.expander(
            f"🚚 Déplacer / 📑 Copier vers une autre liste ({n} sociétés)",
            expanded=False,
        ):
            other_lists = [l for l in list_custom_lists() if l.id != from_list_id]
            if not other_lists:
                st.caption("Aucune autre liste disponible. Crée-en une "
                           "depuis l'onglet Companies.")
            else:
                mc1, mc2 = st.columns([3, 2])
                with mc1:
                    target = st.selectbox(
                        "Liste cible",
                        other_lists,
                        format_func=lambda l: f"{l.name} ({_list_member_count(l.id)})",
                        key=f"bulk_movetarget_{from_list_id}",
                    )
                with mc2:
                    bb1, bb2 = st.columns(2)
                    with bb1:
                        if st.button(f"📑 Copier {n}",
                                     key=f"bulk_copy_{from_list_id}",
                                     use_container_width=True,
                                     help="Ajoute la sélection à la liste "
                                     "cible. La source est inchangée."):
                            added = add_to_list(target.id, exhibitor_ids)
                            st.cache_data.clear()
                            st.success(
                                f"📑 {added} société(s) copiée(s) vers "
                                f"« {target.name} »."
                            )
                            st.rerun()
                    with bb2:
                        if st.button(f"🚚 Déplacer {n}",
                                     key=f"bulk_move_{from_list_id}",
                                     use_container_width=True,
                                     type="primary",
                                     help="Ajoute à la liste cible PUIS "
                                     "retire de la liste source. Les notes "
                                     "spécifiques à la source sont perdues."):
                            added = add_to_list(target.id, exhibitor_ids)
                            removed = remove_from_list(from_list_id,
                                                        exhibitor_ids)
                            st.cache_data.clear()
                            st.success(
                                f"🚚 {removed} retirée(s) de la source, "
                                f"{added} ajoutée(s) à « {target.name} »."
                            )
                            st.rerun()

    cols = st.columns(5)

    with cols[0]:
        st.markdown("**🏷️ Ajouter des tags**")
        tags_raw = st.text_input(
            "Tags", key="bulk_tag", label_visibility="collapsed",
            placeholder="drone;A-target;FR-export",
            help="Plusieurs tags d'un coup, séparés par ';' ou ','.",
        )
        if st.button("Appliquer les tags", key="bulk_tag_apply", use_container_width=True):
            tag_list = [t.strip() for t in tags_raw.replace(",", ";").split(";") if t.strip()]
            if not tag_list:
                st.warning("Aucun tag")
            else:
                from app.database import ExhibitorTag, session_scope
                added = 0
                with session_scope() as s:
                    for eid in exhibitor_ids:
                        for tag in tag_list:
                            existing = s.scalar(
                                select(ExhibitorTag).where(
                                    ExhibitorTag.exhibitor_id == eid,
                                    ExhibitorTag.tag == tag,
                                )
                            )
                            if not existing:
                                s.add(ExhibitorTag(exhibitor_id=eid, tag=tag))
                                added += 1
                st.cache_data.clear()
                st.success(f"{added} tags ajoutés sur {n} société(s).")
                st.rerun()

    with cols[1]:
        st.markdown("**📌 Changer le lead status**")
        new_status = st.selectbox(
            "Status", ALLOWED_LEAD_STATUSES, key="bulk_status",
            label_visibility="collapsed",
        )
        if st.button("Appliquer le status", key="bulk_status_apply", use_container_width=True):
            internal_map = {
                "New": "new", "To qualify": "new", "Qualified": "qualified",
                "To contact": "to_contact", "Contacted": "contacted",
                "Meeting requested": "to_contact", "Meeting booked": "contacted",
                "Not relevant": "not_relevant", "Archived": "archived",
            }
            target = internal_map.get(new_status, "new")
            from app.database import session_scope
            updated = 0
            with session_scope() as s:
                for eid in exhibitor_ids:
                    exh = s.get(Exhibitor, eid)
                    if exh is None:
                        continue
                    if exh.status != target:
                        exh.status = target
                        updated += 1
            st.cache_data.clear()
            st.success(f"Status « {new_status} » appliqué sur {updated} société(s).")
            st.rerun()

    with cols[2]:
        st.markdown("**📋 Ajouter à une liste**")
        all_lists = list_custom_lists()
        target_list = st.selectbox(
            "Liste",
            [""] + [l.name for l in all_lists],
            key="bulk_list", label_visibility="collapsed",
        )
        new_list_name = st.text_input(
            "ou créer", key="bulk_new_list",
            label_visibility="collapsed",
            placeholder="Nouveau nom de liste",
        )
        if st.button("Ajouter à la liste", key="bulk_list_apply", use_container_width=True):
            list_id: int | None = None
            if new_list_name.strip():
                list_id = create_custom_list(name=new_list_name.strip())
            elif target_list:
                lst = next((l for l in all_lists if l.name == target_list), None)
                list_id = lst.id if lst else None
            if list_id is None:
                st.warning("Choisis une liste ou saisis un nouveau nom.")
            else:
                added = add_to_list(list_id, exhibitor_ids)
                st.cache_data.clear()
                st.success(f"Ajout de {added} société(s) à la liste.")
                st.rerun()

    with cols[3]:
        st.markdown("**👤 Assigner owner / team**")
        owner_in = st.text_input(
            "Owner", key="bulk_owner", label_visibility="collapsed",
            placeholder="Owner (ex: alice@…)",
        )
        team_in = st.text_input(
            "Sales team", key="bulk_team", label_visibility="collapsed",
            placeholder="Sales team (ex: EMEA)",
        )
        if st.button("Assigner", key="bulk_owner_apply", use_container_width=True):
            if not (owner_in.strip() or team_in.strip()):
                st.warning("Owner ou team requis")
            else:
                from app.database import session_scope
                with session_scope() as s:
                    for eid in exhibitor_ids:
                        exh = s.get(Exhibitor, eid)
                        if exh is None:
                            continue
                        if owner_in.strip():
                            exh.owner = owner_in.strip()
                        if team_in.strip():
                            exh.sales_team = team_in.strip()
                st.cache_data.clear()
                st.success(f"Owner / team mis à jour sur {n} société(s).")
                st.rerun()

    with cols[4]:
        st.markdown("**⚠️ Manual review**")
        if st.button("Marquer 'à vérifier'", key="bulk_review_yes", use_container_width=True):
            from app.database import session_scope
            with session_scope() as s:
                for eid in exhibitor_ids:
                    exh = s.get(Exhibitor, eid)
                    if exh:
                        exh.needs_review = True
            st.cache_data.clear()
            st.success(f"{n} société(s) marquées 'à vérifier'.")
            st.rerun()
        if st.button("Démarquer 'à vérifier'", key="bulk_review_no", use_container_width=True):
            from app.database import session_scope
            with session_scope() as s:
                for eid in exhibitor_ids:
                    exh = s.get(Exhibitor, eid)
                    if exh:
                        exh.needs_review = False
            st.cache_data.clear()
            st.success(f"{n} société(s) démarquées.")
            st.rerun()

        # Bulk favorite toggles — visible everywhere bulk actions render
        # (Companies tab, opened list view, etc).
        if st.button(f"⭐ Marquer {n} en favori",
                     key="bulk_fav_on", use_container_width=True):
            from app.database import session_scope
            starred = 0
            with session_scope() as s:
                for eid in exhibitor_ids:
                    exh = s.get(Exhibitor, eid)
                    if exh and not exh.is_favorite:
                        exh.is_favorite = True
                        starred += 1
            st.cache_data.clear()
            st.success(f"{starred} société(s) ajoutée(s) aux favoris.")
            st.rerun()
        if st.button(f"★ Retirer favori sur {n}",
                     key="bulk_fav_off", use_container_width=True):
            from app.database import session_scope
            unstarred = 0
            with session_scope() as s:
                for eid in exhibitor_ids:
                    exh = s.get(Exhibitor, eid)
                    if exh and exh.is_favorite:
                        exh.is_favorite = False
                        unstarred += 1
            st.cache_data.clear()
            st.success(f"{unstarred} société(s) retirée(s) des favoris.")
            st.rerun()

    # 3 — Export the current selection only (CSV / XLSX)
    st.markdown("**⬇ Export de la sélection**")
    df = _localize_df(load_crm())
    sub = df[df["account_id"].str.replace("ESY26-", "").astype(int).isin(exhibitor_ids)]
    e1, e2, e3 = st.columns([1, 1, 4])
    with e1:
        st.download_button(
            f"⬇ CSV ({n})", data=sub.to_csv(index=False).encode("utf-8"),
            file_name=f"selection_{datetime.utcnow():%Y%m%d_%H%M%S}.csv",
            mime="text/csv", use_container_width=True,
        )
    with e2:
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            sub.to_excel(w, index=False, sheet_name="Selection")
        buf.seek(0)
        st.download_button(
            f"⬇ XLSX ({n})", data=buf,
            file_name=f"selection_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with e3:
        st.caption(
            "Exporte UNIQUEMENT les sociétés cochées (pas tout le filtre). "
            "Pratique pour envoyer une short-list à un commercial."
        )

    # Quick view of selected
    with st.expander(f"📑 Voir les {n} sociétés sélectionnées"):
        from app.database import session_scope
        with session_scope() as s:
            rows = []
            for eid in exhibitor_ids:
                e = s.get(Exhibitor, eid)
                if e:
                    rows.append(
                        f"• **{e.company_name}** ({e.country_iso2 or '—'}) — "
                        f"{e.priority_level or 'D'} — {e.commercial_relevance_score or 0:.0f}/100"
                    )
        st.markdown("\n".join(rows))


# ---------------------------------------------------------------------------
# Similar companies — same primary segment + overlap on built_products
# ---------------------------------------------------------------------------


def _find_similar_companies(session, exh: Exhibitor, intel: ExhibitorIntelligence | None,
                            limit: int = 5) -> list[tuple]:
    if intel is None:
        return []
    from app.processors.sales_card import _pick_primary_category
    primary = _pick_primary_category(intel.defense_categories or [], intel.built_products or [])
    if primary == "Other":
        return []
    my_builts = set(intel.built_products or [])
    candidates = list(session.execute(
        select(ExhibitorIntelligence, Exhibitor)
        .join(Exhibitor, Exhibitor.id == ExhibitorIntelligence.exhibitor_id)
        .where(
            ExhibitorIntelligence.exhibitor_id != exh.id,
            ExhibitorIntelligence.defense_categories.is_not(None),
        )
    ).all())
    scored: list[tuple[float, tuple]] = []
    for cand_intel, cand_exh in candidates:
        cand_primary = _pick_primary_category(
            cand_intel.defense_categories or [], cand_intel.built_products or [],
        )
        if cand_primary != primary:
            continue
        cand_builts = set(cand_intel.built_products or [])
        overlap = len(my_builts & cand_builts)
        # similarity score: anchor match (10) + overlap × 3 + half of cand score
        score = 10 + overlap * 3 + (cand_intel.defense_commercial_score or 0) * 0.05
        scored.append((score, (
            cand_exh.id, cand_exh.company_name, cand_exh.country_iso2 or "—",
            cand_intel.defense_priority_level or "D",
            round(cand_intel.defense_commercial_score or 0, 0),
            (cand_intel.activity_summary or "")[:80],
        )))
    scored.sort(key=lambda t: -t[0])
    return [row for _, row in scored[:limit]]


# ---------------------------------------------------------------------------
# Detail card (6 sections)
# ---------------------------------------------------------------------------


def _log(s, exhibitor_id: int, kind: str, text: str, author: str | None = None) -> None:
    """Append a single audit-log row (status change, tag add, list add, …).

    Same SQLAlchemy session as the caller — no commit.  Caller commits as
    part of the broader unit of work.
    """
    s.add(ActivityLog(
        exhibitor_id=exhibitor_id, kind=kind, text=text, author=author,
    ))


def _push_recently_viewed(exhibitor_id: int, account_name: str | None) -> None:
    rv = list(st.session_state.get("recently_viewed", []))
    rv = [r for r in rv if r["id"] != exhibitor_id]
    rv.insert(0, {"id": exhibitor_id, "name": account_name or f"#{exhibitor_id}"})
    st.session_state["recently_viewed"] = rv[:8]


def render_recently_viewed() -> None:
    rv = st.session_state.get("recently_viewed", [])
    if not rv:
        return
    st.markdown(
        '<div class="section-title">🕘 Vues récentes</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(min(len(rv), 8))
    for i, row in enumerate(rv[:8]):
        with cols[i]:
            if st.button(
                row["name"][:28],
                key=f"rv_{row['id']}",
                use_container_width=True,
                help="Cliquer pour ré-ouvrir cette fiche.",
            ):
                st.session_state["jump_to_id"] = row["id"]
                st.rerun()


def render_detail(exhibitor_id: int) -> None:
    _push_recently_viewed(exhibitor_id, None)  # name resolved below
    s = SessionLocal()
    try:
        exh = s.get(Exhibitor, exhibitor_id)
        intel = s.scalar(
            select(ExhibitorIntelligence).where(
                ExhibitorIntelligence.exhibitor_id == exhibitor_id
            )
        )
        if exh is None:
            st.error("Société introuvable.")
            return
        _push_recently_viewed(exhibitor_id, exh.company_name)

        # Header strip with priority badge
        priority = (intel.defense_priority_level if intel else None) or "D"
        confidence = "—"
        from app.crm.transformer import to_crm
        row = to_crm(exh, intel)
        confidence = row["data_confidence"]

        st.divider()
        # Logo + name + score live in the same row
        logo_url = exh.logo_url
        if logo_url and logo_url.startswith("http"):
            head_cols = st.columns([0.6, 3.4, 1])
            with head_cols[0]:
                # backslashes in finderr URLs break Streamlit's image fetcher
                st.image(logo_url.replace("\\", "/"), width=80)
            col_h1 = head_cols[1]
            col_h2 = head_cols[2]
        else:
            col_h1, col_h2 = st.columns([4, 1])
        with col_h1:
            st.markdown(
                f"### {row['account_name']}  "
                f"&nbsp; {_badge(priority, PRIORITY_BADGE)}  "
                f"{_badge(confidence, CONFIDENCE_BADGE)}",
                unsafe_allow_html=True,
            )
            st.caption(f"{row['country'] or '—'} · {row['city'] or ''} · {row['account_id']}")
            # Headline = the company's own one-liner from their official site.
            # Shown FIRST since it's the most factual piece — no derivation,
            # no LLM, no inference: literally what they say on their homepage.
            headline = (row.get("headline") or "").strip()
            if headline and not headline.startswith("À enrichir"):
                st.markdown(
                    "<div style='border-left: 3px solid #C1121F; padding: 0.45rem 0.75rem; "
                    "background: #FFFFFF; margin-top: 0.5rem; border-radius: 4px; "
                    "font-size: 1rem; color: #071A33; font-weight: 500;'>"
                    f"<span style='font-size:0.7rem; color:#C1121F; text-transform:uppercase; "
                    "letter-spacing:0.08em; display:block; font-weight:600; margin-bottom:0.2rem;'>"
                    "Site officiel</span>"
                    f"{headline}"
                    "</div>",
                    unsafe_allow_html=True,
                )
            # ============================================================
            # 🎯 Profil de ciblage Eurosatory 2026 (the value-add).
            # Pulled from the JSON pipeline output — falls back silently
            # if the file is missing (degrades to no extra block).
            # ============================================================
            tp = _targeting_profile_by_eid().get(int(exhibitor_id))
            if tp:
                score = int(tp.get("completeness_score", 0))
                src = tp.get("data_source_strength") or ""
                # Score colour band — green ≥ 80, yellow 60-79, red < 60.
                if score >= 80:
                    badge_bg = "#1F7A4D"
                elif score >= 60:
                    badge_bg = "#D97706"
                else:
                    badge_bg = "#7B2D2D"
                src_label = (
                    "Manuel" if src == "manual"
                    else f"Rule-based · {src}" if src else "—"
                )
                a1l = (tp.get("activity_1liner") or "").strip()
                products = tp.get("products") or []
                prod_cats = tp.get("products_categories") or []
                services = tp.get("services") or []
                svc_cats = tp.get("services_categories") or []
                technos = tp.get("technologies") or []
                tech_cats = tp.get("technologies_categories") or []
                buyers = tp.get("target_buyers") or []
                why = (tp.get("why_target") or "").strip()

                # Top frame : title + score badge
                st.markdown(
                    "<div style='border:1px solid #E2E8F0; border-radius:6px; "
                    "padding:0.75rem 1rem; margin-top:0.6rem; background:"
                    "#F9FBFE;'>"
                    "<div style='display:flex; align-items:center; "
                    "justify-content:space-between; margin-bottom:0.4rem;'>"
                    "<span style='font-size:0.75rem; color:#0B2E4A; "
                    "font-weight:700; text-transform:uppercase; "
                    "letter-spacing:0.08em;'>"
                    "🎯 Profil de ciblage commercial</span>"
                    f"<span style='display:inline-block; padding:0.1rem 0.55rem; "
                    f"border-radius:10px; background:{badge_bg}; color:white; "
                    f"font-size:0.75rem; font-weight:600;'>"
                    f"Score {score}/100 · {src_label}</span>"
                    "</div>"
                    + (
                        f"<div style='font-size:1rem; font-weight:500; "
                        f"color:#071A33; margin-bottom:0.6rem;'>{a1l}</div>"
                        if a1l else ""
                    )
                    + "</div>",
                    unsafe_allow_html=True,
                )
                # Two-column layout for the structured fields
                tp_l, tp_r = st.columns(2)
                with tp_l:
                    if products:
                        st.markdown(
                            f"**Produits** ({len(products)})  \n"
                            + " · ".join(products)
                        )
                    if prod_cats:
                        st.caption(
                            "Catégories : " + " · ".join(prod_cats)
                        )
                    if services:
                        st.markdown(
                            f"**Services** ({len(services)})  \n"
                            + " · ".join(services)
                        )
                    if svc_cats:
                        st.caption("Catégories : " + " · ".join(svc_cats))
                with tp_r:
                    if buyers:
                        st.markdown(
                            "**Cibles clients**  \n"
                            + " · ".join(
                                f"`{b}`" for b in buyers
                            )
                        )
                    if technos:
                        st.markdown(
                            f"**Technologies** ({len(technos)})  \n"
                            + " · ".join(technos)
                        )
                    if tech_cats:
                        st.caption(
                            "Catégories : " + " · ".join(tech_cats)
                        )
                if why:
                    st.markdown(
                        "<div style='margin-top:0.5rem; padding:0.5rem "
                        "0.75rem; border-left:3px solid #0B2E4A; "
                        "background:#FFFFFF; border-radius:4px; "
                        "font-size:0.95rem;'>"
                        "<span style='font-size:0.7rem; color:#0B2E4A; "
                        "text-transform:uppercase; letter-spacing:0.08em; "
                        "font-weight:600; display:block; "
                        "margin-bottom:0.2rem;'>Pourquoi cibler</span>"
                        + why + "</div>",
                        unsafe_allow_html=True,
                    )

            # Company pitch — shown right under the name, framed as a quote
            pitch_text = (row.get("company_pitch") or "").strip()
            if pitch_text:
                st.markdown(
                    "<div style='border-left: 3px solid #0B2E4A; padding: 0.4rem 0.75rem; "
                    "background: #F4F6F8; margin-top: 0.5rem; border-radius: 4px;'>"
                    + pitch_text.replace("\n", "<br>")
                    + "</div>",
                    unsafe_allow_html=True,
                )
            relevance = (row.get("commercial_relevance_summary") or "").strip()
            if relevance:
                st.caption(f"💡 **Relevance** : {relevance}")

            # In which lists does this company appear ?
            try:
                memberships = lists_for_exhibitor(exh.id)
            except Exception:  # noqa: BLE001
                memberships = []
            if memberships:
                chips = []
                for m in memberships:
                    bg = {
                        "alert": "#C1121F", "warning": "#D97706",
                        "success": "#1F7A4D", "navy": "#0B2E4A",
                    }.get(m["color"] or "", "#2F5D7C")
                    pin = "📌 " if m["is_pinned"] else ""
                    dyn = " 🔄" if m["is_dynamic"] else ""
                    note_marker = " 📝" if m["note"] else ""
                    chips.append(
                        f"<span class='badge' style='background:{bg};"
                        f"color:white;margin-right:0.3rem;'>"
                        f"{pin}{m['name']}{dyn}{note_marker}</span>"
                    )
                st.markdown(
                    "<div style='margin-top:0.4rem;'>"
                    "<span style='font-size:0.72rem;color:#6c757d;"
                    "text-transform:uppercase;letter-spacing:0.06em;"
                    "margin-right:0.4rem;'>📋 Présent dans</span>"
                    + " ".join(chips) + "</div>",
                    unsafe_allow_html=True,
                )
                # Inline note display (one block per list with a note)
                noted = [m for m in memberships if m["note"]]
                if noted:
                    note_html = []
                    for m in noted:
                        note_html.append(
                            f"<div style='margin-top:0.35rem;font-size:0.85rem;"
                            f"border-left:2px solid #D9A05B;padding:0.25rem "
                            f"0.5rem;background:#FFFBF1;'>"
                            f"<strong>{m['name']}</strong> · "
                            f"<span style='color:#2B2F33;'>{m['note']}</span>"
                            f"</div>"
                        )
                    st.markdown("".join(note_html), unsafe_allow_html=True)

            # Attendance signals attached to this exhibitor (reverse bridge).
            try:
                att_signals = attend_signals_for_exhibitor(exh.id, limit=10)
            except Exception:  # noqa: BLE001
                att_signals = []
            if att_signals:
                # Big "confirmed presence" badge for the latest edition we
                # have a signal for (typically 2026).
                latest_year = max(
                    (s.get("edition_year") or 0 for s in att_signals),
                    default=0,
                )
                if latest_year:
                    st.markdown(
                        f"<div style='margin-top:0.5rem;display:inline-block;"
                        f"padding:0.25rem 0.6rem;border-radius:4px;"
                        f"background:#1F7A4D;color:white;font-weight:600;"
                        f"font-size:0.85rem;'>"
                        f"✅ Présence confirmée {latest_year} "
                        f"<span style='font-weight:400;opacity:0.85;'>"
                        f"({len(att_signals)} source(s) OSINT)</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                st.markdown(
                    "<div style='margin-top:0.6rem;'>"
                    "<span style='font-size:0.72rem;color:#6c757d;"
                    "text-transform:uppercase;letter-spacing:0.06em;'>"
                    f"📡 Attendance signals · {len(att_signals)} OSINT</span>"
                    "</div>",
                    unsafe_allow_html=True,
                )
                with st.expander(
                    f"📡 Voir les {len(att_signals)} signaux OSINT liés",
                    expanded=False,
                ):
                    for sig in att_signals:
                        line = (
                            f"**{sig['edition_year']}** · "
                            f"{sig['person_name'] or '(entreprise)'}"
                        )
                        if sig.get("person_role"):
                            line += f" — _{sig['person_role']}_"
                        if sig.get("signal_type"):
                            line += f" · `{sig['signal_type']}`"
                        if sig.get("sales_priority"):
                            line += f" · prio **{sig['sales_priority']}**"
                        st.markdown(line)
                        meta = []
                        if sig.get("source_url"):
                            meta.append(f"[source]({sig['source_url']})")
                        if sig.get("manual_validation_status"):
                            meta.append(sig["manual_validation_status"])
                        if sig.get("first_seen_at"):
                            meta.append(f"vu {sig['first_seen_at']:%Y-%m-%d}")
                        if meta:
                            st.caption(" · ".join(meta))
        with col_h2:
            score = row["lead_score"] or 0
            st.metric("Lead score", f"{score:.0f} / 100", row["next_best_action"])
            # Prominent favorite toggle — sits at the very top so reps don't
            # miss the favorite mechanism. The detail card has another toggle
            # in section 5 ("CRM follow-up"); both write to ``Exhibitor.is_favorite``.
            fav_label = ("⭐ Favori" if not exh.is_favorite
                         else "★ Retirer favori")
            fav_type = "secondary" if not exh.is_favorite else "primary"
            if st.button(fav_label, key=f"fav_top_{exh.id}",
                         use_container_width=True, type=fav_type):
                exh.is_favorite = not bool(exh.is_favorite)
                _log(s, exh.id,
                     "favorite_added" if exh.is_favorite else "favorite_removed",
                     f"Toggled favorite (top button) → {exh.is_favorite}")
                s.commit()
                st.cache_data.clear()
                st.rerun()
            if not _PDF_AVAILABLE or render_company_pdf is None:
                st.caption("📄 Export PDF indisponible (fpdf2 non installé).")
            else:
                try:
                    pdf_bytes = render_company_pdf(row)
                    st.download_button(
                        "📄 Export PDF",
                        data=pdf_bytes,
                        file_name=f"{(row.get('account_name') or 'fiche').replace(' ', '_')[:60]}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                        key=f"pdf_{exh.id}",
                    )
                except Exception as e:  # noqa: BLE001
                    st.caption(f"PDF indisponible: {e}")

        # Section 1 — Identity
        st.markdown('<div class="section-title">1 · Identity</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            web = row["website_url"]
            if web:
                st.markdown(f"**Website** : [{web}]({web})")
            else:
                st.markdown("**Website** : —")
            li = row["linkedin_company_url"]
            if li:
                st.markdown(f"**LinkedIn** : [{li}]({li})")
            else:
                st.markdown("**LinkedIn** : —")
            es = row["eurosatory_profile_url"]
            if es:
                st.markdown(f"**Profil officiel salon** : [voir la fiche]({es})")
            else:
                st.markdown("**Profil officiel salon** : —")
        with c2:
            st.markdown(f"**Booth** : {row['booth_number'] or '—'}")
            st.markdown(f"**Company type** : {row['company_type']}")
            st.markdown(f"**Business model** : {row['business_model']}")
        with c3:
            st.markdown(f"**Company size** : {row['company_size'] or '—'}")
            fy = row.get("founding_year")
            if fy:
                st.markdown(f"**Founded** : {int(fy)}")
            st.markdown(f"**Last checked** : {row['last_checked_at'] or '—'}")

        # Contacts officiels (catalogue Eurosatory) — generic emails only
        contacts = list(s.execute(
            select(ExhibitorContact).where(ExhibitorContact.exhibitor_id == exh.id)
        ).scalars())
        if contacts:
            st.markdown("**📞 Contacts officiels (catalogue salon)**")
            for c in contacts[:6]:
                # only display generic emails / corporate phones — keep
                # personal-name + role only when both are explicit (the
                # operator wanted "no personal data unless professional").
                name_part = (c.full_name or "").strip()
                fn_part = (c.function or "").strip()
                if fn_part.lower() == "not available language":
                    fn_part = ""
                identity = (
                    f"{name_part} — {fn_part}".strip(" —")
                    if (name_part or fn_part) else "(contact générique)"
                )
                bits: list[str] = []
                if c.email:
                    # 3-way badge :
                    #   📧            — directly sourced from catalogue / scraping
                    #   📧 (générique) — info@ / contact@ / sales@
                    #   ⚠️ (inféré)    — pattern-inferred, MX-validated, NOT verified
                    src = (c.source_url or "").lower()
                    is_inferred = src.startswith("inferred:")
                    if is_inferred:
                        badge = "⚠️ (inféré · à vérifier)"
                    elif c.is_generic:
                        badge = "📧 (générique)"
                    else:
                        badge = "📧"
                    bits.append(f"{badge} `{c.email}`")
                if c.phone:
                    bits.append(f"☎️ `{c.phone}`")
                if c.linkedin:
                    bits.append(f"[🔗 LinkedIn]({c.linkedin})")
                bits_str = "  ·  ".join(bits) if bits else "—"
                st.markdown(f"- **{identity}** — {bits_str}")

        # Section 2 — What they do
        st.markdown('<div class="section-title">2 · What they do</div>', unsafe_allow_html=True)
        st.markdown(f"**Cœur de métier** : {row.get('core_business') or '—'}")
        st.markdown(f"**Synthèse produits/services** : {row.get('main_products_services') or '—'}")
        st.write(row["description_short"])

        # Public PDFs (brochures, datasheets) — direct download links for the
        # sales rep. Sourced from the deep-crawler audit log.
        pdf_pages = list(s.execute(
            select(CrawledPage)
            .where(
                CrawledPage.exhibitor_id == exh.id,
                CrawledPage.kind == "pdf",
                CrawledPage.url.is_not(None),
            )
            .order_by(CrawledPage.id)
        ).scalars())
        if pdf_pages:
            st.markdown("**📄 Brochures & catalogues publics**")
            for p in pdf_pages[:8]:
                # display the file name from the URL
                from urllib.parse import unquote, urlparse
                fname = unquote(urlparse(p.url).path.rsplit("/", 1)[-1] or p.url)
                st.markdown(f"- [{fname}]({p.url})")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Products built** : {row['products_built'] or '—'}")
            st.markdown(f"**Products sold** : {row['products_sold'] or '—'}")
            st.markdown(f"**Services sold** : {row['services_sold'] or '—'}")
        with c2:
            st.markdown(f"**Technologies** : {row['technologies'] or '—'}")
            st.markdown(f"**Target clients** : {row['target_clients'] or '—'}")
            st.markdown(f"**Markets served** : {row['markets_served'] or '—'}")

        certs = (row.get("certifications") or "").strip()
        if certs:
            badges = " ".join(
                f"<span class='badge' style='background:#0B2E4A;color:white;"
                f"margin-right:0.3rem;'>🏅 {c.strip()}</span>"
                for c in certs.split(";") if c.strip()
            )
            st.markdown(f"**Certifications** {badges}", unsafe_allow_html=True)

        assocs = (row.get("industry_associations") or "").strip()
        if assocs:
            badges = " ".join(
                f"<span class='badge' style='background:#2F5D7C;color:white;"
                f"margin-right:0.3rem;'>🤝 {a.strip()}</span>"
                for a in assocs.split(";") if a.strip()
            )
            st.markdown(f"**Industry associations** {badges}", unsafe_allow_html=True)

        parent = (row.get("parent_group") or "").strip()
        offices = (row.get("additional_offices") or "").strip()
        if parent or offices:
            c_p, c_o = st.columns(2)
            with c_p:
                if parent:
                    st.markdown(f"🏢 **Parent group** : {parent}")
            with c_o:
                if offices:
                    st.markdown(f"📍 **Other offices** : {offices}")

        # Section 3 — Why they matter
        st.markdown('<div class="section-title">3 · Why they matter</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Defense segment** : `{row['defense_segment_main']}`")
            st.markdown(f"**Secondary segments** : {row['defense_segments_secondary'] or '—'}")
            st.markdown(f"**Target type** : **{row['target_type']}**")
            st.markdown(f"**All target types** : {row['all_target_types']}")
        with c2:
            st.markdown(f"**Main buying need** : **{row['buying_need_main']}**")
            st.markdown(f"**Buying confidence** : {row['buying_need_confidence']}")
            st.markdown(f"**Other buying needs** : {row['buying_needs_secondary'] or '—'}")
            st.markdown(
                f"**Opportunities** :  "
                f"supplier {row['supplier_opportunity']} · "
                f"partner {row['partnership_opportunity']} · "
                f"integrator {row['integration_opportunity']} · "
                f"distributor {row['distribution_opportunity']}"
            )

        # Score breakdown — visualise the 7 components of the defense score
        if intel and intel.defense_score_breakdown:
            comps = (intel.defense_score_breakdown or {}).get("components") or {}
            if comps:
                st.markdown("**📈 Defense fit — décomposition du score**")
                COMPONENT_MAX = {
                    "defense_fit": 20, "offering_clarity": 15,
                    "supplier_buying_potential": 20, "partnership_potential": 15,
                    "size_maturity": 10, "international": 10,
                    "data_completeness": 10,
                }
                COMPONENT_LABEL = {
                    "defense_fit": "Adéquation défense",
                    "offering_clarity": "Clarté produits/services",
                    "supplier_buying_potential": "Potentiel d'achat fournisseur",
                    "partnership_potential": "Potentiel de partenariat",
                    "size_maturity": "Taille / maturité",
                    "international": "Présence internationale",
                    "data_completeness": "Qualité de la donnée",
                }
                # Build a small dataframe and feed it to st.bar_chart in a way
                # that shows progress against the cap of each component.
                breakdown_rows = []
                for comp, points in comps.items():
                    cap = COMPONENT_MAX.get(comp, 100)
                    label = COMPONENT_LABEL.get(comp, comp)
                    pct = round((points / cap) * 100, 1) if cap else 0
                    breakdown_rows.append({
                        "Composante": label,
                        "Points": float(points or 0),
                        "Max": cap,
                        "%": pct,
                    })
                bdf = pd.DataFrame(breakdown_rows)
                st.dataframe(
                    bdf, hide_index=True, use_container_width=True,
                    column_config={
                        "Composante": st.column_config.TextColumn(width="medium"),
                        "Points": st.column_config.ProgressColumn(
                            "Points", min_value=0, max_value=20, format="%.1f"),
                        "Max": st.column_config.NumberColumn(width="small"),
                        "%": st.column_config.ProgressColumn(
                            "% cap", min_value=0, max_value=100, format="%.0f%%"),
                    },
                )
                explanation = (intel.defense_score_breakdown or {}).get("explanation")
                if explanation:
                    st.caption(f"💬 {explanation}")

        # Section 4 — Sales approach
        st.markdown('<div class="section-title">4 · Sales approach</div>', unsafe_allow_html=True)
        st.markdown(f"**Ideal seller profile** : {row['ideal_seller_profile']}")
        st.markdown(f"**Sales angle** : {row['recommended_sales_angle']}")
        if row["short_pitch"]:
            st.markdown("**Short pitch :**")
            st.code(row["short_pitch"], language=None)
        if row["objections_probables"]:
            with st.expander("Objections probables"):
                for o in row["objections_probables"].split(";"):
                    st.write(f"• {o.strip()}")
        if row["prospecting_keywords"]:
            st.caption(f"**Keywords prospection** : {row['prospecting_keywords']}")
        st.info(f"**Next best action** : {row['next_best_action']}")

        # Outreach email — generate a mailto: link with subject + body filled
        from urllib.parse import quote
        recipient = (
            row.get("contact_email") or row.get("generic_sales_email") or ""
        )
        company = row["account_name"]
        seg = row.get("defense_segment_main") or "défense"
        pitch = (intel.recommended_pitch if intel else "") or ""
        seller = row.get("ideal_seller_profile") or ""
        subject = f"{company} — opportunités {seg}"
        body_lines = [
            f"Bonjour,",
            "",
            f"Je vous contacte dans le cadre du salon défense 2026.",
            "",
            (pitch[:500] + ("…" if len(pitch) > 500 else "")) if pitch else "",
            "",
            f"Profil vendeur idéal pour vous : {seller}" if seller else "",
            "",
            "Seriez-vous disponible pour 20 minutes durant le salon ?",
            "",
            "Cordialement,",
        ]
        body = "\n".join(l for l in body_lines if l is not None)
        mailto = f"mailto:{recipient}?subject={quote(subject)}&body={quote(body)}"
        em1, em2 = st.columns([1, 4])
        with em1:
            st.markdown(
                f"<a href='{mailto}' target='_blank' "
                f"style='display:inline-block;background:#0B2E4A;color:white;"
                f"padding:0.5rem 1rem;border-radius:4px;text-decoration:none;"
                f"font-weight:600;'>✉️ Draft email</a>",
                unsafe_allow_html=True,
            )
        with em2:
            st.caption(
                f"Ouvre {('un mailto: vers ' + recipient) if recipient else 'un mailto: vide'} "
                "avec sujet + corps pré-remplis (pitch + ideal seller). "
                "Le commercial copie/colle dans son client mail si besoin."
            )
        with st.expander("📋 Voir le brouillon (copier-coller)"):
            st.code(f"À : {recipient or '—'}\nSujet : {subject}\n\n{body}",
                    language=None)

        # Section 5 — CRM follow-up (editable)
        st.markdown('<div class="section-title">5 · CRM follow-up</div>', unsafe_allow_html=True)
        # Star toggle — sits at the very top of the CRM section, alongside
        # the "mark contacted" quick action.
        qa_fav, qa_date, qa_btn = st.columns([1, 2, 2])
        with qa_fav:
            star_label = "⭐ Favori" if not exh.is_favorite else "★ Retirer favori"
            if st.button(star_label, key=f"fav_{exh.id}", use_container_width=True):
                exh.is_favorite = not bool(exh.is_favorite)
                _log(s, exh.id,
                     "favorite_added" if exh.is_favorite else "favorite_removed",
                     f"Toggled favorite → {exh.is_favorite}")
                s.commit()
                st.cache_data.clear()
                st.rerun()
        with qa_date:
            from datetime import date as _date, datetime as _dt
            existing_date = (
                exh.next_action_date.date()
                if exh.next_action_date else None
            )
            new_date = st.date_input(
                "📅 Next action date",
                value=existing_date,
                key=f"next_action_{exh.id}",
                help="Date de la prochaine action commerciale prévue.",
            )
            new_dt = _dt.combine(new_date, _dt.min.time()) if new_date else None
            if (new_dt or None) != exh.next_action_date:
                old = exh.next_action_date
                exh.next_action_date = new_dt
                _log(s, exh.id, "next_action_set",
                     f"Next action date: {old} → {new_dt}")
                s.commit()
                st.cache_data.clear()

        # Quick action: mark as contacted today
        qa1, qa2 = st.columns([1, 5])
        with qa1:
            if st.button("✅ Contacté aujourd'hui", key=f"mark_contacted_{exh.id}",
                         use_container_width=True, type="primary"):
                from datetime import date, datetime as _dt
                old = exh.status
                exh.status = "contacted"
                exh.last_contact_date = _dt.utcnow()
                _log(s, exh.id, "mark_contacted",
                     f"Marqué comme contacté ({date.today():%Y-%m-%d}) — était '{old}'")
                s.commit()
                st.cache_data.clear()
                st.rerun()
        with qa2:
            st.caption(
                "Bouton rapide : passe le statut à 'Contacted' + ajoute une "
                "note datée. Les autres champs restent éditables ci-dessous."
            )

        c1, c2, c3 = st.columns(3)
        with c1:
            new_status = st.selectbox(
                "Lead status", ALLOWED_LEAD_STATUSES,
                index=ALLOWED_LEAD_STATUSES.index(row["lead_status"])
                if row["lead_status"] in ALLOWED_LEAD_STATUSES else 0,
                key=f"status_{exh.id}",
            )
            internal_map = {
                "New": "new", "To qualify": "new", "Qualified": "qualified",
                "To contact": "to_contact", "Contacted": "contacted",
                "Meeting requested": "to_contact", "Meeting booked": "contacted",
                "Not relevant": "not_relevant", "Archived": "archived",
            }
            new_internal = internal_map.get(new_status, "new")
            if exh.status != new_internal:
                old = exh.status
                exh.status = new_internal
                _log(s, exh.id, "status_change",
                     f"Status: '{old}' → '{new_internal}'")
                s.commit()
                st.cache_data.clear()
                st.success("Status mis à jour.")
        with c2:
            new_owner = st.text_input("Owner", value=row["owner"] or "", key=f"owner_{exh.id}")
            new_team = st.text_input("Sales team", value=row["sales_team"] or "", key=f"team_{exh.id}")
            if (new_owner or None) != exh.owner or (new_team or None) != exh.sales_team:
                old_o, old_t = exh.owner, exh.sales_team
                exh.owner = new_owner or None
                exh.sales_team = new_team or None
                _log(s, exh.id, "owner_changed",
                     f"Owner: {old_o!r} → {exh.owner!r}; team: {old_t!r} → {exh.sales_team!r}")
                s.commit()
                st.cache_data.clear()
        with c3:
            new_review = st.checkbox(
                "Manual review required",
                value=bool(row["manual_review_required"]),
                key=f"review_{exh.id}",
            )
            if new_review != bool(exh.needs_review):
                exh.needs_review = new_review
                _log(s, exh.id,
                     "review_flag" if new_review else "review_unflag",
                     f"Manual review = {new_review}")
                s.commit()

        # Tags
        st.markdown("**Tags**")
        tag_col1, tag_col2 = st.columns([3, 1])
        existing_tags = sorted({
            t.tag for t in s.execute(
                select(ExhibitorTag).where(ExhibitorTag.exhibitor_id == exh.id)
            ).scalars()
        })
        with tag_col1:
            st.write(" · ".join(existing_tags) if existing_tags else "_(aucun)_")
        with tag_col2:
            new_tag = st.text_input(" ", placeholder="Nouveau tag", label_visibility="collapsed",
                                    key=f"new_tag_{exh.id}")
            if st.button("Ajouter tag", key=f"add_tag_{exh.id}") and new_tag:
                existing = s.scalar(
                    select(ExhibitorTag).where(
                        ExhibitorTag.exhibitor_id == exh.id, ExhibitorTag.tag == new_tag
                    )
                )
                if not existing:
                    s.add(ExhibitorTag(exhibitor_id=exh.id, tag=new_tag))
                    s.commit()
                    st.cache_data.clear()
                    st.rerun()

        # Custom list assignment
        st.markdown("**Custom lists**")
        all_lists = list_custom_lists()
        member_list_ids = {
            cm.list_id for cm in s.execute(
                select(CustomListMember).where(CustomListMember.exhibitor_id == exh.id)
            ).scalars()
        }
        if all_lists:
            picked = st.multiselect(
                "Listes",
                options=[l.name for l in all_lists],
                default=[l.name for l in all_lists if l.id in member_list_ids],
                key=f"lists_{exh.id}",
                label_visibility="collapsed",
            )
            picked_ids = {l.id for l in all_lists if l.name in picked}
            to_add = picked_ids - member_list_ids
            to_remove = member_list_ids - picked_ids
            for lid in to_add:
                add_to_list(lid, [exh.id])
            for lid in to_remove:
                remove_from_list(lid, [exh.id])
            if to_add or to_remove:
                st.cache_data.clear()
        else:
            st.caption("(Aucune liste créée — onglet **Custom lists** pour en créer une)")

        # Notes
        st.markdown("**Notes commerciales**")
        with st.form(key=f"note_form_{exh.id}", clear_on_submit=True):
            new_note = st.text_area("Ajouter une note", key=f"note_text_{exh.id}",
                                    label_visibility="collapsed")
            note_author = st.text_input("Auteur", key=f"note_author_{exh.id}")
            if st.form_submit_button("Enregistrer la note") and new_note:
                s.add(CommercialNote(
                    exhibitor_id=exh.id, note=new_note, author=note_author or None
                ))
                s.commit()
                st.cache_data.clear()
                st.rerun()
        notes = list(s.execute(
            select(CommercialNote)
            .where(CommercialNote.exhibitor_id == exh.id)
            .order_by(CommercialNote.created_at.desc())
        ).scalars())
        # Activity log entries (status changes, owner changes, mark-contacted, …)
        logs = list(s.execute(
            select(ActivityLog)
            .where(ActivityLog.exhibitor_id == exh.id)
            .order_by(ActivityLog.created_at.desc())
        ).scalars())

        # Merge into a single chronological timeline
        events = []
        for n in notes:
            events.append((n.created_at, "note", n.author or "anon", n.note))
        for l in logs:
            events.append((l.created_at, l.kind, l.author or "system", l.text))
        events.sort(key=lambda e: e[0], reverse=True)

        if events:
            ICON = {
                "note": "📝", "status_change": "🔁", "tag_added": "🏷️",
                "tag_removed": "🗑", "list_added": "📋", "list_removed": "📋",
                "mark_contacted": "✅", "review_flag": "⚠️",
                "review_unflag": "✓", "owner_changed": "👤",
            }
            with st.expander(f"📜 Timeline d'activité ({len(events)})", expanded=False):
                for ts, kind, author, text_ in events[:40]:
                    icon = ICON.get(kind, "·")
                    st.markdown(
                        f"<div style='border-left:2px solid #2F5D7C; padding:0.2rem 0.6rem; "
                        f"margin-bottom:0.3rem;'>"
                        f"<span style='font-size:0.75rem; color:#4A5158;'>"
                        f"{ts:%Y-%m-%d %H:%M}</span> "
                        f"<span style='margin-left:0.5rem;'>{icon}</span> "
                        f"<b style='color:#0B2E4A;'>{kind}</b> · "
                        f"<i>{author}</i><br>"
                        f"<span style='font-size:0.9rem;'>{text_}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
        else:
            for n in notes[:5]:
                st.markdown(f"> _{n.created_at:%Y-%m-%d %H:%M}_ — **{n.author or 'anon'}** — {n.note}")

        # News / press from the deep crawler audit log
        news_pages = list(s.execute(
            select(CrawledPage)
            .where(
                CrawledPage.exhibitor_id == exh.id,
                CrawledPage.kind == "news",
                CrawledPage.url.is_not(None),
            )
            .order_by(CrawledPage.id.desc())
            .limit(5)
        ).scalars())
        if news_pages:
            st.markdown('<div class="section-title">📰 News / press (extraits du crawl)</div>',
                        unsafe_allow_html=True)
            for p in news_pages:
                from urllib.parse import unquote
                label = unquote((p.url or "").rsplit("/", 1)[-1] or p.url)[:90]
                st.markdown(f"- [{label}]({p.url})")

        # Similar companies — same primary defense segment + overlap on built_products
        similar = _find_similar_companies(s, exh, intel)
        if similar:
            st.markdown('<div class="section-title">🔁 Sociétés similaires</div>',
                        unsafe_allow_html=True)
            sim_df = pd.DataFrame(similar, columns=["id", "Société", "Pays", "Prio", "Score", "Cœur de métier"])
            st.dataframe(sim_df, hide_index=True, use_container_width=True,
                         column_config={"id": None})

        # Section 6 — Data quality
        st.markdown('<div class="section-title">6 · Data quality</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Data confidence** : {_badge(row['data_confidence'], CONFIDENCE_BADGE)}",
                        unsafe_allow_html=True)
            st.markdown(f"**Source confidence** : {row['source_confidence']}")
            st.markdown(f"**Extraction method** : `{row['extraction_method']}`")
            st.markdown(f"**Manual review** : {'Yes' if row['manual_review_required'] else 'No'}")
        with c2:
            st.markdown(f"**Fields to verify** : {row['fields_to_verify'] or '(none)'}")
            st.markdown(f"**Missing critical** : {row['missing_critical_fields'] or '(none)'}")
        if row["source_urls"]:
            with st.expander("Source URLs (extraits)"):
                for u in row["source_urls"].split(" | "):
                    st.write(f"- {u}")
        with st.expander("Pages crawlées (audit)"):
            pages = list(s.execute(
                select(CrawledPage)
                .where(CrawledPage.exhibitor_id == exh.id)
                .order_by(CrawledPage.id)
            ).scalars())
            for p in pages:
                st.write(
                    f"- `{p.kind}` [{p.status_code}] {p.url}"
                    + (f" — {p.error}" if p.error else "")
                )
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Custom lists tab
# ---------------------------------------------------------------------------


def _list_member_count(list_id: int) -> int:
    """Cheap count of members for one list."""
    s = SessionLocal()
    try:
        from sqlalchemy import func as _f
        return s.scalar(
            select(_f.count(CustomListMember.id))
            .where(CustomListMember.list_id == list_id)
        ) or 0
    finally:
        s.close()


def _list_inline_stats(list_id: int, full_df: pd.DataFrame) -> dict:
    """Compute summary stats for one list using the cached CRM dataframe.

    Returns ``{count, contacted, meetings, avg_score, overdue}``.
    """
    member_set = set(list_member_ids(list_id))
    if not member_set:
        return {"count": 0, "contacted": 0, "meetings": 0,
                "avg_score": 0.0, "overdue": 0}
    sub = full_df[
        full_df["account_id"].str.replace("ESY26-", "").astype(int).isin(member_set)
    ]
    if sub.empty:
        return {"count": 0, "contacted": 0, "meetings": 0,
                "avg_score": 0.0, "overdue": 0}

    contacted = int((sub["lead_status"] == "Contacted").sum())
    # "Meeting requested" + "Meeting booked" both surface as "Contacted" in
    # our lead_status mapping, so we approximate meetings via the next_action_date
    # being set in the future (booked) or already happened (kept).
    meetings = 0
    overdue = 0
    if "next_action_date" in sub.columns:
        from datetime import datetime as _dt
        nad = pd.to_datetime(sub["next_action_date"], errors="coerce")
        meetings = int(nad.notna().sum())
        overdue = int(((nad.notna()) & (nad < _dt.utcnow())).sum())
    avg_score = round(float(sub["lead_score"].dropna().mean() or 0), 1)
    return {
        "count": len(sub),
        "contacted": contacted,
        "meetings": meetings,
        "avg_score": avg_score,
        "overdue": overdue,
    }


def _push_filters_into_session(criteria: dict | None) -> None:
    """Push a saved filter set back into the Companies-tab session keys.

    Maps the criteria_json keys (set by render_sidebar) to the actual
    ``filter_*`` session_state keys.
    """
    if not criteria:
        return
    KEY_MAP = {
        "countries": "filter_countries",
        "defense_segments": "filter_segments",
        "target_types": "filter_target_types",
        "priority_levels": "filter_priorities",
        "lead_statuses": "filter_lead_statuses",
        "crm_stages": "filter_crm_stages",
        "company_types": "filter_company_types",
        "buying_needs": "filter_buying_needs",
        "interest_levels": "filter_interest",
        "data_confidences": "filter_confidence",
        "custom_lists": "filter_custom_lists",
        "tags": "filter_tags",
        "products_built_any": "filter_products_built",
        "products_sold_any": "filter_products_sold",
        "services_sold_any": "filter_services_sold",
        "buying_needs_any": "filter_buying_needs_full",
        "certifications_any": "filter_certifications",
        "associations_any": "filter_associations",
        "halls_any": "filter_halls",
        "search_text": "filter_search",
        "min_lead_score": "filter_min_score",
        "only_with_website": "filter_only_website",
        "only_high_confidence": "filter_only_high_conf",
        "only_priority_targets": "filter_only_priority",
        "only_favorites": "filter_only_favorites",
        "next_action_filter": "filter_next_action",
    }
    for ckey, val in criteria.items():
        skey = KEY_MAP.get(ckey)
        if skey:
            st.session_state[skey] = val


def _list_analytics(members_df: pd.DataFrame) -> None:
    """Small dashboard for one list's members."""
    if members_df.empty:
        st.info("Liste vide.")
        return

    n = len(members_df)
    n_a = int(members_df["priority_level"].isin(["A+", "A"]).sum())
    n_high_conf = int((members_df["data_confidence"] == "High").sum())
    n_contacted = int((members_df["lead_status"] == "Contacted").sum())
    avg_score = round(float(members_df["lead_score"].dropna().mean() or 0), 1)
    n_overdue = 0
    if "next_action_date" in members_df.columns:
        from datetime import datetime as _dt
        nad = pd.to_datetime(members_df["next_action_date"], errors="coerce")
        n_overdue = int(((nad.notna()) & (nad < _dt.utcnow())).sum())

    cols = st.columns(6)
    with cols[0]: _kpi("Sociétés", n)
    with cols[1]: _kpi("Priorité A / A+", n_a, css_class="alert")
    with cols[2]: _kpi("High confidence", n_high_conf, css_class="success")
    with cols[3]: _kpi("Contacted", n_contacted)
    with cols[4]: _kpi("Overdue", n_overdue, css_class="warning")
    with cols[5]: _kpi("Avg score", avg_score)

    # Status mix + Country / Segment top 5
    a, b, c = st.columns(3)
    with a:
        st.markdown("**Mix lead status**")
        ls = members_df["lead_status"].value_counts().to_dict()
        if ls:
            st.bar_chart(pd.DataFrame(list(ls.items()), columns=["Status", "Count"])
                         .set_index("Status"), height=220)
    with b:
        st.markdown("**Top 5 pays**")
        cc = members_df["country"].value_counts().head(5).to_dict()
        if cc:
            st.bar_chart(pd.DataFrame(list(cc.items()), columns=["Pays", "Count"])
                         .set_index("Pays"), height=220)
    with c:
        st.markdown("**Top 5 segments défense**")
        sg = members_df["defense_segment_main"].value_counts().head(5).to_dict()
        if sg:
            st.bar_chart(pd.DataFrame(list(sg.items()), columns=["Segment", "Count"])
                         .set_index("Segment"), height=220)


def _render_list_edit_form(list_obj) -> None:
    """Inline form to rename a list / change owner / sales_team / color."""
    with st.expander("✏️ Éditer les métadonnées de cette liste"):
        with st.form(f"edit_list_{list_obj.id}"):
            new_name = st.text_input("Nom *", value=list_obj.name or "")
            new_desc = st.text_area(
                "Description", value=list_obj.description or "", height=70,
            )
            cc1, cc2, cc3 = st.columns(3)
            with cc1:
                new_owner = st.text_input("Owner", value=list_obj.owner or "")
            with cc2:
                new_team = st.text_input("Sales team", value=list_obj.sales_team or "")
            with cc3:
                colors = ["", "navy", "alert", "warning", "success"]
                new_color = st.selectbox(
                    "Couleur",
                    colors,
                    index=colors.index(list_obj.color) if list_obj.color in colors else 0,
                )
            has_criteria = bool(list_obj.criteria_json)
            new_dynamic = st.checkbox(
                "🔄 Liste dynamique — resync auto avec les critères sauvegardés",
                value=bool(getattr(list_obj, "is_dynamic", False)),
                disabled=not has_criteria,
                help=(
                    "Quand activé, l'ouverture de la liste recalcule les "
                    "membres à partir des critères : ajoute les nouveaux "
                    "matchs, retire ceux qui ne matchent plus. Les notes "
                    "des sociétés conservées sont préservées."
                ) if has_criteria else (
                    "Indisponible : cette liste n'a pas de critères "
                    "sauvegardés. Recrée-la depuis l'onglet Companies via "
                    "« 💾 Save as list » avec des filtres actifs."
                ),
            )
            if st.form_submit_button("💾 Enregistrer", type="primary"):
                if not new_name.strip():
                    st.warning("Nom requis")
                else:
                    update_custom_list(
                        list_obj.id,
                        name=new_name.strip(),
                        description=new_desc.strip(),
                        owner=new_owner.strip(),
                        sales_team=new_team.strip(),
                        color=new_color,
                        is_dynamic=new_dynamic,
                    )
                    st.cache_data.clear()
                    st.success("Métadonnées mises à jour.")
                    st.rerun()


_KIND_ICONS = {
    "status_change": "🔁", "tag_added": "🏷️", "tag_removed": "🏷️",
    "list_added": "📂", "list_removed": "📂",
    "note": "📝", "mark_contacted": "📞",
    "review_flag": "⚠️", "review_unflag": "✓",
    "owner_changed": "👤",
    "favorite_added": "⭐", "favorite_removed": "★",
    "next_action_set": "📅",
    "company_call_logged": "📞", "company_email_logged": "✉",
}


_FUNNEL_STAGES: list[tuple[str, str]] = [
    ("New", "Identified — pas encore qualifié"),
    ("To qualify", "À qualifier"),
    ("Qualified", "Qualifié — match commercial confirmé"),
    ("To contact", "Prêt à approcher"),
    ("Contacted", "Contacté"),
    ("Meeting requested", "RDV demandé"),
    ("Meeting booked", "RDV booké"),
]
_FUNNEL_TERMINAL: list[str] = ["Not relevant", "Archived"]
# Soft palette navy → warm to suggest progression toward conversion.
_FUNNEL_COLORS: list[str] = [
    "#1F3A5F", "#2F5D7C", "#3F8099", "#5BA9B8", "#86C5C0",
    "#D9A05B", "#C1121F",
]


def _render_list_funnel(members_df: pd.DataFrame) -> None:
    """Pipeline funnel for the members of one list.

    Renders descending horizontal bars with counts + share + conversion to
    the next stage. Unknown statuses are normalised to ``New``.
    """
    with st.expander("🪜 Funnel pipeline (cette liste)", expanded=False):
        if members_df.empty:
            st.caption("Liste vide — pas de funnel à afficher.")
            return
        statuses = (
            members_df["lead_status"].fillna("New")
            .replace("", "New").astype(str)
        )
        counts = statuses.value_counts().to_dict()

        rows = [(label, counts.get(label, 0), helptxt)
                for label, helptxt in _FUNNEL_STAGES]
        max_count = max((c for _, c, _ in rows), default=1) or 1
        terminal_total = sum(counts.get(t, 0) for t in _FUNNEL_TERMINAL)
        active_total = sum(c for _, c, _ in rows)

        # Headline KPIs
        kc1, kc2, kc3, kc4 = st.columns(4)
        with kc1: _kpi("Pipeline actif", active_total)
        with kc2: _kpi("RDV bookés", counts.get("Meeting booked", 0),
                       css_class="success")
        with kc3: _kpi("Contactés", counts.get("Contacted", 0))
        with kc4: _kpi("Drop-off (Not rel./Archived)", terminal_total,
                       css_class="alert")

        # Funnel rendering — HTML/CSS, no extra deps
        bars_html = ["<div style='margin-top:0.5rem;'>"]
        for i, (label, count, helptxt) in enumerate(rows):
            pct = (count / max_count) * 100 if max_count else 0
            color = _FUNNEL_COLORS[i % len(_FUNNEL_COLORS)]
            share_total = (count / active_total * 100) if active_total else 0
            bars_html.append(
                f"<div style='margin-bottom:0.35rem;'>"
                f"<div style='display:flex;justify-content:space-between;"
                f"font-size:0.8rem;color:#0B2E4A;font-weight:600;'>"
                f"<span>{i+1}. {label}</span>"
                f"<span style='color:#6c757d;font-weight:400;'>"
                f"{count} <span style='font-size:0.72rem;'>"
                f"({share_total:.0f}% du pipe)</span></span></div>"
                f"<div title='{helptxt}' style='height:18px;background:"
                f"{color};width:{max(pct,2):.1f}%;border-radius:3px;"
                f"transition:width 0.4s;'></div></div>"
            )
        bars_html.append("</div>")
        st.markdown("".join(bars_html), unsafe_allow_html=True)

        # Step-by-step conversion (each stage → next)
        st.markdown("**🔁 Conversion étape par étape**")
        conv_cols = st.columns(len(rows) - 1)
        for i in range(len(rows) - 1):
            with conv_cols[i]:
                a_label, a_count, _ = rows[i]
                b_label, b_count, _ = rows[i + 1]
                rate = (b_count / a_count * 100) if a_count else 0
                tone = "success" if rate >= 50 else (
                    "warning" if rate >= 20 else "alert"
                )
                _kpi(
                    f"{a_label[:10]} → {b_label[:10]}",
                    f"{rate:.0f}%",
                    css_class=tone,
                )

        if terminal_total:
            st.caption(
                f"📉 {terminal_total} société(s) sortie(s) du pipeline "
                f"(`Not relevant` = {counts.get('Not relevant', 0)}, "
                f"`Archived` = {counts.get('Archived', 0)})."
            )


def _render_list_timeline(list_id: int) -> None:
    """Recent ActivityLog entries for the members of this list — manager view.

    Groups entries by day, shows ``time + icon + company name + action text``.
    """
    with st.expander("🕒 Timeline d'activité (membres de la liste)", expanded=False):
        rows = list_activity_timeline(list_id, limit=120)
        if not rows:
            st.caption("Aucune activité enregistrée sur cette liste pour le "
                       "moment. Les actions CRM (favoris, status, notes, "
                       "contacted, owner change…) apparaîtront ici.")
            return

        # Filters: by kind, by author, by company
        kinds = sorted({r["kind"] for r in rows})
        authors = sorted({r["author"] or "—" for r in rows})
        cols = st.columns([2, 2, 2])
        with cols[0]:
            picked_kinds = st.multiselect(
                "Type d'action", kinds, default=[],
                key=f"timeline_kinds_{list_id}",
                help="Vide = tous les types.",
            )
        with cols[1]:
            picked_authors = st.multiselect(
                "Auteur", authors, default=[],
                key=f"timeline_authors_{list_id}",
                help="Vide = tous les auteurs.",
            )
        with cols[2]:
            search = st.text_input(
                "🔎 Recherche libre (texte / société)",
                key=f"timeline_search_{list_id}",
            )

        filtered = rows
        if picked_kinds:
            filtered = [r for r in filtered if r["kind"] in picked_kinds]
        if picked_authors:
            filtered = [
                r for r in filtered
                if (r["author"] or "—") in picked_authors
            ]
        if search:
            q = search.lower()
            filtered = [
                r for r in filtered
                if q in (r["account_name"] or "").lower()
                or q in (r["text"] or "").lower()
            ]

        kc1, kc2, kc3 = st.columns(3)
        with kc1: _kpi("Entrées", len(filtered))
        with kc2: _kpi("Sociétés concernées",
                       len({r["exhibitor_id"] for r in filtered}))
        with kc3: _kpi("Auteurs",
                       len({r["author"] or "—" for r in filtered}))

        if not filtered:
            st.caption("Aucune entrée ne correspond aux filtres.")
            return

        # Group by date (descending)
        from collections import OrderedDict
        from datetime import datetime as _dt
        grouped: "OrderedDict[str, list[dict]]" = OrderedDict()
        for r in filtered:
            day = (r["created_at"] or _dt.utcnow()).strftime("%Y-%m-%d")
            grouped.setdefault(day, []).append(r)

        for day, entries in grouped.items():
            st.markdown(
                f"<div style='margin-top:0.6rem;font-weight:600;color:#0B2E4A;"
                f"border-bottom:1px solid #E2E6EA;padding-bottom:0.2rem;'>"
                f"📅 {day} <span style='color:#6c757d;font-weight:400;'>"
                f"&middot; {len(entries)} action(s)</span></div>",
                unsafe_allow_html=True,
            )
            for r in entries:
                icon = _KIND_ICONS.get(r["kind"], "•")
                ts = (r["created_at"] or _dt.utcnow()).strftime("%H:%M")
                author = r["author"] or "—"
                text = (r["text"] or "").strip() or r["kind"]
                st.markdown(
                    f"<div style='padding:0.25rem 0; "
                    f"border-bottom:1px dashed #EFF2F5;'>"
                    f"<span style='color:#6c757d;font-variant-numeric:tabular-nums;"
                    f"margin-right:0.5rem;'>{ts}</span>"
                    f"<span style='margin-right:0.4rem;'>{icon}</span>"
                    f"<strong>{r['account_name']}</strong> "
                    f"<span style='color:#2B2F33;'>· {text}</span> "
                    f"<span style='color:#6c757d;font-style:italic;"
                    f"margin-left:0.4rem;'>— {author}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )


_PRIORITY_ORDER = {"A+": 0, "A": 1, "B": 2, "C": 3, "D": 4}


def _render_members_editable(
    members_df: pd.DataFrame, list_id: int, list_name: str,
) -> int | None:
    """Render members as explicit per-row layouts (NOT a data_editor).

    Each row = ``[⭐ btn | name | country | score | status | note input |
    👁 btn]``. The ⭐ button toggles ``Exhibitor.is_favorite`` instantly
    on click. The 👁 button opens the detailed fiche below. Pagination
    via session_state for lists with many members.
    """
    if members_df.empty:
        st.info("Liste vide.")
        return None

    df = members_df.copy()
    df["_id"] = df["account_id"].str.replace("ESY26-", "").astype(int)
    df["is_favorite"] = df["is_favorite"].fillna(False).astype(bool)
    df["list_note"] = df["list_note"].fillna("")

    # Sort + page-size controls (top of view)
    sc1, sc2, sc3 = st.columns([2, 1, 2])
    with sc1:
        sort_label = st.selectbox(
            "Trier par",
            [
                "Score décroissant", "Defense fit (A+→D)",
                "Nom (A→Z)", "Pays (A→Z)",
                "Status", "Favoris d'abord",
            ],
            index=0,
            key=f"members_sort_{list_id}",
            label_visibility="collapsed",
        )
    if sort_label == "Score décroissant":
        df = df.sort_values("lead_score", ascending=False, na_position="last")
    elif sort_label == "Defense fit (A+→D)":
        df["__pri"] = df["priority_level"].map(_PRIORITY_ORDER).fillna(99)
        df = df.sort_values("__pri").drop(columns=["__pri"])
    elif sort_label == "Nom (A→Z)":
        df = df.sort_values("account_name")
    elif sort_label == "Pays (A→Z)":
        df = df.sort_values("country", na_position="last")
    elif sort_label == "Status":
        df = df.sort_values("lead_status", na_position="last")
    elif sort_label == "Favoris d'abord":
        df = df.sort_values(["is_favorite", "lead_score"],
                              ascending=[False, False])

    page_size = 25
    page_key = f"members_page_{list_id}"
    page = int(st.session_state.get(page_key, 0))
    total_pages = max(1, (len(df) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    with sc2:
        st.caption(f"Page **{page + 1}** / {total_pages}")
    with sc3:
        pc1, pc2 = st.columns(2)
        with pc1:
            if st.button("◀ Préc.", key=f"members_prev_{list_id}",
                         disabled=page <= 0, use_container_width=True):
                st.session_state[page_key] = page - 1
                st.rerun()
        with pc2:
            if st.button("Suiv. ▶", key=f"members_next_{list_id}",
                         disabled=page >= total_pages - 1,
                         use_container_width=True):
                st.session_state[page_key] = page + 1
                st.rerun()

    page_df = df.iloc[page * page_size : (page + 1) * page_size]

    detail_id: int | None = None
    detail_key = f"members_detail_id_{list_id}"
    if detail_key in st.session_state:
        detail_id = int(st.session_state[detail_key])

    # Caption explaining what each row offers, so the user knows where to click.
    st.caption(
        "💡 **Bouton ⭐ Favori** sur chaque ligne pour ajouter/retirer "
        "des favoris en 1 clic. Le **📝 Note** ouvre la note de cette "
        "liste. **👁 Détail** ouvre la fiche complète."
    )

    for _, row in page_df.iterrows():
        eid = int(row["_id"])
        is_fav = bool(row["is_favorite"])

        # Card-style row: a thin colored band at left for status colour, then
        # a single big info line + 3 buttons on the right.
        with st.container(border=True):
            score_val = row.get("lead_score")
            try:
                score_str = (
                    f"{float(score_val):.0f}" if score_val is not None
                    and not pd.isna(score_val) else "—"
                )
            except (TypeError, ValueError):
                score_str = "—"
            info_line = (
                f"<div style='line-height:1.3;'>"
                f"<strong style='font-size:1.0rem;'>{row['account_name']}</strong> "
                f"<span style='font-size:0.78rem;color:#6c757d;'>"
                f"· {row.get('country') or '—'} "
                f"· score {score_str} "
                f"· {row.get('priority_level') or '—'} "
                f"· {row.get('lead_status') or '—'}"
                f"</span></div>"
            )
            ic, b_fav, b_note, b_view = st.columns([6.0, 1.4, 1.2, 1.0])
            with ic:
                st.markdown(info_line, unsafe_allow_html=True)
            with b_fav:
                # The star button is FAT, with a clear text label, so the
                # user sees it whatever the viewport width.
                fav_label = "★ Retirer favori" if is_fav else "⭐ Favori"
                fav_type = "primary" if is_fav else "secondary"
                if st.button(
                    fav_label,
                    key=f"row_fav_{list_id}_{eid}",
                    use_container_width=True,
                    type=fav_type,
                    help=("Cette société est dans tes favoris — clique pour "
                          "la retirer." if is_fav
                          else "Clique pour ajouter cette société à tes "
                          "favoris (1 clic = 1 favori)."),
                ):
                    from app.database import session_scope as _ss
                    with _ss() as s:
                        e = s.get(Exhibitor, eid)
                        if e is not None:
                            e.is_favorite = not bool(e.is_favorite)
                            _log(s, eid,
                                 "favorite_added" if e.is_favorite else "favorite_removed",
                                 f"Row toggle → {e.is_favorite}")
                    st.cache_data.clear()
                    st.toast(
                        f"{'⭐ Ajouté aux favoris' if not is_fav else '★ Retiré des favoris'} : "
                        f"{row['account_name']}",
                        icon="⭐" if not is_fav else "★",
                    )
                    st.rerun()
            with b_note:
                if st.button(
                    "📝 Note",
                    key=f"row_note_toggle_{list_id}_{eid}",
                    use_container_width=True,
                    help="Ouvre / ferme l'éditeur de note pour cette liste.",
                ):
                    open_key = f"row_note_open_{list_id}_{eid}"
                    st.session_state[open_key] = not st.session_state.get(
                        open_key, False
                    )
                    st.rerun()
            with b_view:
                if st.button(
                    "👁 Détail",
                    key=f"row_view_{list_id}_{eid}",
                    use_container_width=True,
                    help="Ouvre la fiche détaillée en bas de page.",
                ):
                    st.session_state[detail_key] = eid
                    detail_id = eid

            # Inline note editor (only shown when toggled open)
            open_key = f"row_note_open_{list_id}_{eid}"
            if st.session_state.get(open_key, False):
                note_key = f"row_note_{list_id}_{eid}"
                current_note = str(row.get("list_note") or "")
                if note_key not in st.session_state:
                    st.session_state[note_key] = current_note
                nc1, nc2 = st.columns([5, 1])
                with nc1:
                    st.text_area(
                        "Note pour cette liste",
                        key=note_key,
                        label_visibility="collapsed",
                        placeholder="Tape ta note ici…",
                        height=70,
                    )
                with nc2:
                    if st.button(
                        "💾 Save",
                        key=f"row_save_note_{list_id}_{eid}",
                        use_container_width=True,
                    ):
                        new_note = st.session_state.get(note_key, "") or ""
                        set_member_note(
                            list_id, eid,
                            new_note if new_note.strip() else None,
                        )
                        st.cache_data.clear()
                        st.toast(
                            f"📝 Note enregistrée : {row['account_name']}",
                            icon="💾",
                        )
                        st.rerun()

    return detail_id


def _render_quick_actions(list_id: int, exhibitor_id: int,
                            list_name: str) -> None:
    """Quick action bar : favorite toggle + per-list note for ONE company.

    Used both by the explicit selectbox (above the table) and by the
    table-row selection branch (below the table). Pulling it out of
    ``_render_opened_list`` keeps the two paths in sync.
    """
    from app.database import session_scope as _ss
    with _ss() as _s:
        _exh = _s.get(Exhibitor, exhibitor_id)
        if _exh is None:
            st.warning("Société introuvable.")
            return
        _is_fav = bool(_exh.is_favorite)
        _name = _exh.company_name

    st.markdown(
        f'<div class="section-title">⚡ Actions rapides — '
        f'« {_name} »</div>',
        unsafe_allow_html=True,
    )
    ab1, ab2 = st.columns([1, 1])
    with ab1:
        fav_label = ("⭐ Marquer favori" if not _is_fav
                     else "★ Retirer des favoris")
        fav_type = "primary" if not _is_fav else "secondary"
        if st.button(fav_label, key=f"fav_qa_{list_id}_{exhibitor_id}",
                     use_container_width=True, type=fav_type):
            from app.database import session_scope
            with session_scope() as s:
                e = s.get(Exhibitor, exhibitor_id)
                if e:
                    e.is_favorite = not bool(e.is_favorite)
                    _log(s, e.id,
                         "favorite_added" if e.is_favorite else "favorite_removed",
                         f"Toggled favorite (quick action) → {e.is_favorite}")
            st.cache_data.clear()
            st.rerun()
        st.caption(
            "Le ⭐ apparaît dans la table et dans la pseudo-liste "
            "« ⭐ Mes favoris »."
        )
    with ab2:
        st.markdown("**📝 Note pour cette liste**")
        existing_note = get_member_note(list_id, exhibitor_id) or ""
        # IMPORTANT: ne PAS combiner ``value=`` et ``key=`` sur un text_area
        # Streamlit — selon la version, le widget devient non-éditable.
        # On pré-remplit session_state à la place et on ne passe que key=.
        note_key = f"qa_note_{list_id}_{exhibitor_id}"
        if note_key not in st.session_state:
            st.session_state[note_key] = existing_note
        st.text_area(
            "Note spécifique à cette liste",
            key=note_key,
            help="Note attachée à la PAIRE (société, liste). La même "
            "société dans une autre liste porte une note indépendante.",
            height=90,
            label_visibility="collapsed",
            placeholder="Ex: en attente du retour de Mr Dupont, contact "
            "via X au salon, commande Q3 prévue…",
        )
        if st.button("💾 Enregistrer la note",
                     key=f"qa_save_note_{list_id}_{exhibitor_id}",
                     use_container_width=True):
            note_text = st.session_state.get(note_key, "") or ""
            set_member_note(list_id, exhibitor_id,
                             note_text if note_text.strip() else None)
            st.cache_data.clear()
            st.success("Note enregistrée.")
            st.rerun()


def _render_opened_list(list_id: int, full_df: pd.DataFrame) -> None:
    cl = get_custom_list(list_id)
    if cl is None:
        st.error("Liste introuvable.")
        st.session_state.pop("opened_list_id", None)
        return

    # Auto-sync on open for dynamic lists — runs once per (list, session-flag)
    # to avoid double-running on every rerun within the same view.
    sync_flag_key = f"_dyn_synced_{list_id}"
    if (getattr(cl, "is_dynamic", False) and cl.criteria_json
            and not st.session_state.get(sync_flag_key)):
        result = sync_dynamic_list(list_id)
        st.session_state[sync_flag_key] = True
        if result["added"] or result["removed"]:
            st.toast(
                f"🔄 Liste dynamique resync : +{result['added']} / "
                f"-{result['removed']} (total: {result['total']})",
                icon="🔄",
            )
            st.cache_data.clear()
            cl = get_custom_list(list_id)  # reload with fresh last_synced_at

    member_ids = list_member_ids(list_id)
    members_df = full_df[
        full_df["account_id"].str.replace("ESY26-", "").astype(int).isin(member_ids)
    ].copy()

    # Inject the per-membership note into the dataframe so it shows up in the
    # table (we store it under ``list_note`` to avoid clashing with the
    # company-level ``notes`` column already used by the CRM transformer).
    notes_map = list_member_notes_map(list_id)
    if not members_df.empty:
        members_df["list_note"] = (
            members_df["account_id"]
            .str.replace("ESY26-", "").astype(int)
            .map(notes_map).fillna("")
        )

    # Header strip with back button + actions
    h1, h2, h3, h4, h5 = st.columns([3, 1, 1, 1, 1])
    with h1:
        dyn_badge = ""
        if getattr(cl, "is_dynamic", False):
            dyn_badge = (" <span class='badge' style='background:#D97706;"
                         "color:white;'>🔄 dynamique</span>")
        st.markdown(f"### 📋 {cl.name}{dyn_badge}", unsafe_allow_html=True)
        meta_bits = [
            f"{len(members_df)} sociétés",
            f"owner: {cl.owner or '—'}",
            f"team: {cl.sales_team or '—'}",
            f"créée le {cl.created_at:%Y-%m-%d}",
        ]
        if (getattr(cl, "is_dynamic", False)
                and getattr(cl, "last_synced_at", None)):
            meta_bits.append(f"sync: {cl.last_synced_at:%Y-%m-%d %H:%M}")
        st.caption(" · ".join(meta_bits))
        if cl.description:
            st.caption(f"📝 {cl.description}")
    with h2:
        if st.button("← Retour", key="back_to_lists",
                     use_container_width=True):
            st.session_state.pop("opened_list_id", None)
            st.rerun()
    with h3:
        if cl.criteria_json:
            # Resync NOW (always available with criteria, regardless of is_dynamic)
            if st.button("🔄 Resync now",
                         key=f"resync_{list_id}",
                         use_container_width=True,
                         help="Recalcule manuellement les membres depuis les "
                         "critères sauvegardés (ajoute les nouveaux matchs, "
                         "retire ceux qui ne matchent plus)."):
                result = sync_dynamic_list(list_id)
                st.session_state.pop(f"_dyn_synced_{list_id}", None)
                st.cache_data.clear()
                st.success(
                    f"Resync : +{result['added']} / -{result['removed']} "
                    f"(total: {result['total']})"
                )
                st.rerun()
            if st.button("🔁 Recharger filtres",
                         key=f"reload_filters_{list_id}",
                         use_container_width=True,
                         help="Pousse les filtres sauvegardés dans l'onglet Companies."):
                _push_filters_into_session(cl.criteria_json)
                st.success(
                    "Filtres rechargés ✓ — clique sur l'onglet **🏢 Companies** "
                    "pour les voir appliqués."
                )
        else:
            st.caption("(pas de filtres sauvegardés)")
    with h4:
        if st.button("📑 Dupliquer", key=f"dup_{list_id}",
                     use_container_width=True,
                     help="Cloner cette liste (avec ses membres + critères + métadonnées)."):
            new_id = duplicate_list(list_id, f"{cl.name} (copie)")
            st.cache_data.clear()
            st.session_state["opened_list_id"] = new_id
            st.success(f"Liste dupliquée — nouvelle id={new_id}")
            st.rerun()
    with h5:
        # Two-step delete: first click sets a flag, second click confirms.
        confirm_key = f"confirm_del_{list_id}"
        if st.session_state.get(confirm_key):
            if st.button("⚠️ Confirmer ?", key=f"confirm_btn_{list_id}",
                         use_container_width=True, type="primary"):
                delete_list(list_id)
                st.session_state.pop("opened_list_id", None)
                st.session_state.pop(confirm_key, None)
                st.cache_data.clear()
                st.rerun()
            if st.button("Annuler", key=f"cancel_del_{list_id}",
                         use_container_width=True):
                st.session_state.pop(confirm_key, None)
                st.rerun()
        else:
            if st.button("🗑 Supprimer", key=f"del_in_view_{list_id}",
                         use_container_width=True):
                st.session_state[confirm_key] = True
                st.rerun()

    _render_list_edit_form(cl)

    # Analytics
    st.markdown(
        '<div class="section-title">📊 Aperçu analytique</div>',
        unsafe_allow_html=True,
    )
    _list_analytics(members_df)

    # Pipeline funnel for this list.
    _render_list_funnel(members_df)

    # Activity timeline — manager view across the list's members.
    _render_list_timeline(list_id)

    # Standalone single-company action selector — placed BEFORE the table
    # because Streamlit's ``st.dataframe`` row-selection (click on tiny left
    # margin) is unintuitive. With this selectbox the rep picks a company
    # by name and gets the favorite + note widgets immediately, no row
    # click needed.
    if not members_df.empty:
        st.markdown(
            '<div class="section-title">⚡ Actions rapides société par société</div>',
            unsafe_allow_html=True,
        )
        sb_rows = (
            members_df[["account_id", "account_name", "country", "is_favorite"]]
            .sort_values("account_name")
            .values.tolist()
        )
        sb_labels = {
            a: f"{'⭐ ' if bool(fav) else ''}{n}  ·  {c or '—'}"
            for a, n, c, fav in sb_rows
        }
        sb_options = ["—"] + [a for a, _, _, _ in sb_rows]
        picked_acc = st.selectbox(
            f"Choisir une société (sur {len(sb_rows)} membres)",
            sb_options,
            format_func=lambda a: "— Choisir —" if a == "—" else sb_labels.get(a, a),
            key=f"single_picker_{list_id}",
            help="Sélectionne ici pour avoir directement le toggle favori + "
            "la note sans avoir à cliquer dans la table en dessous.",
        )
        if picked_acc != "—":
            picked_id = int(picked_acc.replace("ESY26-", ""))
            _render_quick_actions(list_id, picked_id, cl.name)
            st.divider()

    # Filtres + tableau scopés à la liste
    st.markdown(
        '<div class="section-title">🏢 Membres de la liste — édition inline</div>',
        unsafe_allow_html=True,
    )
    # data_editor : la colonne ⭐ + la note sont CLIQUABLES / ÉDITABLES
    # directement dans la ligne. Bulk + comparaison passent via la deuxième
    # vue (tableau classique multi-select) en bas de page.
    detail_id_editor = _render_members_editable(members_df, list_id, cl.name)
    if detail_id_editor is not None:
        _render_quick_actions(list_id, detail_id_editor, cl.name)
        st.divider()
        render_detail(detail_id_editor)

    # Vue tableau classique pour la sélection multiple (bulk + comparaison)
    with st.expander(
        "📊 Vue tableau classique (sélection multi-row pour bulk / comparaison)",
        expanded=False,
    ):
        detail_id, bulk_ids = render_table(
            members_df, total_rows=len(members_df),
            key_prefix=f"list_{list_id}",
            extra_columns=["list_note"],
        )
        if detail_id is not None and detail_id_editor is None:
            _render_quick_actions(list_id, detail_id, cl.name)
            st.divider()
            render_detail(detail_id)
        elif 2 <= len(bulk_ids) <= 4:
            mode = st.radio(
                f"{len(bulk_ids)} sélectionnées :",
                ["⚖️ Comparaison côte-à-côte", "⚡ Bulk actions"],
                horizontal=True, key=f"opened_multi_{list_id}",
            )
            if mode.startswith("⚖️"):
                render_comparison(bulk_ids)
            else:
                render_bulk_actions(bulk_ids, from_list_id=list_id)
        elif bulk_ids:
            render_bulk_actions(bulk_ids, from_list_id=list_id)

    # Add a company manually — search by name and append to the list
    st.divider()
    with st.expander("➕ Ajouter une société manuellement"):
        from app.database import session_scope as _ss
        existing_member_set = set(member_ids)
        all_candidates = full_df[
            ~full_df["account_id"].str.replace("ESY26-", "").astype(int).isin(existing_member_set)
        ].copy()
        if all_candidates.empty:
            st.caption("Toutes les sociétés du catalogue sont déjà dans la liste.")
        else:
            search = st.text_input(
                "Chercher par nom",
                key=f"add_search_{list_id}",
                placeholder="ex: Aselsan",
            )
            if search and len(search) >= 2:
                pattern = search.lower()
                hits = all_candidates[
                    all_candidates["account_name"].str.lower().str.contains(
                        pattern, na=False, regex=False
                    )
                ].head(15)
                if hits.empty:
                    st.caption("Aucune société trouvée.")
                else:
                    options = hits["account_id"].tolist()
                    labels = {
                        a: f"{n}  ({c})"
                        for a, n, c in hits[["account_id", "account_name", "country"]].values
                    }
                    picked = st.multiselect(
                        f"{len(hits)} résultat(s) — choisir une ou plusieurs sociétés",
                        options,
                        format_func=lambda a: labels.get(a, a),
                        key=f"add_picks_{list_id}",
                    )
                    if picked and st.button(
                        f"➕ Ajouter {len(picked)} à la liste",
                        key=f"add_apply_{list_id}",
                        type="primary",
                    ):
                        ids = [int(a.replace("ESY26-", "")) for a in picked]
                        added = add_to_list(list_id, ids)
                        st.cache_data.clear()
                        st.success(f"{added} société(s) ajoutée(s) à la liste.")
                        st.rerun()

    # CRM-ready exports SCOPED to this list
    st.divider()
    st.markdown("**⬇ Exports CRM-ready (cette liste uniquement)**")
    ex_cols = st.columns(6)
    actions = [
        ("Dynamics", export_dynamics_csv),
        ("Salesforce", export_salesforce_csv),
        ("HubSpot", export_hubspot_csv),
        ("Airtable", export_airtable_csv),
        ("Prospecting", export_prospecting_csv),
        ("Full XLSX", export_full_xlsx),
    ]
    for i, (label, fn) in enumerate(actions):
        with ex_cols[i]:
            if st.button(label, key=f"crm_{label}_{list_id}",
                         use_container_width=True):
                p = fn(list_id=list_id)
                st.success(f"écrit : {p}")

    # Lightweight downloads (CSV/XLSX of the displayed dataframe)
    st.markdown("**⬇ Téléchargement direct (la liste complète)**")
    e1, e2, e3 = st.columns([1, 1, 4])
    with e1:
        st.download_button(
            f"⬇ CSV ({len(members_df)})",
            data=members_df.to_csv(index=False).encode("utf-8"),
            file_name=f"list_{cl.name.replace(' ', '_')[:60]}_{datetime.utcnow():%Y%m%d}.csv",
            mime="text/csv", use_container_width=True,
        )
    with e2:
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            members_df.to_excel(w, index=False, sheet_name="List")
        buf.seek(0)
        st.download_button(
            f"⬇ XLSX ({len(members_df)})", data=buf,
            file_name=f"list_{cl.name.replace(' ', '_')[:60]}_{datetime.utcnow():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with e3:
        st.caption(
            "Export brut (toutes colonnes CRM). Les boutons CRM-ready au-dessus "
            "renomment les colonnes pour Microsoft Dynamics / Salesforce / "
            "HubSpot / Airtable directement."
        )


def _render_favorites_view(full_df: pd.DataFrame) -> None:
    """Pseudo-list view : every exhibitor with ``is_favorite=True``.

    Behaves like a custom list (header strip, analytics, table, bulk, detail,
    exports) but without a real ``CustomList`` row. Favorites are stored on
    ``Exhibitor.is_favorite`` directly — toggled from the fiche or from this
    view via a dedicated bulk button.
    """
    if "is_favorite" not in full_df.columns:
        st.warning("La colonne `is_favorite` est absente du dataset.")
        st.session_state.pop("opened_list_id", None)
        return

    members_df = full_df[full_df["is_favorite"] == True].copy()  # noqa: E712

    # Header strip with back button
    h1, h2 = st.columns([5, 1])
    with h1:
        st.markdown("### ⭐ Mes favoris")
        st.caption(
            f"{len(members_df)} société(s) marquée(s) en favori · "
            "vue transverse — indépendante des listes commerciales."
        )
    with h2:
        if st.button("← Retour", key="back_from_favs",
                     use_container_width=True):
            st.session_state.pop("opened_list_id", None)
            st.rerun()

    if members_df.empty:
        st.info(
            "Aucun favori pour le moment. Marque une société en favori depuis "
            "sa fiche (bouton ⭐ Favori dans la section CRM follow-up)."
        )
        return

    # Analytics
    st.markdown(
        '<div class="section-title">📊 Aperçu analytique</div>',
        unsafe_allow_html=True,
    )
    _list_analytics(members_df)

    # Table + bulk + detail (key_prefix avoids collision with Companies tab)
    st.markdown(
        '<div class="section-title">🏢 Sociétés en favori</div>',
        unsafe_allow_html=True,
    )
    detail_id, bulk_ids = render_table(
        members_df, total_rows=len(members_df),
        key_prefix="favorites",
    )
    if detail_id is not None:
        render_detail(detail_id)
    elif 2 <= len(bulk_ids) <= 4:
        mode = st.radio(
            f"{len(bulk_ids)} sélectionnées :",
            ["⚖️ Comparaison côte-à-côte", "⚡ Bulk actions"],
            horizontal=True, key="favs_multi_mode",
        )
        if mode.startswith("⚖️"):
            render_comparison(bulk_ids)
        else:
            _favorites_bulk_unstar(bulk_ids)
            render_bulk_actions(bulk_ids)
    elif bulk_ids:
        _favorites_bulk_unstar(bulk_ids)
        render_bulk_actions(bulk_ids)

    # Exports — favorites scope passed via apply_filters({"only_favorites": True})
    st.divider()
    st.markdown("**⬇ Exports CRM-ready (favoris uniquement)**")
    fav_filters = {"only_favorites": True}
    ex_cols = st.columns(6)
    actions = [
        ("Dynamics", export_dynamics_csv),
        ("Salesforce", export_salesforce_csv),
        ("HubSpot", export_hubspot_csv),
        ("Airtable", export_airtable_csv),
        ("Prospecting", export_prospecting_csv),
        ("Full XLSX", export_full_xlsx),
    ]
    for i, (label, fn) in enumerate(actions):
        with ex_cols[i]:
            if st.button(label, key=f"crm_fav_{label}",
                         use_container_width=True):
                p = fn(filters=fav_filters)
                st.success(f"écrit : {p}")

    st.markdown("**⬇ Téléchargement direct (favoris bruts)**")
    e1, e2, e3 = st.columns([1, 1, 4])
    with e1:
        st.download_button(
            f"⬇ CSV ({len(members_df)})",
            data=members_df.to_csv(index=False).encode("utf-8"),
            file_name=f"favorites_{datetime.utcnow():%Y%m%d_%H%M%S}.csv",
            mime="text/csv", use_container_width=True,
        )
    with e2:
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            members_df.to_excel(w, index=False, sheet_name="Favorites")
        buf.seek(0)
        st.download_button(
            f"⬇ XLSX ({len(members_df)})", data=buf,
            file_name=f"favorites_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with e3:
        st.caption(
            "Les favoris sont stockés au niveau de la société (pas d'une "
            "liste) — la même société peut être en favori ET dans plusieurs "
            "listes commerciales sans interférence."
        )


def _favorites_bulk_unstar(exhibitor_ids: list[int]) -> None:
    """Bulk button to remove the ⭐ flag from N exhibitors at once."""
    n = len(exhibitor_ids)
    if st.button(f"★ Retirer {n} société(s) des favoris",
                 key=f"bulk_unstar_{n}", type="primary"):
        from app.database import session_scope as _ss
        unstarred = 0
        with _ss() as s:
            for eid in exhibitor_ids:
                exh = s.get(Exhibitor, eid)
                if exh and exh.is_favorite:
                    exh.is_favorite = False
                    unstarred += 1
        st.cache_data.clear()
        st.success(f"{unstarred} société(s) retirée(s) des favoris.")
        st.rerun()


def _render_compare_lists(full_df: pd.DataFrame) -> None:
    """Side panel: pick 2 lists, show overlap counts + 3 mini-tables."""
    lists = list_custom_lists()
    if len(lists) < 2:
        st.caption("Il faut au moins 2 listes pour comparer.")
        return
    with st.expander("⚖️ Comparer 2 listes (overlap)", expanded=False):
        cc1, cc2 = st.columns(2)
        with cc1:
            list_a = st.selectbox(
                "Liste A", lists,
                format_func=lambda l: f"{l.name} ({_list_member_count(l.id)})",
                key="compare_a",
            )
        with cc2:
            list_b = st.selectbox(
                "Liste B", lists,
                format_func=lambda l: f"{l.name} ({_list_member_count(l.id)})",
                index=1 if len(lists) > 1 else 0,
                key="compare_b",
            )
        if list_a.id == list_b.id:
            st.warning("Choisis deux listes différentes.")
            return
        ov = list_overlap(list_a.id, list_b.id)
        kc = st.columns(5)
        with kc[0]: _kpi(f"A : {list_a.name[:18]}", ov["a_total"])
        with kc[1]: _kpi(f"B : {list_b.name[:18]}", ov["b_total"])
        with kc[2]: _kpi("Communes", len(ov["in_both"]), css_class="success")
        with kc[3]: _kpi("Uniques A", len(ov["a_only"]), css_class="alert")
        with kc[4]: _kpi("Uniques B", len(ov["b_only"]), css_class="warning")

        def _frame_for(ids: list[int]) -> pd.DataFrame:
            return full_df[
                full_df["account_id"].str.replace("ESY26-", "").astype(int).isin(ids)
            ][["account_name", "country", "core_business", "priority_level", "lead_score"]]

        # Pre-compute the three frames once — used both by the tabs and
        # by the export buttons further below.
        frames = {
            "in_both": _frame_for(ov["in_both"]),
            "a_only": _frame_for(ov["a_only"]),
            "b_only": _frame_for(ov["b_only"]),
        }

        t1, t2, t3 = st.tabs([
            f"🟢 Communes ({len(ov['in_both'])})",
            f"🔵 Uniques A ({len(ov['a_only'])})",
            f"🟠 Uniques B ({len(ov['b_only'])})",
        ])
        with t1:
            st.dataframe(frames["in_both"],
                         use_container_width=True, hide_index=True, height=320)
        with t2:
            st.dataframe(frames["a_only"],
                         use_container_width=True, hide_index=True, height=320)
        with t3:
            st.dataframe(frames["b_only"],
                         use_container_width=True, hide_index=True, height=320)

        # One-click create new list from any of the three buckets
        st.markdown("**Créer une nouvelle liste depuis…**")
        bb1, bb2, bb3 = st.columns(3)
        for col, key, ids, label in (
            (bb1, "in_both", ov["in_both"], "🟢 Communes"),
            (bb2, "a_only", ov["a_only"], "🔵 Uniques A"),
            (bb3, "b_only", ov["b_only"], "🟠 Uniques B"),
        ):
            with col:
                if not ids:
                    st.caption(f"({label} : vide)")
                    continue
                if st.button(f"📂 Liste depuis {label}",
                             key=f"newfrom_{key}", use_container_width=True):
                    new_name = f"{label}: {list_a.name} × {list_b.name}"[:120]
                    new_id = create_custom_list(name=new_name)
                    add_to_list(new_id, ids)
                    st.cache_data.clear()
                    st.session_state["opened_list_id"] = new_id
                    st.rerun()

        # Export each bucket to CSV / a combined XLSX
        st.markdown("**⬇ Exporter le résultat de comparaison**")
        ec1, ec2, ec3, ec4 = st.columns(4)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        slug = (f"{list_a.name}_x_{list_b.name}"
                .replace(" ", "_").replace("/", "_")[:80])
        with ec1:
            st.download_button(
                f"⬇ CSV Communes ({len(frames['in_both'])})",
                data=frames["in_both"].to_csv(index=False).encode("utf-8"),
                file_name=f"compare_{slug}_communes_{ts}.csv",
                mime="text/csv", use_container_width=True,
                disabled=frames["in_both"].empty,
            )
        with ec2:
            st.download_button(
                f"⬇ CSV Uniques A ({len(frames['a_only'])})",
                data=frames["a_only"].to_csv(index=False).encode("utf-8"),
                file_name=f"compare_{slug}_uniqA_{ts}.csv",
                mime="text/csv", use_container_width=True,
                disabled=frames["a_only"].empty,
            )
        with ec3:
            st.download_button(
                f"⬇ CSV Uniques B ({len(frames['b_only'])})",
                data=frames["b_only"].to_csv(index=False).encode("utf-8"),
                file_name=f"compare_{slug}_uniqB_{ts}.csv",
                mime="text/csv", use_container_width=True,
                disabled=frames["b_only"].empty,
            )
        with ec4:
            buf = BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                frames["in_both"].to_excel(w, index=False, sheet_name="Communes")
                frames["a_only"].to_excel(w, index=False, sheet_name="Uniques A")
                frames["b_only"].to_excel(w, index=False, sheet_name="Uniques B")
            buf.seek(0)
            st.download_button(
                "⬇ XLSX (3 onglets)",
                data=buf,
                file_name=f"compare_{slug}_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )


def _render_merge_lists() -> None:
    lists = list_custom_lists()
    if len(lists) < 2:
        return
    with st.expander("🧬 Fusionner plusieurs listes (union)", expanded=False):
        with st.form("merge_lists_form"):
            picked = st.multiselect(
                "Listes à fusionner *",
                lists,
                format_func=lambda l: f"{l.name} ({_list_member_count(l.id)})",
                key="merge_picks",
            )
            new_name = st.text_input("Nom de la nouvelle liste *",
                                      key="merge_new_name")
            new_desc = st.text_input("Description", key="merge_new_desc")
            new_owner = st.text_input("Owner", key="merge_new_owner")
            if st.form_submit_button("Fusionner", type="primary"):
                if len(picked) < 2:
                    st.warning("Au moins 2 listes requises.")
                elif not new_name.strip():
                    st.warning("Nom de liste requis.")
                else:
                    new_id = merge_lists(
                        [l.id for l in picked], new_name.strip(),
                        description=new_desc or None, owner=new_owner or None,
                    )
                    st.cache_data.clear()
                    st.session_state["opened_list_id"] = new_id
                    st.success(
                        f"Liste « {new_name} » créée — union de "
                        f"{len(picked)} listes."
                    )
                    st.rerun()


def _render_csv_import(df: pd.DataFrame) -> None:
    """Import a CSV (or XLSX) of company names → match against the catalog
    by name (and optional country) → bulk-add to a target list.

    Shows a 3-step UI:
    1. File upload + column picker (auto-detected when possible).
    2. Preview of matches / unmatched / ambiguous.
    3. Target list (existing or new) + apply button.
    """
    with st.expander("📥 Importer une liste CSV / XLSX (CRM legacy → liste)",
                     expanded=False):
        st.caption(
            "Charge un fichier de sociétés existant, on match contre les "
            "exposants catalogue par **nom** (case-insensitive), et "
            "optionnellement filtré par **pays** pour lever les ambiguïtés. "
            "Les sociétés non-trouvées dans le catalogue sont listées pour "
            "vérification manuelle — pas d'ajout silencieux."
        )
        uploaded = st.file_uploader(
            "Fichier (CSV ou XLSX)",
            type=["csv", "xlsx"],
            key="csv_import_file",
        )
        if not uploaded:
            return

        try:
            if uploaded.name.lower().endswith(".xlsx"):
                src = pd.read_excel(uploaded)
            else:
                src = pd.read_csv(uploaded)
        except Exception as e:  # noqa: BLE001
            st.error(f"Lecture impossible : {e}")
            return

        if src.empty:
            st.warning("Fichier vide.")
            return

        st.caption(f"📄 **{len(src)} ligne(s)** chargée(s) — colonnes : "
                    f"`{', '.join(src.columns.astype(str)[:12])}`"
                    f"{'…' if len(src.columns) > 12 else ''}")

        # Auto-detect likely name / country columns
        cols_lower = {c: str(c).lower() for c in src.columns}
        guess_name = next(
            (c for c, l in cols_lower.items()
             if l in ("name", "company", "company name", "société", "societe",
                      "account_name", "account name", "nom")),
            list(src.columns)[0] if len(src.columns) else None,
        )
        guess_country = next(
            (c for c, l in cols_lower.items()
             if l in ("country", "pays", "country/region")),
            None,
        )

        cc1, cc2 = st.columns(2)
        with cc1:
            name_col = st.selectbox(
                "Colonne *Nom de société* *",
                list(src.columns),
                index=list(src.columns).index(guess_name) if guess_name in src.columns else 0,
                key="csv_name_col",
            )
        with cc2:
            country_options = ["(aucune)"] + list(src.columns)
            default_idx = (
                country_options.index(guess_country)
                if guess_country and guess_country in country_options else 0
            )
            country_col = st.selectbox(
                "Colonne *Pays* (facultatif, lève les ambiguïtés)",
                country_options,
                index=default_idx,
                key="csv_country_col",
            )

        # Build a normalised lookup of catalog names → account_id
        import re as _re

        def _norm(x: object) -> str:
            s = str(x or "").lower()
            # collapse non-alphanum to space, strip
            s = _re.sub(r"[^a-z0-9]+", " ", s).strip()
            return s

        catalog = df[["account_id", "account_name", "country"]].copy()
        catalog["_norm"] = catalog["account_name"].apply(_norm)
        # Build a mapping (name, country) -> rows for ambiguity resolution
        name_index: dict[str, list[tuple[str, str, str]]] = {}
        for a, n, c, nn in catalog[
            ["account_id", "account_name", "country", "_norm"]
        ].values:
            name_index.setdefault(nn, []).append((str(a), str(n), str(c or "")))

        matched: list[dict] = []
        unmatched: list[str] = []
        ambiguous: list[dict] = []
        for _, srow in src.iterrows():
            raw_name = str(srow[name_col] or "").strip()
            if not raw_name:
                continue
            nn = _norm(raw_name)
            hits = name_index.get(nn, [])
            srow_country = (
                str(srow[country_col] or "").strip()
                if country_col and country_col != "(aucune)" else ""
            )
            if not hits:
                unmatched.append(raw_name)
                continue
            if len(hits) > 1 and srow_country:
                # disambiguate by country (case-insensitive prefix match)
                target_c = srow_country.lower()
                hits_filtered = [
                    h for h in hits
                    if h[2].lower().startswith(target_c)
                    or target_c.startswith(h[2].lower())
                ]
                hits = hits_filtered or hits
            if len(hits) == 1:
                matched.append({
                    "input_name": raw_name,
                    "account_id": hits[0][0],
                    "matched_name": hits[0][1],
                    "country": hits[0][2],
                })
            else:
                ambiguous.append({
                    "input_name": raw_name,
                    "candidates": hits,
                })

        kc1, kc2, kc3 = st.columns(3)
        with kc1: _kpi("✅ Matchés", len(matched), css_class="success")
        with kc2: _kpi("❓ Ambigus", len(ambiguous), css_class="warning")
        with kc3: _kpi("❌ Introuvables", len(unmatched), css_class="alert")

        if matched:
            with st.expander(f"✅ Voir les {len(matched)} matches"):
                st.dataframe(
                    pd.DataFrame(matched),
                    use_container_width=True, hide_index=True, height=240,
                )
        if ambiguous:
            with st.expander(f"❓ {len(ambiguous)} cas ambigus à résoudre"):
                for amb in ambiguous[:50]:
                    st.markdown(f"**{amb['input_name']}** — candidats :")
                    for a, n, c in amb["candidates"]:
                        st.caption(f"• {a} — {n} ({c or '—'})")
                if len(ambiguous) > 50:
                    st.caption(f"…et {len(ambiguous) - 50} autres "
                               "(ajoute la colonne *Pays* pour les lever).")
        if unmatched:
            with st.expander(f"❌ {len(unmatched)} introuvables dans le catalogue"):
                st.dataframe(
                    pd.DataFrame({"input_name": unmatched}),
                    use_container_width=True, hide_index=True, height=200,
                )

        if not matched:
            st.info("Aucune ligne matchée — pas d'ajout possible. Vérifie "
                    "la colonne nom et la normalisation.")
            return

        # Target list
        st.markdown("**🎯 Liste cible**")
        existing = list_custom_lists()
        tc1, tc2 = st.columns(2)
        with tc1:
            target_existing = st.selectbox(
                "Liste existante",
                ["(créer une nouvelle liste)"] + [l.name for l in existing],
                key="csv_target_existing",
            )
        with tc2:
            new_name = st.text_input(
                "ou nouveau nom",
                key="csv_new_name",
                placeholder=f"Import {datetime.utcnow():%Y-%m-%d}",
            )
        if st.button(f"📥 Importer {len(matched)} sociétés",
                     key="csv_apply", type="primary"):
            target_id: int | None = None
            if new_name.strip():
                target_id = create_custom_list(
                    name=new_name.strip(),
                    description=f"CSV import — {len(matched)} matches "
                    f"(file: {uploaded.name})",
                )
            elif target_existing != "(créer une nouvelle liste)":
                lst = next(
                    (l for l in existing if l.name == target_existing), None
                )
                target_id = lst.id if lst else None
            if target_id is None:
                st.warning("Choisis une liste existante ou saisis un "
                           "nouveau nom.")
                return
            ids = [int(m["account_id"].replace("ESY26-", "")) for m in matched]
            added = add_to_list(target_id, ids)
            st.cache_data.clear()
            st.success(
                f"📥 {added} société(s) ajoutée(s) à la liste cible "
                f"(id={target_id}). Bascule dessus avec « 📂 Ouvrir » "
                "ci-dessous."
            )
            st.session_state["opened_list_id"] = target_id
            st.rerun()


def _render_lists_index(df: pd.DataFrame) -> None:
    # Transverse "Favoris" entry point — pseudo-list, always visible at the top.
    n_fav = (
        int((df["is_favorite"] == True).sum())  # noqa: E712
        if "is_favorite" in df.columns else 0
    )
    fc1, fc2 = st.columns([5, 1])
    with fc1:
        st.markdown(
            f"<span class='badge' style='background:#D97706;color:white;'>"
            f"{n_fav}</span> &nbsp; ⭐ **Mes favoris** "
            "<span style='color:#6c757d;'>"
            "&middot; vue transverse, toutes listes confondues</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Stocké au niveau société (`is_favorite`) — orthogonal aux listes "
            "commerciales. Marque une société en favori depuis sa fiche."
        )
    with fc2:
        if st.button("📂 Ouvrir", key="open_favorites",
                     use_container_width=True, type="primary"):
            st.session_state["opened_list_id"] = "favorites"
            st.rerun()
    st.divider()

    with st.expander("➕ Créer une nouvelle liste à partir des filtres actuels", expanded=False):
        with st.form("new_list_form", clear_on_submit=True):
            name = st.text_input("Nom de la liste *")
            description = st.text_input("Description")
            cc1, cc2 = st.columns(2)
            with cc1:
                owner = st.text_input("Owner")
            with cc2:
                sales_team = st.text_input("Sales team")
            color = st.selectbox("Couleur badge", ["", "navy", "alert", "warning", "success"])
            submit = st.form_submit_button("Créer + ajouter les sociétés filtrées",
                                            type="primary")
            if submit and name:
                cl_id = create_custom_list(
                    name=name, description=description or None,
                    owner=owner or None, color=color or None,
                )
                # save sales_team separately + criteria from current filters
                update_custom_list(cl_id, sales_team=sales_team or None)
                ids = [
                    int(a.replace("ESY26-", ""))
                    for a in df["account_id"].tolist() if isinstance(a, str)
                ]
                added = add_to_list(cl_id, ids)
                st.success(f"Liste « {name} » créée avec {added} sociétés.")
                st.cache_data.clear()
                st.session_state["opened_list_id"] = cl_id
                st.rerun()

    lists = list_custom_lists()

    # Multi-list operations panels (compare + merge) — only when ≥2 lists exist
    _render_compare_lists(df)
    _render_merge_lists()
    _render_csv_import(df)

    if not lists:
        st.info(
            "Aucune liste créée pour le moment. Crée-en une depuis l'onglet "
            "🏢 **Companies** (bouton « 💾 Save as list » en haut à droite du "
            "tableau) ou depuis l'expander ci-dessus."
        )
        return

    st.markdown(f"**{len(lists)} liste(s) actives**")
    # Master list — clickable rows with inline stats + pin / archive / delete
    for l in lists:
        stats = _list_inline_stats(l.id, df)
        c1, c2, c3, c4, c5, c6, c7 = st.columns([3.0, 3.4, 0.6, 0.6, 0.9, 0.9, 0.6])
        with c1:
            badge_color = {
                "alert": "#C1121F", "warning": "#D97706",
                "success": "#1F7A4D", "navy": "#0B2E4A",
            }.get(l.color or "", "#2F5D7C")
            pin_marker = "📌 " if l.is_pinned else ""
            dyn_marker = ""
            if getattr(l, "is_dynamic", False):
                dyn_marker = (" <span class='badge' style='background:"
                              "#D97706;color:white;'>🔄</span>")
            st.markdown(
                f"<span class='badge' style='background:{badge_color};color:white;'>"
                f"{stats['count']}</span> &nbsp; {pin_marker}**{l.name}**"
                f"{dyn_marker}",
                unsafe_allow_html=True,
            )
            meta_bits = [f"owner: {l.owner or '—'}"]
            if l.sales_team:
                meta_bits.append(f"team: {l.sales_team}")
            meta_bits.append(f"{l.created_at:%Y-%m-%d}")
            st.caption(" · ".join(meta_bits))
        with c2:
            # Inline stats (instead of just description)
            chip_html = []
            if stats["count"]:
                chip_html.append(
                    f"<span class='badge' style='background:#0B2E4A;color:white;"
                    f"margin-right:0.25rem;'>{stats['count']} sociétés</span>"
                )
            if stats["contacted"]:
                chip_html.append(
                    f"<span class='badge' style='background:#1F7A4D;color:white;"
                    f"margin-right:0.25rem;'>✅ {stats['contacted']} contactés</span>"
                )
            if stats["meetings"]:
                chip_html.append(
                    f"<span class='badge' style='background:#2F5D7C;color:white;"
                    f"margin-right:0.25rem;'>📅 {stats['meetings']} avec date</span>"
                )
            if stats["overdue"]:
                chip_html.append(
                    f"<span class='badge' style='background:#C1121F;color:white;"
                    f"margin-right:0.25rem;'>⚠️ {stats['overdue']} overdue</span>"
                )
            if stats["avg_score"]:
                chip_html.append(
                    f"<span class='badge' style='background:#D97706;color:white;'>"
                    f"⭐ score moyen {stats['avg_score']:.0f}</span>"
                )
            st.markdown(" ".join(chip_html), unsafe_allow_html=True)
            if l.description:
                st.caption(l.description)
        with c3:
            pin_label = "📌" if not l.is_pinned else "📍"
            if st.button(pin_label, key=f"pin_{l.id}",
                         use_container_width=True,
                         help="Épingler / retirer l'épingle"):
                toggle_pin_list(l.id)
                st.cache_data.clear()
                st.rerun()
        with c4:
            if st.button("📦", key=f"arch_{l.id}",
                         use_container_width=True,
                         help="Archiver (gardé en historique, masqué)"):
                archive_list(l.id)
                st.cache_data.clear()
                st.rerun()
        with c5:
            if st.button("📂 Ouvrir", key=f"open_{l.id}",
                         use_container_width=True, type="primary"):
                st.session_state["opened_list_id"] = l.id
                st.rerun()
        with c6:
            if st.button("⬇ XLSX", key=f"export_xlsx_idx_{l.id}",
                         use_container_width=True):
                p = export_custom_list_csv(l.id, fmt="xlsx")
                st.success(f"écrit : {p}")
        with c7:
            # Two-step delete (idem opened view): first click flags, second click confirms.
            confirm_key = f"confirm_del_idx_{l.id}"
            if st.session_state.get(confirm_key):
                if st.button("⚠️", key=f"confirm_btn_idx_{l.id}",
                             use_container_width=True, type="primary",
                             help="Confirmer la suppression définitive"):
                    delete_list(l.id)
                    st.session_state.pop(confirm_key, None)
                    st.cache_data.clear()
                    st.rerun()
            else:
                if st.button("🗑", key=f"del_idx_{l.id}",
                             use_container_width=True,
                             help="Suppression définitive (double-clic). "
                             "Préférez 📦 Archiver."):
                    st.session_state[confirm_key] = True
                    st.rerun()
        st.divider()

    # Archived lists section — collapsed by default
    archived = list_archived_custom_lists()
    if archived:
        with st.expander(f"📦 Archivées ({len(archived)})"):
            for l in archived:
                ac1, ac2, ac3, ac4 = st.columns([4, 3, 1.2, 1.2])
                with ac1:
                    st.markdown(
                        f"**{l.name}** — {_list_member_count(l.id)} sociétés"
                    )
                    st.caption(
                        f"archivée le {l.archived_at:%Y-%m-%d}" if l.archived_at
                        else ""
                    )
                with ac2:
                    if l.description:
                        st.caption(l.description)
                with ac3:
                    if st.button("↩ Désarchiver",
                                 key=f"unarch_{l.id}",
                                 use_container_width=True):
                        unarchive_list(l.id)
                        st.cache_data.clear()
                        st.rerun()
                with ac4:
                    confirm_key = f"confirm_del_arch_{l.id}"
                    if st.session_state.get(confirm_key):
                        if st.button("⚠️ Supprimer ?",
                                     key=f"confirm_arch_{l.id}",
                                     type="primary",
                                     use_container_width=True):
                            delete_list(l.id)
                            st.session_state.pop(confirm_key, None)
                            st.cache_data.clear()
                            st.rerun()
                    else:
                        if st.button("🗑", key=f"del_arch_{l.id}",
                                     use_container_width=True):
                            st.session_state[confirm_key] = True
                            st.rerun()


def render_custom_lists_tab(df: pd.DataFrame) -> None:
    """Simplified Favoris-only tab.

    Custom lists / list comparison / list merge / CSV import are retired
    — too much complexity for the value they provided. This tab is now
    just the user's favorited exhibitors, with a simple table and CSV /
    XLSX download.

    Add to favorites : click the ⭐ checkbox in any Companies-tab row
    (or from a fiche detail).
    """
    st.markdown(
        '<div class="section-title">⭐ Mes favoris</div>',
        unsafe_allow_html=True,
    )
    if "is_favorite" not in df.columns:
        st.warning("La colonne `is_favorite` est absente du dataset.")
        return

    fav_df = df[df["is_favorite"] == True].copy()  # noqa: E712
    n = len(fav_df)
    st.caption(
        f"**{n} société(s) en favori** · "
        "Pour ajouter une société, coche **⭐** sur sa ligne dans l'onglet "
        "Companies."
    )

    if n == 0:
        st.info(
            "Aucun favori pour le moment. Va dans l'onglet **🏢 Companies**, "
            "coche la case **⭐** sur les lignes qui t'intéressent — elles "
            "apparaîtront ici."
        )
        return

    # ----- Bulk un-favorite ----------------------------------------------
    if st.button(f"★ Retirer les {n} favoris", key="favs_clear_all"):
        from app.database import session_scope as _ss
        with _ss() as s:
            for eid in fav_df["account_id"].str.replace(
                "ESY26-", "", regex=False
            ).astype(int):
                exh = s.get(Exhibitor, int(eid))
                if exh and exh.is_favorite:
                    exh.is_favorite = False
            s.commit()
        st.cache_data.clear()
        st.toast(f"{n} société(s) retirée(s) des favoris.", icon="⭐")
        st.rerun()

    # ----- Table (read-only, sorted by name) -----------------------------
    detail_id, _bulk = render_table(
        fav_df.sort_values("account_name"),
        total_rows=n,
        key_prefix="favorites",
    )
    if detail_id is not None:
        render_detail(detail_id)

    # ----- Direct download -----------------------------------------------
    st.divider()
    st.markdown("**⬇ Téléchargement**")
    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        st.download_button(
            f"⬇ CSV ({n})",
            data=fav_df.to_csv(index=False).encode("utf-8"),
            file_name=f"favoris_{datetime.utcnow():%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with c2:
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            fav_df.to_excel(w, index=False, sheet_name="Favoris")
        buf.seek(0)
        st.download_button(
            f"⬇ XLSX ({n})",
            data=buf,
            file_name=f"favoris_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Exports tab
# ---------------------------------------------------------------------------


def render_exports_tab(filtered: pd.DataFrame) -> None:
    st.markdown('<div class="section-title">Exports</div>', unsafe_allow_html=True)
    st.caption(
        "Les exports tiennent compte des filtres actuels (sidebar). Les exports "
        "sur disque arrivent dans `data/exports/`."
    )

    # ============================================================
    # 🎯 LIVRABLE EUROSATORY 2026 — what we sell to customers
    # ============================================================
    # The XLSX is generated by the rule-based + manual-overrides + taxonomy
    # pipeline. It has 2 sheets, autofilter, color-coded scores. We just
    # surface it as a download button.
    st.markdown("### 🎯 Livrable commercial LeadForges")
    st.caption(
        "Le fichier vendu aux clients. 2580 sociétés, 15 colonnes, "
        "autofilter Excel activé sur chaque colonne, sociétés triées "
        "par score décroissant. Catégories canoniques pour le filtrage "
        "(75 produits · 23 services · 31 technos · 5 cibles)."
    )
    from pathlib import Path
    xlsx_path = Path("data/exports/Eurosatory_2026_targeting.xlsx")
    json_path = Path("data/exports/targeting_profiles_final.json")
    csv_path = Path("data/exports/targeting_profiles_final.csv")

    cols_premium = st.columns(3)
    with cols_premium[0]:
        if xlsx_path.exists():
            st.download_button(
                "⬇ XLSX livrable (recommandé)",
                data=xlsx_path.read_bytes(),
                file_name=xlsx_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary",
                help="Le fichier vendu. 2 onglets : main + long-tail à retraiter.",
            )
            st.caption(
                f"📦 {xlsx_path.stat().st_size//1024} ko · "
                f"généré dernièrement par le pipeline."
            )
        else:
            st.warning(
                f"Manquant : {xlsx_path}.  \n"
                "Lance `.venv/bin/python scripts/export_xlsx.py` pour le régénérer."
            )
    with cols_premium[1]:
        if csv_path.exists():
            st.download_button(
                "⬇ CSV livrable (import CRM)",
                data=csv_path.read_bytes(),
                file_name=csv_path.name,
                mime="text/csv",
                use_container_width=True,
                help="Format plat — colonnes flat, prêtes pour import "
                "Salesforce / HubSpot / Dynamics / Airtable.",
            )
            st.caption(f"📦 {csv_path.stat().st_size//1024} ko")
        else:
            st.warning(f"Manquant : {csv_path}")
    with cols_premium[2]:
        if json_path.exists():
            st.download_button(
                "⬇ JSON (intégration / API)",
                data=json_path.read_bytes(),
                file_name=json_path.name,
                mime="application/json",
                use_container_width=True,
                help="Format hiérarchique — listes Python natives "
                "(produits, catégories, etc.).",
            )
            st.caption(f"📦 {json_path.stat().st_size//1024} ko")
        else:
            st.warning(f"Manquant : {json_path}")

    # === Companion PDFs (the 2 sales documents accompanying the XLSX) ===
    st.markdown("**📕 PDFs d'accompagnement commercial**")
    pdf_dico = Path("data/exports/Eurosatory_2026_dictionnaire.pdf")
    pdf_uc = Path("data/exports/Eurosatory_2026_use_cases.pdf")
    pdf_cols = st.columns(2)
    with pdf_cols[0]:
        if pdf_dico.exists():
            st.download_button(
                "⬇ Dictionnaire des données (PDF)",
                data=pdf_dico.read_bytes(),
                file_name=pdf_dico.name,
                mime="application/pdf",
                use_container_width=True,
                help="Document de référence livré au client : "
                "méthodologie, chaque colonne expliquée, formule "
                "du score, mode d'emploi des filtres Excel.",
            )
            st.caption(f"📦 {pdf_dico.stat().st_size//1024} ko")
        else:
            st.warning(
                f"Manquant : `{pdf_dico.name}`. Lance "
                "`.venv/bin/python scripts/generate_data_dictionary_pdf.py`"
            )
    with pdf_cols[1]:
        if pdf_uc.exists():
            st.download_button(
                "⬇ 10 use-cases commerciaux (PDF)",
                data=pdf_uc.read_bytes(),
                file_name=pdf_uc.name,
                mime="application/pdf",
                use_container_width=True,
                help="10 questions commerciales typiques avec les "
                "filtres exacts à appliquer + 3 sociétés exemples + "
                "angle commercial.",
            )
            st.caption(f"📦 {pdf_uc.stat().st_size//1024} ko")
        else:
            st.warning(
                f"Manquant : `{pdf_uc.name}`. Lance "
                "`.venv/bin/python scripts/generate_use_cases_pdf.py`"
            )

    if st.button("🔄 Régénérer le livrable maintenant",
                 help="Relance pipeline complet : rule-based + merge manual + "
                 "categories + XLSX + 2 PDFs companion. ~10 sec."):
        import subprocess
        steps = [
            ("Run rule-based sur 2580", "scripts/run_targeting_profiles.py"),
            ("Merge manual overrides", "scripts/merge_manual_overrides.py"),
            ("Normalize categories",  "scripts/normalize_categories.py"),
            ("Export XLSX final",     "scripts/export_xlsx.py"),
            ("PDF dictionnaire",      "scripts/generate_data_dictionary_pdf.py"),
            ("PDF use-cases",         "scripts/generate_use_cases_pdf.py"),
        ]
        progress = st.progress(0, text="Démarrage…")
        for i, (label, script) in enumerate(steps, start=1):
            progress.progress((i-1)/len(steps), text=f"⏳ {label}…")
            res = subprocess.run(
                [".venv/bin/python", script],
                capture_output=True, text=True, cwd=".",
            )
            if res.returncode != 0:
                progress.empty()
                st.error(f"Échec sur **{label}** :\n```\n{res.stderr[-500:]}\n```")
                return
        progress.progress(1.0, text="✅ Régénération terminée")
        st.cache_data.clear()
        st.success("Livrable régénéré. Recharge la page pour voir les nouveaux scores.")
        st.rerun()

    st.markdown("---")
    st.markdown("**Exports vers CRM (legacy)**")
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**Pour Microsoft Dynamics**")
        if st.button("Exporter CSV Dynamics", use_container_width=True):
            p = export_dynamics_csv()
            st.success(f"écrit : {p}")
        st.markdown("**Pour Salesforce**")
        if st.button("Exporter CSV Salesforce", use_container_width=True):
            p = export_salesforce_csv()
            st.success(f"écrit : {p}")
    with cols[1]:
        st.markdown("**Pour HubSpot**")
        if st.button("Exporter CSV HubSpot", use_container_width=True):
            p = export_hubspot_csv()
            st.success(f"écrit : {p}")
        st.markdown("**Pour Airtable**")
        if st.button("Exporter CSV Airtable", use_container_width=True):
            p = export_airtable_csv()
            st.success(f"écrit : {p}")
    with cols[2]:
        st.markdown("**Sales prospecting (lean)**")
        if st.button("Exporter CSV prospection", use_container_width=True):
            p = export_prospecting_csv()
            st.success(f"écrit : {p}")
        st.markdown("**Full database**")
        if st.button("Exporter CSV complet", use_container_width=True):
            p = export_full_csv()
            st.success(f"écrit : {p}")
        if st.button("Exporter XLSX complet", use_container_width=True):
            p = export_full_xlsx()
            st.success(f"écrit : {p}")

    st.markdown("---")
    st.markdown("**Téléchargement direct (résultat filtré)**")
    cols = st.columns(2)
    with cols[0]:
        st.download_button(
            "⬇ CSV (filtres appliqués)",
            data=filtered.to_csv(index=False).encode("utf-8"),
            file_name=f"crm_filtered_{datetime.utcnow():%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with cols[1]:
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            filtered.to_excel(w, index=False, sheet_name="Filtered")
        buf.seek(0)
        st.download_button(
            "⬇ XLSX (filtres appliqués)", data=buf,
            file_name=f"crm_filtered_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pipeline Kanban view
# ---------------------------------------------------------------------------

PIPELINE_STAGES: list[tuple[str, str, str]] = [
    # (lead_status, internal_status, badge_color_class)
    ("New",                  "new",            "kanban-new"),
    ("Qualified",            "qualified",      "kanban-qual"),
    ("To contact",           "to_contact",     "kanban-toc"),
    ("Contacted",            "contacted",      "kanban-done"),
    ("Not relevant",         "not_relevant",   "kanban-skip"),
]
PIPELINE_NEXT_INTERNAL: dict[str, str] = {
    "new": "qualified",
    "qualified": "to_contact",
    "to_contact": "contacted",
    "contacted": "contacted",  # terminal
    "not_relevant": "not_relevant",
}
PIPELINE_PREV_INTERNAL: dict[str, str] = {
    "qualified": "new",
    "to_contact": "qualified",
    "contacted": "to_contact",
    "not_relevant": "new",
    "new": "new",  # already first
}

PIPELINE_CSS = """
<style>
.kanban-col {
    background: #F4F6F8; border-radius: 6px; padding: 0.6rem;
    border-top: 3px solid #0B2E4A;
    height: 100%;
}
.kanban-col h4 {
    margin: 0 0 0.5rem; font-size: 0.85rem;
    color: #071A33; text-transform: uppercase; letter-spacing: 0.06em;
}
.kanban-card {
    background: white; border-radius: 4px; padding: 0.45rem 0.6rem;
    margin-bottom: 0.4rem; border-left: 3px solid #2F5D7C;
    font-size: 0.85rem;
}
.kanban-card .name { font-weight: 600; color: #071A33; }
.kanban-card .meta { color: #4A5158; font-size: 0.78rem; }
.kanban-col.priority-targeting { border-top-color: #C1121F; }
.kanban-col.qualification     { border-top-color: #D97706; }
.kanban-col.watchlist         { border-top-color: #2F5D7C; }
.kanban-col.low               { border-top-color: #94A3B8; }
</style>
"""


def _set_status(exhibitor_id: int, internal: str) -> None:
    from app.database import session_scope
    with session_scope() as s:
        exh = s.get(Exhibitor, exhibitor_id)
        if exh is not None:
            exh.status = internal


def render_pipeline_tab(df: pd.DataFrame) -> None:
    st.markdown(
        '<div class="section-title">🧭 Pipeline outreach pré-salon</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Vue Kanban par **lead status**. Filtres pré-appliqués : tu peux "
        "limiter par owner, priorité, segment depuis la sidebar. Boutons "
        "« Avancer / Reculer » sur chaque carte."
    )
    st.markdown(PIPELINE_CSS, unsafe_allow_html=True)

    if df.empty:
        st.info("Aucune société à afficher avec les filtres actuels.")
        return

    # quick filters specific to the kanban
    c1, c2, c3 = st.columns(3)
    with c1:
        per_col_limit = st.slider("Cartes par colonne", 5, 100, 30, step=5)
    with c2:
        owner_filter = st.text_input(
            "Owner (filtrage exact)", key="kanban_owner",
            placeholder="ex: alice@…",
        )
    with c3:
        only_a = st.checkbox("Priorité A+ / A uniquement", value=True)

    # apply pipeline-local filters
    pf = df.copy()
    if owner_filter:
        pf = pf[pf["owner"].fillna("") == owner_filter]
    if only_a:
        pf = pf[pf["priority_level"].isin(["A+", "A"])]
    st.caption(f"**{len(pf)} sociétés** dans le pipeline (filtres appliqués)")

    cols = st.columns(len(PIPELINE_STAGES))
    for i, (label, internal, _css_cls) in enumerate(PIPELINE_STAGES):
        sub = pf[pf["lead_status"] == label].copy()
        sub = sub.sort_values("lead_score", ascending=False, na_position="last").head(per_col_limit)
        with cols[i]:
            st.markdown(
                f"<div class='kanban-col'><h4>{label} · {len(pf[pf['lead_status'] == label])}</h4></div>",
                unsafe_allow_html=True,
            )
            for _, r in sub.iterrows():
                exh_id = int(str(r["account_id"]).replace("ESY26-", ""))
                priority = r["priority_level"] or "D"
                badge = _badge(priority, PRIORITY_BADGE)
                core = (r.get("core_business") or "—")[:60]
                country = r.get("country") or "—"
                score = r.get("lead_score")
                st.markdown(
                    f"<div class='kanban-card'>"
                    f"<div class='name'>{badge} &nbsp; {r['account_name']}</div>"
                    f"<div class='meta'>{country} · {core}"
                    + (f" · {score:.0f}/100" if score is not None else "")
                    + "</div>"
                    "</div>",
                    unsafe_allow_html=True,
                )
                bcols = st.columns([1, 1, 2])
                with bcols[0]:
                    if st.button("◀", key=f"k_back_{exh_id}_{internal}",
                                 help="Reculer d'une colonne"):
                        _set_status(exh_id, PIPELINE_PREV_INTERNAL.get(internal, internal))
                        st.cache_data.clear()
                        st.rerun()
                with bcols[1]:
                    if st.button("▶", key=f"k_fwd_{exh_id}_{internal}",
                                 help="Avancer d'une colonne"):
                        _set_status(exh_id, PIPELINE_NEXT_INTERNAL.get(internal, internal))
                        st.cache_data.clear()
                        st.rerun()
                with bcols[2]:
                    if st.button("Skip", key=f"k_skip_{exh_id}_{internal}",
                                 help="Marquer Not relevant"):
                        _set_status(exh_id, "not_relevant")
                        st.cache_data.clear()
                        st.rerun()

    # Bulk reset button
    st.divider()
    if st.button("⚠️ Reset all filtered companies to 'New'", type="secondary"):
        ids = [int(str(a).replace("ESY26-", "")) for a in pf["account_id"]]
        from app.database import session_scope
        with session_scope() as s:
            for eid in ids:
                e = s.get(Exhibitor, eid)
                if e:
                    e.status = "new"
        st.cache_data.clear()
        st.success(f"{len(ids)} sociétés repassées en 'New'.")
        st.rerun()


# ---------------------------------------------------------------------------
# Data Quality dashboard
# ---------------------------------------------------------------------------


@st.cache_data(ttl=120)
def _load_quality_report() -> dict:
    from app.pipelines.report import build_report
    return build_report()


def _bar_distribution(d: dict, label: str, *, sort_by_value: bool = True, top: int = 10) -> None:
    if not d:
        st.info(f"Pas de données pour {label}")
        return
    items = list(d.items())
    if sort_by_value:
        items.sort(key=lambda kv: -(kv[1] or 0))
    items = [(k, v) for k, v in items if v]
    items = items[:top]
    df_chart = pd.DataFrame(items, columns=[label, "Count"]).set_index(label)
    st.bar_chart(df_chart, height=280)


def render_data_quality_tab(df: pd.DataFrame) -> None:
    """Buyer-facing data quality view.

    The audience is a defense commercial evaluating whether to license
    the database — they want to know **volume**, **contactability**,
    **B2B targeting coverage**, **geographic reach** and **sector reach**.
    Internal pipeline metrics (crawl errors, extraction method, field-level
    confidence, manual-review queue, dedupe clusters) are intentionally
    omitted — they have no commercial value to a buyer.
    """
    st.markdown(
        '<div class="section-title">📊 Couverture de la base</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Vue synthétique de ce que contient la base LeadForges. "
        "Ces chiffres répondent à : *« Si j'achète, qu'est-ce que je récupère ? »*"
    )

    rep = _load_quality_report()
    n_total = max(rep.get("total_exhibitors", 0) or 0, 1)
    pct = rep.get("completeness_pct", {})

    # ---- 1. Volume header ---------------------------------------------------
    n_targeting = int(df["activity_1liner"].fillna("").astype(bool).sum()) \
        if "activity_1liner" in df.columns else 0
    try:
        from app.attendance.signals import signals_dataframe
        sig_df = signals_dataframe(include_duplicates=False)
        n_signals = len(sig_df)
    except Exception:  # noqa: BLE001
        n_signals = 0

    st.markdown("**Volume**")
    c1, c2, c3 = st.columns(3)
    with c1:
        _kpi("Sociétés répertoriées", rep.get("total_exhibitors", 0))
    with c2:
        _kpi("Profils de ciblage qualifiés", n_targeting, css_class="success")
    with c3:
        _kpi("Signaux de présence détectés", n_signals)

    st.divider()

    # ---- 2. Contactabilité --------------------------------------------------
    st.markdown("**Contactabilité — quoi sera utilisable pour prospecter**")
    cols = st.columns(4)
    for i, (label, key) in enumerate([
        ("Site web", "website"),
        ("Email", "contact_email"),
        ("Téléphone", "phone"),
        ("LinkedIn", "linkedin"),
    ]):
        with cols[i]:
            pct_v = float(pct.get(key, 0) or 0)
            st.markdown(f"**{label}** — {pct_v:.0f}%")
            st.progress(min(int(pct_v), 100) / 100)

    st.divider()

    # ---- 3. Couverture intelligence commerciale -----------------------------
    st.markdown(
        "**Intelligence commerciale — qualification B2B exploitable**"
    )

    def _pct_filled(col: str) -> float:
        if col not in df.columns:
            return 0.0
        n_ok = int(df[col].fillna("").astype(bool).sum())
        return round(n_ok * 100 / n_total, 1)

    cols = st.columns(4)
    metrics = [
        ("Activité (1 ligne)", "activity_1liner",
         "Phrase d'accroche normalisée par société."),
        ("Catégorie produit", "products_categories",
         "Au moins une catégorie produit canonique."),
        ("Cible commerciale", "target_buyers",
         "Au moins un type d'acheteur identifié (MoD, primes, sécurité civile…)."),
        ("Pourquoi cibler", "why_target",
         "Phrase contextuelle d'angle commercial (acheteur / compétiteur / partenaire)."),
    ]
    for i, (label, col, helptxt) in enumerate(metrics):
        with cols[i]:
            v = _pct_filled(col)
            st.markdown(f"**{label}** — {v:.0f}%", help=helptxt)
            st.progress(min(int(v), 100) / 100)

    st.divider()

    # ---- 4. Géographie ------------------------------------------------------
    st.markdown("**Couverture géographique** — top 15 pays")
    _bar_distribution(rep["top_countries"], "Pays", sort_by_value=True, top=15)

    st.divider()

    # ---- 5. Secteurs couverts -----------------------------------------------
    st.markdown("**Secteurs couverts** — top 15 catégories produit")
    cat_counts: dict[str, int] = {}
    if "products_categories" in df.columns:
        from collections import Counter as _C
        c = _C()
        for v in df["products_categories"].dropna():
            for piece in str(v).split("·"):
                p = piece.strip()
                if p and not p.lower().startswith("autre"):
                    c[p] += 1
        cat_counts = dict(c.most_common(20))
    if cat_counts:
        _bar_distribution(cat_counts, "Catégorie", sort_by_value=True, top=15)
    else:
        st.info("Catégories non encore agrégées.")


def _is_demo_mode() -> bool:
    """Return True for THIS request when ``?demo=1`` is in the URL.

    CRITICAL : reads ``st.query_params`` PER CALL — never an env var.
    The Streamlit Cloud process is shared by all users, so an env var
    would leak demo restrictions to paying buyers. This implementation
    is fully session-scoped : a demo visitor in tab A and a paying
    buyer in tab B are independent.
    """
    try:
        qp = dict(st.query_params)
    except Exception:  # noqa: BLE001
        try:
            qp = {k: v[0] if isinstance(v, list) else v
                  for k, v in st.experimental_get_query_params().items()}
        except Exception:  # noqa: BLE001
            qp = {}
    val = str(qp.get("demo", "")).strip().lower()
    return val in ("1", "true", "yes", "on")


def _render_demo_banner() -> None:
    """Persistent banner shown at the top of every page in demo mode."""
    from app.ui.i18n import is_en
    if is_en():
        label = "DEMO VERSION"
        subtitle = "50 exhibitors × 50 signals · representative sample"
        cta = "Access the full database (€2,000) →"
        href = "https://leadforges-eight.vercel.app/en"
    else:
        label = "VERSION DÉMO"
        subtitle = "50 exposants × 50 signaux · échantillon représentatif"
        cta = "Accéder à la base complète (2 000 €) →"
        href = "https://leadforges-eight.vercel.app"
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(90deg, #0A0A0A 0%, #1A1A1A 100%);
            color: #FAFAFA;
            padding: 10px 20px;
            margin: -1rem -1rem 1rem -1rem;
            border-bottom: 2px solid #0B2E4A;
            font-family: 'Inter', system-ui, sans-serif;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        ">
          <div style="font-size: 14px;">
            <strong style="font-weight: 700;">{label}</strong>
            <span style="opacity: 0.7; margin-left: 12px;">{subtitle}</span>
          </div>
          <div>
            <a href="{href}" target="_blank"
               style="background: #FAFAFA; color: #0A0A0A;
                      padding: 6px 14px; border-radius: 6px;
                      text-decoration: none; font-weight: 600;
                      font-size: 13px;">{cta}</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _check_password() -> bool:
    """Multi-buyer authentication gate.

    Delegates to :mod:`app.ui.auth`. Returns ``True`` when access is
    granted (either a logged-in buyer with non-expired access, or no
    gate configured for local dev, or demo mode). Otherwise renders the
    login screen and returns ``False``.
    """
    if _is_demo_mode():
        return True  # demo bypasses auth — that's the whole point
    from app.ui.auth import is_authenticated, render_login_screen
    if is_authenticated():
        return True
    render_login_screen()
    return False


def main() -> None:
    if not _check_password():
        st.stop()
    if _is_demo_mode():
        _render_demo_banner()
    else:
        from app.ui.auth import render_access_banner
        render_access_banner()
    df = load_crm()
    # ── DEMO HARD LIMIT ──────────────────────────────────────────────
    if _is_demo_mode():
        df = df.head(50).copy()
    # ── i18n : swap FR data fields with EN when ?lang=en ─────────────
    df = _localize_df(df)
    # Overlay session-scoped favorites on top of the cached dataframe so
    # each buyer sees their own ⭐ selection without polluting the cache.
    df = _inject_session_favorites(df)
    render_header(df)

    if df.empty:
        st.warning(
            "Aucune donnée. Lancez :  \n"
            "`python -m app.cli scrape && python -m app.cli enrich && "
            "python -m app.cli intelligence --limit 200`"
        )
        return

    filters = render_sidebar(df)
    filtered = apply_filters(df, **filters)

    from app.ui.i18n import is_en
    if is_en():
        tab_labels = ["🏢 Companies", "⭐ Favorites",
                      "📡 Attendance Signals", "Target lists", "⬇ Exports"]
    else:
        tab_labels = ["🏢 Companies", "⭐ Favoris",
                      "📡 Attendance Signals", "Listes ciblées", "⬇ Exports"]
    tab_companies, tab_lists, tab_signals, tab_target_lists, tab_exports = st.tabs(
        tab_labels
    )

    with tab_companies:
        render_recently_viewed()
        # 8-bucket "Type d'entreprise" quick-filter chips (replaces the
        # legacy supply-chain-tier chips).
        render_company_type_chips(filtered)
        render_active_filter_chips(filters, filtered)
        detail_id, bulk_ids = render_table(filtered, total_rows=len(df))
        # Recently-viewed click overrides the table selection
        jump_id = st.session_state.pop("jump_to_id", None)
        if jump_id is not None:
            render_detail(int(jump_id))
            return
        if detail_id is not None:
            render_detail(detail_id)
        elif 2 <= len(bulk_ids) <= 4:
            mode = st.radio(
                f"{len(bulk_ids)} sociétés sélectionnées — afficher :",
                ["⚖️ Comparaison côte-à-côte", "⚡ Bulk actions"],
                horizontal=True, key="multi_select_mode",
            )
            if mode.startswith("⚖️"):
                render_comparison(bulk_ids)
            else:
                render_bulk_actions(bulk_ids)
        elif bulk_ids:
            render_bulk_actions(bulk_ids)

    with tab_lists:
        render_custom_lists_tab(filtered)

    with tab_signals:
        render_attendance_signals_tab()

    with tab_target_lists:
        from app.ui.target_lists import render_target_lists_tab
        render_target_lists_tab(df)

    with tab_exports:
        render_companies_topbar(df, key_prefix="topbar_exports")
        render_kpis(filtered)
        render_exports_tab(filtered)


# ---------------------------------------------------------------------------
# Attendance Signals tab
# ---------------------------------------------------------------------------


@st.cache_data(ttl=60)
def _load_signals() -> pd.DataFrame:
    """Load every signal (incl. duplicates) — the page is meant to surface
    *everything we sourced*, not just canonical rows. Duplicate flagging
    is still useful for back-end exports; the UI just doesn't hide them.
    """
    return signals_dataframe(include_duplicates=True)


def _render_attendance_filters(df: pd.DataFrame) -> dict:
    """Minimal filter row : Année + Recherche + toggle exposants.

    The page is meant to surface POTENTIAL VISITORS — by default we hide
    signals tied to companies already in the Exhibitors catalog (those
    are covered by the Companies tab). Operators can flip the toggle to
    see them too.
    """
    from app.crm.normalizers import GEOGRAPHIC_ZONES as _ZONES
    c1, c2, c3, c4, c5, c6 = st.columns([0.8, 1.0, 1.2, 1.2, 1.8, 1.0])
    with c1:
        years = st.multiselect(
            "Année",
            sorted([int(y) for y in df["edition_year"].dropna().unique()]),
            key="att_years",
        )
    with c2:
        zones = st.multiselect(
            "Zone",
            list(_ZONES),
            key="att_zones",
            help="Europe · Amérique du Nord · Asie · Amérique du Sud · "
            "Autre (Afrique, Océanie, autres).",
        )
    with c3:
        # Pays-level filter — more granular than Zone. Builds the list of
        # available countries from the dataframe so we never offer a
        # country that has 0 signals.
        if "country" in df.columns:
            available_countries = sorted(
                {c for c in df["country"].dropna().unique()
                 if isinstance(c, str) and c.strip()}
            )
        else:
            available_countries = []
        countries = st.multiselect(
            "Pays",
            available_countries,
            key="att_countries",
            help="Filtre par pays exact (basé sur le pays détecté du "
            "signal). Pour un filtre plus large, utilise Zone.",
        )
    with c4:
        company_types = st.multiselect(
            "Type d'entreprise",
            ALLOWED_COMPANY_TYPES,
            key="att_company_types",
            help="Taxonomie fermée 8 valeurs — calculée depuis les "
            "capabilities de la société. Filtre les signaux dont la "
            "société matche un de ces buckets.",
        )
    with c5:
        search_text = st.text_input(
            "🔎 Recherche libre (nom personne / société / texte)",
            key="att_search",
            placeholder="Ex: Thales, John Doe, cyber, France…",
        )
    with c6:
        include_exhibitors = st.checkbox(
            "Inclure exposants",
            value=False,
            key="att_include_exhibitors",
            help="Par défaut, les signaux dont la société matche un "
            "exposant catalogue sont masqués (ils sont déjà dans la "
            "page Companies). Coche pour les inclure aussi.",
        )

    return {
        "years": years,
        "zones": zones,
        "countries": countries,
        "company_types": company_types,
        # Source / platform filter retired — sources are confidential
        # (proprietary intel scraping). Only LinkedIn signals show their
        # source publicly in the table.
        "platforms": [],
        "search_text": search_text,
        "exclude_known_exhibitors": not include_exhibitors,
    }


_ATT_LIST_COLUMNS: list[str] = [
    "person_name", "person_role",
    "company_name", "matched_exhibitor", "country",
    "ads_product_categories",
    "derived_email", "derived_phone", "derived_linkedin",
    "edition_year", "source_platform", "source_url",
    # Removed columns : ads_supply_chain_tier (Niveau), presence_score
    # (Score), ads_capabilities_count (# Tags) — internal-only metrics
    # not commercially useful in the client-facing list.
]


def _render_attendance_table(df: pd.DataFrame) -> tuple[int | None, list[int]]:
    """Companies-style list of attendance signals.

    Each row = one person or company potentially attending an Eurosatory
    edition, with the **source URL** (LinkedIn post / press release /
    corporate page) clickable to justify the entry. Click 1 row → fiche
    détaillée. Click 2+ rows → bulk actions.
    """
    if df.empty:
        st.info(
            "Aucun signal pour le moment. Utilise le **📋 Bulk paste** "
            "tout en haut pour importer rapidement, ou le **🤖 Scraper "
            "auto** pour scanner les sites des exposants."
        )
        return None, []

    # Sort + page-size + page-navigation controls
    sort_col, limit_col, page_col = st.columns([2, 1, 1.5])
    with sort_col:
        sort_label = st.selectbox(
            "Trier par",
            [
                "Date capture (récent → ancien)",
                "Date capture (ancien → récent)",
                "Nom personne", "Nom société",
                "Année (récente → ancienne)",
            ],
            index=0,
            key="att_list_sort",
            label_visibility="collapsed",
        )
    # The selectbox offers fixed bucket sizes + a "Tout" option that
    # disables pagination and renders the whole filtered set. We add a
    # ``Tout (N)`` label so the user always knows the full count.
    _total_n = len(df)
    _SIZE_OPTIONS: list[int | str] = [50, 100, 200, 500, 1000, 5000, "Tout"]
    with limit_col:
        size_choice = st.selectbox(
            "Lignes",
            _SIZE_OPTIONS,
            index=2,
            key="att_list_page_size",
            format_func=lambda v: (
                f"Tout ({_total_n})" if v == "Tout" else str(v)
            ),
            label_visibility="collapsed",
        )

    sort_input = df.copy()
    if "Date capture" in sort_label:
        ascending = "ancien → récent" in sort_label
        sort_input = sort_input.sort_values(
            "first_seen_at", ascending=ascending, na_position="last",
        )
    elif sort_label == "Nom personne":
        sort_input = sort_input.sort_values("person_name", na_position="last")
    elif sort_label == "Nom société":
        sort_input = sort_input.sort_values("company_name", na_position="last")
    elif "Année" in sort_label:
        sort_input = sort_input.sort_values("edition_year", ascending=False)

    # Pagination : when the user picks a finite page size and there are
    # more rows than fit on one page, render ◀ Prev / Next ▶ buttons.
    if size_choice == "Tout":
        page_size = _total_n
        page = 0
        total_pages = 1
    else:
        page_size = int(size_choice)
        total_pages = max(1, (_total_n + page_size - 1) // page_size)
        page_key = "att_list_page"
        page = int(st.session_state.get(page_key, 0))
        page = max(0, min(page, total_pages - 1))
        with page_col:
            pc1, pc2, pc3 = st.columns([1, 1.6, 1])
            with pc1:
                if st.button(
                    "◀", key="att_page_prev",
                    disabled=page <= 0,
                    use_container_width=True,
                ):
                    st.session_state[page_key] = max(0, page - 1)
                    st.rerun()
            with pc2:
                st.markdown(
                    f"<div style='text-align:center;line-height:2.3rem;"
                    f"font-size:0.85rem;'>Page <b>{page + 1}</b> / "
                    f"{total_pages}</div>",
                    unsafe_allow_html=True,
                )
            with pc3:
                if st.button(
                    "▶", key="att_page_next",
                    disabled=page >= total_pages - 1,
                    use_container_width=True,
                ):
                    st.session_state[page_key] = min(total_pages - 1, page + 1)
                    st.rerun()

    sort_input = sort_input.iloc[page * page_size : (page + 1) * page_size]

    cols_present = [c for c in _ATT_LIST_COLUMNS if c in sort_input.columns]
    show = sort_input[cols_present].copy()
    show.insert(0, "_id", sort_input["id"].astype(int))

    # ---- Confidentiality : mask the proprietary source attribution
    # (ADS Group UK, GICAT, Lemlist, etc.) — the client must NOT see
    # which intel platform the data came from.
    # The ``source_url`` column is treated more permissively : a
    # ``linkedin.com`` URL is a *public* link and stays visible even
    # when it was harvested via a proprietary platform (the client
    # benefits from one-click access to the person's LinkedIn profile,
    # without learning where we sourced the lead).
    if "source_platform" in show.columns:
        # Mask the platform attribution unless it IS LinkedIn (rare
        # native LinkedIn signals scraped directly from the platform).
        is_linkedin_platform = (
            show["source_platform"].fillna("")
            .str.lower().str.contains("linkedin", regex=False)
        )
        show.loc[~is_linkedin_platform, "source_platform"] = ""
        if "source_url" in show.columns:
            # Mask source_url ONLY when it doesn't point to LinkedIn.
            url_is_linkedin = (
                show["source_url"].fillna("")
                .str.lower().str.contains("linkedin.com", regex=False)
            )
            show.loc[~url_is_linkedin, "source_url"] = ""

    _pagination_caption = (
        f"Page {page + 1}/{total_pages} · " if total_pages > 1 else ""
    )
    st.caption(
        f"{_pagination_caption}**{len(show)} affichés** / {len(df)} filtrés "
        f"· trié par **{sort_label}** · 💡 cliquer sur une ligne ouvre la "
        f"fiche détaillée. La colonne **Source** n'est affichée que pour "
        f"les signaux LinkedIn (les autres sources sont confidentielles)."
    )

    event = st.dataframe(
        show,
        use_container_width=True,
        height=520,
        on_select="rerun",
        selection_mode="multi-row",
        key="att_signals_dataframe",
        column_config={
            "_id": None,
            "person_name": st.column_config.TextColumn(
                "Personne", width="medium",
                help="Nom de la personne potentiellement présente "
                "(quand identifiée).",
            ),
            "person_role": st.column_config.TextColumn(
                "Rôle", width="medium",
                help="Intitulé de poste tel que repéré dans la source.",
            ),
            "company_name": st.column_config.TextColumn(
                "Société", width="medium",
            ),
            "matched_exhibitor": st.column_config.TextColumn(
                "🛡 Exposant", width="medium",
                help="Société catalogue salon matchée (vide si la "
                "société du contact n'est pas exposante). Pour ouvrir la "
                "fiche, clique sur la ligne puis sur « 📂 Fiche société ».",
            ),
            "country": st.column_config.TextColumn("Pays", width="small"),
            "derived_email": st.column_config.TextColumn(
                "✉ Email", width="medium",
                help="Email direct extrait de la source (GICAT directory, "
                "LinkedIn, etc.). Cliquer = sélectionner pour copier.",
            ),
            "derived_phone": st.column_config.TextColumn(
                "📞 Tel", width="small",
                help="Téléphone direct extrait de la source.",
            ),
            "derived_linkedin": st.column_config.LinkColumn(
                "🔗 LinkedIn", width="small",
                display_text=r".*linkedin\.com/(?:in|company)/([^/?]+).*",
                help="Profil LinkedIn (personne ou société).",
            ),
            "edition_year": st.column_config.NumberColumn(
                "Édition", width="small",
            ),
            "signal_type": st.column_config.TextColumn(
                "Type de signal", width="medium",
                help="company_announcement / personal_linkedin_post / "
                "press_release / event_page / etc.",
            ),
            "source_platform": st.column_config.TextColumn(
                "Plateforme", width="small",
            ),
            "ads_supply_chain_tier": st.column_config.TextColumn(
                "Niveau", width="small",
                help="Position dans la pyramide industrielle "
                "(OEM / Tier 1-4 / N/A) — calculée depuis les capabilities "
                "ADS Group UK quand disponibles.",
            ),
            "ads_product_categories": st.column_config.TextColumn(
                "Catégories produits", width="medium",
                help="Catégories produits canoniques dérivées des "
                "capabilities ADS Group (mappées sur nos 75 buckets).",
            ),
            "ads_capabilities_count": st.column_config.NumberColumn(
                "# Tags", width="small",
                help="Nombre de capabilities déclarées par la société "
                "dans le directory ADS Group UK.",
            ),
            "presence_score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%.0f",
                help="Score 0-100 de probabilité de présence.",
            ),
            "source_url": st.column_config.LinkColumn(
                "🔗 Source", width="medium",
                display_text=r"https?://(?:www\.)?([^/]+).*",
                help="Lien direct vers la source publique (post LinkedIn, "
                "communiqué, page événement, etc.) qui justifie ce signal.",
            ),
        },
        hide_index=True,
    )
    rows = event.selection.rows if event and event.selection else []
    if not rows:
        return None, []
    ids = [int(show.iloc[r]["_id"]) for r in rows]
    if len(ids) == 1:
        return ids[0], []
    return None, ids


_SIG_KIND_ICONS = {
    "company_announcement": "🏢",
    "personal_linkedin_post": "👤",
    "official_delegation": "🇺🇳",
    "national_pavilion": "🏛",
    "press_release": "📰",
    "event_page": "🗓",
    "social_post": "💬",
    "media_article": "🗞",
}


def _render_attendance_bulk_paste() -> None:
    """Bulk-paste URLs (e.g. from a Google search) → analyse + import as
    signals in one shot. The fastest enrichment path : the operator
    pastes 5-50 URLs, we fetch each one, extract title + snippet,
    detect "Eurosatory <year>" mentions, and upsert.

    Admin-only : data-curation tool, hidden from buyers.
    """
    from app.ui.auth import is_admin
    if not is_admin():
        return
    with st.expander("📋 Bulk paste — URLs → signaux (méthode rapide)",
                     expanded=True):
        st.caption(
            "Colle ici des URLs trouvées dans Google / LinkedIn / etc. — "
            "**une par ligne**. Le système les fetch en parallèle, "
            "extrait titre + snippet, détecte les mentions du salon "
            "ciblé et crée les signaux automatiquement (dédupliqués). "
            "C'est la voie d'enrichissement la plus rapide : ~10s pour "
            "20 URLs."
        )
        cc1, cc2 = st.columns([1, 4])
        with cc1:
            paste_year = st.number_input(
                "Édition cible",
                min_value=2020, max_value=2030, value=2026, step=1,
                key="bulk_paste_year",
            )
        with cc2:
            urls_raw = st.text_area(
                "URLs (une par ligne)",
                key="bulk_paste_urls",
                height=160,
                placeholder=(
                    "https://www.example.com/news/eurosatory-2026-attendance\n"
                    "https://www.linkedin.com/posts/...\n"
                    "https://www.example.com/press/booth-d042"
                ),
            )

        if st.button("▶ Analyser & importer",
                     key="bulk_paste_run",
                     type="primary"):
            urls = [u.strip() for u in (urls_raw or "").splitlines()
                    if u.strip()]
            if not urls:
                st.warning("Colle au moins une URL.")
                return
            from app.attendance.auto_collect import bulk_paste_signals
            progress = st.progress(0.0, text="Démarrage…")

            def _cb(scanned: int, total: int, hits: int) -> None:
                pct = scanned / total if total else 1.0
                progress.progress(
                    min(pct, 1.0),
                    text=f"Scanné {scanned}/{total} · {hits} importés",
                )

            with st.spinner("Fetch en cours…"):
                result = bulk_paste_signals(
                    urls, target_year=int(paste_year), progress_cb=_cb,
                )
            progress.progress(1.0, text="Terminé.")

            cols = st.columns(4)
            with cols[0]: _kpi("Scanné", result["scanned"])
            with cols[1]: _kpi("➕ Nouveaux", result["new"], css_class="success")
            with cols[2]: _kpi("🔄 Mis à jour", result["updated"])
            with cols[3]: _kpi("⏭ Skip / err",
                               result["skipped"] + result["errors"],
                               css_class="warning"
                               if (result["skipped"] + result["errors"])
                               else "")
            if result["results"]:
                st.markdown("**Détails par URL**")
                df = pd.DataFrame(result["results"])
                st.dataframe(df, use_container_width=True,
                             hide_index=True, height=240)
            st.cache_data.clear()
            st.toast(
                f"📋 +{result['new']} nouveaux signaux importés.",
                icon="📋",
            )


def _render_attendance_auto_collect() -> None:
    """Trigger the automated scrape that scans exhibitor websites for
    "Eurosatory <year>" mentions and emits ``AttendanceSignal`` rows.

    Stays within the existing legal posture (corporate sites only, no
    LinkedIn/X login). LinkedIn / X OSINT remains operator-driven via the
    documented Google queries panel below.

    Admin-only : data-curation tool, hidden from buyers.
    """
    from app.ui.auth import is_admin
    if not is_admin():
        return
    with st.expander("🤖 Scraper auto — sites web exposants → signaux",
                     expanded=False):
        st.caption(
            "Scanne les sites officiels des exposants pour des "
            "mentions du salon ciblé sur leur homepage + pages "
            "/news, /press, /events. Génère des signaux "
            "`signal_type=company_announcement` automatiquement scorés et "
            "dédupliqués. **Ne touche pas LinkedIn / X** (collecte manuelle "
            "via les requêtes Google ci-dessous, posture légale "
            "inchangée)."
        )
        cc1, cc2, cc3 = st.columns([1, 1, 2])
        with cc1:
            target_year = st.number_input(
                "Édition cible",
                min_value=2020, max_value=2030, value=2026, step=1,
                key="auto_collect_year",
            )
        with cc2:
            limit_str = st.text_input(
                "Limite exposants (vide = tous)",
                value="50",
                key="auto_collect_limit",
                help="Commence avec 50 pour tester rapidement (~2-3 min). "
                "Vide pour scanner les 2337 (~30-60 min).",
            )
        with cc3:
            st.caption(
                "💡 Conseille un premier run avec 50 exposants pour valider "
                "le rendu, puis lance sans limite la nuit."
            )

        try:
            limit_val = int(limit_str.strip()) if limit_str.strip() else None
        except ValueError:
            limit_val = None
            st.warning("Limite invalide — interprétée comme 'tous'.")

        if st.button("▶ Lancer le scraping",
                     key="auto_collect_run",
                     type="primary",
                     help="Scan synchrone — la page reste bloquée jusqu'à "
                     "la fin. Pour un run long, lance plutôt en CLI : "
                     "python -m app.cli attendance-auto-collect."):
            from app.attendance.auto_collect import collect_corporate_signals
            progress = st.progress(0.0, text="Démarrage…")
            status_box = st.empty()

            def _cb(scanned: int, total: int, hits: int) -> None:
                pct = scanned / total if total else 1.0
                progress.progress(
                    min(pct, 1.0),
                    text=f"Scanné {scanned}/{total} · {hits} hits",
                )

            with st.spinner("Scraping en cours…"):
                result = collect_corporate_signals(
                    target_year=int(target_year),
                    limit=limit_val,
                    only_with_website=True,
                    progress_cb=_cb,
                )
            progress.progress(1.0, text="Terminé.")
            status_box.success(
                f"✅ Scan terminé — **{result['scanned']} exposants** scannés, "
                f"**{result['hits']} mentions** détectées : "
                f"{result['new']} nouveaux signaux, "
                f"{result['updated']} mis à jour"
                + (f", {result['errors']} erreurs"
                   if result["errors"] else "")
                + "."
            )
            st.cache_data.clear()
            st.toast(
                f"🤖 +{result['new']} signaux auto-collectés", icon="🤖",
            )


def _render_attendance_watchlist() -> None:
    """Watchlist : saved keyword/company/person/country/role watches.

    Each watch shows the count of UNREAD signals (id greater than the
    last_seen_signal_id) plus a button to mark them seen. New watches
    can be added at the bottom of the panel.

    Admin-only : data-curation tool, hidden from buyers (deploy DB is
    read-only — watches couldn't persist anyway).
    """
    from app.ui.auth import is_admin
    if not is_admin():
        return
    with st.expander("🔔 Watchlist (alertes signaux)", expanded=False):
        watches = attend_list_watches()
        if not watches:
            st.caption(
                "Aucune alerte enregistrée. Ajoute-en une ci-dessous : par ex. "
                "« Thales » (kind=company) ou « cyber » (kind=keyword) — "
                "tu auras un compteur de nouveaux signaux à chaque nouvelle "
                "session."
            )

        for w in watches:
            unread = attend_watch_unread(w["id"], limit=10)
            with st.container(border=True):
                hh1, hh2, hh3 = st.columns([4, 1, 1])
                with hh1:
                    badge = (
                        f"<span class='badge' style='background:#0B2E4A;"
                        f"color:white;'>{len(unread)} nouveaux</span>"
                        if unread else
                        "<span class='badge' style='background:#1F7A4D;"
                        "color:white;'>à jour</span>"
                    )
                    st.markdown(
                        f"{badge} &nbsp; <strong>{w['label'] or w['term']}</strong>"
                        f" <span style='color:#6c757d;font-size:0.78rem;'>"
                        f"· kind=<code>{w['kind']}</code> · "
                        f"term=<code>{w['term']}</code></span>",
                        unsafe_allow_html=True,
                    )
                    if w["owner"]:
                        st.caption(f"owner: {w['owner']}")
                with hh2:
                    if st.button(
                        "✓ Vu", key=f"watch_seen_{w['id']}",
                        use_container_width=True,
                        disabled=not unread,
                        help="Marque tous les signaux actuels comme vus.",
                    ):
                        attend_mark_watch_seen(w["id"])
                        st.cache_data.clear()
                        st.rerun()
                with hh3:
                    if st.button(
                        "🗑", key=f"watch_del_{w['id']}",
                        use_container_width=True,
                        help="Supprimer cette alerte.",
                    ):
                        attend_delete_watch(w["id"])
                        st.cache_data.clear()
                        st.toast("Alerte supprimée.", icon="🗑")
                        st.rerun()
                if unread:
                    rows_html = []
                    for sig in unread:
                        who = sig["person_name"] or sig["company_name"] or "—"
                        ts = (
                            sig["first_seen_at"].strftime("%Y-%m-%d %H:%M")
                            if sig["first_seen_at"] else "—"
                        )
                        rows_html.append(
                            f"<div style='font-size:0.85rem;padding:0.15rem 0;"
                            f"border-bottom:1px dashed #EFF2F5;'>"
                            f"<span style='color:#6c757d;'>{ts}</span> · "
                            f"<strong>{who}</strong> · "
                            f"{sig.get('signal_type') or '—'} · "
                            f"{sig.get('country') or '—'}"
                            + (
                                f" · <a href='{sig['source_url']}' target='_blank'>"
                                f"source</a>"
                                if sig.get("source_url") else ""
                            )
                            + "</div>"
                        )
                    st.markdown(
                        "<div style='margin-top:0.4rem;'>" + "".join(rows_html)
                        + "</div>",
                        unsafe_allow_html=True,
                    )

        st.divider()
        st.markdown("**➕ Ajouter une alerte**")
        with st.form("add_watch_form", clear_on_submit=True):
            wc1, wc2, wc3, wc4 = st.columns([1, 2, 2, 1.4])
            with wc1:
                kind = st.selectbox(
                    "Kind",
                    ["keyword", "company", "person", "country",
                     "role_category"],
                    key="watch_new_kind",
                    help="`keyword` = sous-chaîne sur titre/texte/personne/"
                    "société. `company`/`person` = match nom canonique. "
                    "`country` = exact. `role_category` = exact "
                    "(ex: 'CEO', 'Procurement').",
                )
            with wc2:
                term = st.text_input(
                    "Term *", key="watch_new_term",
                    placeholder="Ex: Thales / cyber / France / CEO",
                )
            with wc3:
                label = st.text_input(
                    "Label (facultatif)",
                    key="watch_new_label",
                    placeholder="Affiché dans la watchlist",
                )
            with wc4:
                owner = st.text_input(
                    "Owner",
                    key="watch_new_owner",
                    placeholder="alice@…",
                )
            if st.form_submit_button("➕ Créer l'alerte", type="primary"):
                if not term.strip():
                    st.warning("Term requis.")
                else:
                    new_id = attend_add_watch(
                        kind, term.strip(),
                        label or None, owner or None,
                    )
                    st.cache_data.clear()
                    st.toast(f"🔔 Alerte créée (#{new_id}).", icon="🔔")
                    st.rerun()


def _render_attendance_source_stats(df: pd.DataFrame) -> None:
    """Per-source-domain quality stats : volume, avg score, validation
    pass-rate, GDPR risk distribution.

    Reveals which sources produce the highest-quality OSINT (e.g. official
    press releases vs LinkedIn social posts) so the operator can weight
    further collection accordingly.
    """
    with st.expander("📊 Stats par source (qualité OSINT)", expanded=False):
        if df.empty or "source_url" not in df.columns:
            st.caption("Pas de signaux pour calculer les stats sources.")
            return
        df = df.copy()
        import re as _re

        def _domain(url: object) -> str:
            if not url or not isinstance(url, str):
                return "(inconnu)"
            m = _re.search(r"^https?://([^/]+)", url)
            host = m.group(1) if m else url
            return host.lower().removeprefix("www.")

        df["__domain"] = df["source_url"].apply(_domain)
        # Normalised platform (fallback to extracted domain)
        df["__platform"] = df.get("source_platform", "").fillna("(unknown)")

        # Aggregate per domain
        agg = df.groupby("__domain").agg(
            volume=("id", "count"),
            avg_score=("presence_score", "mean"),
            validated=("manual_validation_status",
                       lambda s: int((s == "Validated").sum())),
            rejected=("manual_validation_status",
                      lambda s: int((s == "Rejected").sum())),
            pending=("manual_validation_status",
                     lambda s: int((s == "Pending").sum())),
            review=("manual_validation_status",
                    lambda s: int((s == "Review required").sum())),
            high_gdpr=("gdpr_risk_level",
                       lambda s: int((s == "High").sum())),
            confidences=("presence_confidence", lambda s: s.value_counts().to_dict()),
        ).reset_index().rename(columns={"__domain": "Domaine"})
        agg = agg.sort_values("volume", ascending=False).head(40)
        # Pass rate among validated/rejected (excludes pending/review)
        decided = agg["validated"] + agg["rejected"]
        agg["pass_rate_%"] = (
            agg["validated"] / decided.replace(0, pd.NA) * 100
        ).fillna(0).round(0).astype(int)
        agg["avg_score"] = agg["avg_score"].fillna(0).round(1)
        agg["high_conf_%"] = agg["confidences"].apply(
            lambda d: int(round((d.get("High", 0) /
                                 max(sum(d.values()), 1)) * 100))
        )
        agg = agg.drop(columns=["confidences"])

        kc1, kc2, kc3 = st.columns(3)
        with kc1: _kpi("Domaines distincts", len(agg))
        with kc2: _kpi("Top domaine (volume)",
                       (agg.iloc[0]["Domaine"] if not agg.empty else "—"))
        with kc3:
            best_quality = (
                agg[agg["volume"] >= 5]
                .sort_values("avg_score", ascending=False)
            )
            _kpi(
                "Top domaine (qualité, ≥5 signaux)",
                (best_quality.iloc[0]["Domaine"]
                 if not best_quality.empty else "—"),
            )

        st.dataframe(
            agg,
            use_container_width=True, hide_index=True, height=380,
            column_config={
                "Domaine": st.column_config.TextColumn(width="medium"),
                "volume": st.column_config.NumberColumn("Volume"),
                "avg_score": st.column_config.NumberColumn("Score moyen"),
                "validated": st.column_config.NumberColumn("✅ Validés"),
                "rejected": st.column_config.NumberColumn("❌ Rejetés"),
                "pending": st.column_config.NumberColumn("⏳ Pending"),
                "review": st.column_config.NumberColumn("🔍 Review"),
                "high_gdpr": st.column_config.NumberColumn(
                    "GDPR High",
                    help="Nombre de signaux à risque GDPR élevé pour ce domaine.",
                ),
                "pass_rate_%": st.column_config.ProgressColumn(
                    "Pass rate %", min_value=0, max_value=100, format="%d%%",
                    help="Validés / (validés + rejetés). Mesure la fiabilité "
                    "du domaine selon les arbitrages humains passés.",
                ),
                "high_conf_%": st.column_config.ProgressColumn(
                    "% High confidence", min_value=0, max_value=100,
                    format="%d%%",
                ),
            },
        )

        st.caption(
            "💡 Un domaine avec **volume élevé + pass rate < 50%** est une "
            "source bruyante à filtrer. Un domaine avec **score moyen > 70 "
            "+ pass rate > 80%** mérite plus de requêtes ciblées."
        )


def _render_attendance_yoy(df: pd.DataFrame) -> None:
    """Year-over-year view : who signaled in past editions and signals
    again for 2026 → "warm" cibles, vs entirely new entrants.

    Two cohorts displayed :
    * **Repeat attendees** — canonical name appears in ≥ 2 editions
      (incl. 2026) → priority for the sales team.
    * **New for 2026** — present in 2026 only.
    """
    with st.expander("📅 Year-over-year — qui revient vs nouveaux entrants",
                     expanded=False):
        if df.empty or "edition_year" not in df.columns:
            st.caption("Pas de signaux datés pour comparer.")
            return
        years = sorted([int(y) for y in df["edition_year"].dropna().unique()])
        if len(years) < 2:
            st.caption(
                f"Une seule édition présente ({years[0] if years else '—'}). "
                "Importe des signaux d'éditions antérieures pour activer "
                "la comparaison year-over-year."
            )
            return

        # Use canonical name (company OR person) as the entity identity
        df = df.copy()
        df["__entity"] = df["canonical_company_name"].fillna("")
        mask_no_company = df["__entity"] == ""
        df.loc[mask_no_company, "__entity"] = df.loc[
            mask_no_company, "canonical_person_name"
        ].fillna("")
        df = df[df["__entity"] != ""]

        # Build a {entity → set of years}
        ent_years: dict[str, set[int]] = {}
        for ent, yr in df[["__entity", "edition_year"]].dropna().values:
            ent_years.setdefault(ent, set()).add(int(yr))

        latest_year = max(years)
        repeat_in_latest: list[tuple[str, set[int]]] = [
            (ent, yrs) for ent, yrs in ent_years.items()
            if latest_year in yrs and len(yrs) >= 2
        ]
        new_in_latest: list[str] = [
            ent for ent, yrs in ent_years.items()
            if yrs == {latest_year}
        ]
        gone: list[tuple[str, set[int]]] = [
            (ent, yrs) for ent, yrs in ent_years.items()
            if latest_year not in yrs
        ]

        kc1, kc2, kc3 = st.columns(3)
        with kc1: _kpi(f"🔁 Repeat dans {latest_year}",
                       len(repeat_in_latest), css_class="success")
        with kc2: _kpi(f"🆕 Nouveaux {latest_year}",
                       len(new_in_latest))
        with kc3: _kpi("📉 Absents cette année",
                       len(gone), css_class="warning")

        t1, t2, t3 = st.tabs([
            f"🔁 Repeat dans {latest_year} ({len(repeat_in_latest)})",
            f"🆕 Nouveaux {latest_year} ({len(new_in_latest)})",
            f"📉 Absents cette année ({len(gone)})",
        ])

        with t1:
            if not repeat_in_latest:
                st.caption("Personne ne signale 2026 ET une édition antérieure.")
            else:
                st.caption(
                    "💡 Cibles **chaudes** : ils étaient déjà présents avant "
                    "et re-signalent pour cette année."
                )
                rep_df = pd.DataFrame([
                    {
                        "Entité": ent,
                        "Éditions": " · ".join(str(y) for y in sorted(yrs)),
                        "N éditions": len(yrs),
                    }
                    for ent, yrs in sorted(
                        repeat_in_latest, key=lambda x: -len(x[1])
                    )[:200]
                ])
                st.dataframe(rep_df, use_container_width=True,
                             hide_index=True, height=320)

        with t2:
            if not new_in_latest:
                st.caption(f"Aucun nouvel entrant pour {latest_year}.")
            else:
                st.caption(
                    "🆕 Premières apparitions : à qualifier en priorité pour "
                    "comprendre s'ils sont des cibles ou des compétiteurs."
                )
                new_df = pd.DataFrame(
                    {"Entité": sorted(new_in_latest)[:200]}
                )
                st.dataframe(new_df, use_container_width=True,
                             hide_index=True, height=320)

        with t3:
            if not gone:
                st.caption("Tout le monde est revenu — pas de drop.")
            else:
                st.caption(
                    "📉 Présents les éditions précédentes mais pas de signal "
                    f"{latest_year} — relance possible / signal qu'ils "
                    "ne reviennent pas."
                )
                gone_df = pd.DataFrame([
                    {
                        "Entité": ent,
                        "Dernière édition": max(yrs),
                        "Éditions": " · ".join(str(y) for y in sorted(yrs)),
                    }
                    for ent, yrs in sorted(gone, key=lambda x: -max(x[1]))[:200]
                ])
                st.dataframe(gone_df, use_container_width=True,
                             hide_index=True, height=320)


def _render_attendance_duplicates_panel() -> None:
    """Show clusters of signals sharing a ``dedupe_key`` and let the user
    pick a primary + flip the rest to ``is_duplicate=True``.

    Reads via ``attend_dup_clusters`` (top 30 clusters by size).

    Admin-only : data-curation tool, hidden from buyers.
    """
    from app.ui.auth import is_admin
    if not is_admin():
        return
    with st.expander("🧬 Clusters de doublons (signaux dédupliqués)",
                     expanded=False):
        clusters = attend_dup_clusters(limit=30)
        if not clusters:
            st.caption("Aucun cluster de doublons détecté pour l'instant.")
            return
        st.caption(
            f"{len(clusters)} cluster(s) — un même événement peut générer "
            "plusieurs signaux (presse + LinkedIn + page événement). "
            "Choisis le **primaire** et bascule les autres en doublon."
        )
        for cluster in clusters:
            with st.container(border=True):
                hh1, hh2 = st.columns([5, 1])
                with hh1:
                    st.markdown(
                        f"<strong>{cluster['primary_label']}</strong> "
                        f"<span style='color:#6c757d;font-size:0.78rem;'>"
                        f"· édition {cluster['edition_year']} · "
                        f"{cluster['size']} signaux · "
                        f"<code>dedupe={cluster['dedupe_key']}</code>"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                with hh2:
                    if st.button(
                        "🧬 Fusionner",
                        key=f"dup_merge_{cluster['dedupe_key']}",
                        use_container_width=True,
                        type="primary",
                        help="Marque toutes les lignes du cluster comme "
                        "doublons sauf le primaire choisi (radio en bas).",
                    ):
                        primary_id = st.session_state.get(
                            f"dup_primary_{cluster['dedupe_key']}",
                            cluster["primary_id"],
                        )
                        n = attend_merge_cluster(
                            cluster["dedupe_key"], int(primary_id)
                        )
                        st.cache_data.clear()
                        st.toast(
                            f"🧬 {n} doublons reliés au primaire #{primary_id}.",
                            icon="🧬",
                        )
                        st.rerun()
                ids = [m["id"] for m in cluster["sample_signals"]]
                primary_id = st.radio(
                    "Choisir le **primaire** (le canonique conservé)",
                    ids,
                    index=ids.index(cluster["primary_id"])
                    if cluster["primary_id"] in ids else 0,
                    horizontal=True,
                    key=f"dup_primary_{cluster['dedupe_key']}",
                    label_visibility="collapsed",
                )
                # Sample table of the cluster members
                df_cluster = pd.DataFrame(cluster["sample_signals"])
                st.dataframe(
                    df_cluster,
                    use_container_width=True, hide_index=True, height=180,
                    column_config={
                        "is_duplicate": st.column_config.CheckboxColumn(
                            "Doublon ?", width="small",
                        ),
                        "presence_score": st.column_config.ProgressColumn(
                            "Score", min_value=0, max_value=100, format="%.0f",
                        ),
                        "source_url": st.column_config.LinkColumn(
                            "Source",
                            display_text=r"https?://(?:www\.)?([^/]+).*",
                        ),
                    },
                )


def _render_attendance_review_queue(filtered: pd.DataFrame) -> None:
    """Inline validation queue : signals with status ``Pending`` or
    ``Review required`` get a row with ✅ / ❌ / 🔍 buttons each.

    Lets the operator clear the validation backlog without opening the
    detail card for every signal. Limited to 25 signals at once to keep
    the page snappy.

    Admin-only : data-curation tool, hidden from buyers.
    """
    from app.ui.auth import is_admin
    if not is_admin():
        return
    if filtered.empty:
        return
    queue = filtered[
        filtered["manual_validation_status"].isin(["Pending", "Review required"])
    ]
    if queue.empty:
        return
    with st.expander(
        f"🚦 File de validation rapide — {len(queue)} signal(aux) à statuer",
        expanded=False,
    ):
        st.caption(
            "Boutons par ligne : **✅ Valider** / **❌ Rejeter** / **🔍 À revoir**. "
            "Tri par score décroissant pour traiter les plus pesants d'abord."
        )
        page = queue.sort_values("presence_score", ascending=False).head(25)
        for _, r in page.iterrows():
            sid = int(r["id"])
            who = r.get("person_name") or r.get("company_name") or "—"
            country = r.get("country") or ""
            sig_type = r.get("signal_type") or "—"
            score = r.get("presence_score")
            status = r.get("manual_validation_status") or "Pending"
            try:
                score_str = f"{float(score):.0f}" if score else "—"
            except (TypeError, ValueError):
                score_str = "—"
            with st.container(border=True):
                ic, b_yes, b_no, b_rev = st.columns([6.0, 1.2, 1.2, 1.2])
                with ic:
                    src_url = r.get("source_url") or ""
                    src_link = (
                        f" · [source]({src_url})" if src_url else ""
                    )
                    st.markdown(
                        f"<div style='line-height:1.25;'>"
                        f"<strong>{who}</strong> "
                        f"<span style='font-size:0.78rem;color:#6c757d;'>"
                        f"· {country} · score {score_str} · {sig_type} · "
                        f"<em>{status}</em>{src_link}</span></div>",
                        unsafe_allow_html=True,
                    )
                with b_yes:
                    if st.button(
                        "✅",
                        key=f"queue_val_yes_{sid}",
                        use_container_width=True, type="primary",
                        help=f"Valider le signal #{sid}",
                    ):
                        attend_set_validation(sid, "Validated")
                        st.cache_data.clear()
                        st.toast(f"✅ Validé : {who}", icon="✅")
                        st.rerun()
                with b_no:
                    if st.button(
                        "❌",
                        key=f"queue_val_no_{sid}",
                        use_container_width=True,
                        help=f"Rejeter le signal #{sid}",
                    ):
                        attend_set_validation(sid, "Rejected")
                        st.cache_data.clear()
                        st.toast(f"❌ Rejeté : {who}", icon="❌")
                        st.rerun()
                with b_rev:
                    if st.button(
                        "🔍",
                        key=f"queue_val_rev_{sid}",
                        use_container_width=True,
                        help=f"Marquer #{sid} à revoir plus tard",
                    ):
                        attend_set_validation(sid, "Review required")
                        st.cache_data.clear()
                        st.toast(f"🔍 À revoir : {who}", icon="🔍")
                        st.rerun()


def _render_attendance_timeline(filtered: pd.DataFrame) -> None:
    """Chronological feed of the filtered attendance signals.

    Groups by day (DESC). Within a day, each entry shows ``time + icon +
    person/company + signal_type + source link``. Used to scan "what
    arrived this week" before the show.
    """
    with st.expander("🕒 Timeline chronologique des signaux", expanded=False):
        if filtered.empty:
            st.caption("Aucun signal après filtres.")
            return
        df = filtered.copy()
        if "first_seen_at" not in df.columns:
            st.caption("Pas de date de capture sur ces signaux.")
            return
        df["__dt"] = pd.to_datetime(df["first_seen_at"], errors="coerce")
        df = df.dropna(subset=["__dt"]).sort_values("__dt", ascending=False)
        if df.empty:
            st.caption("Pas de date de capture exploitable.")
            return

        kc1, kc2, kc3 = st.columns(3)
        with kc1: _kpi("Signaux datés", len(df))
        with kc2: _kpi("Sur 7 jours",
                       int((df["__dt"]
                            >= (df["__dt"].max() - pd.Timedelta(days=7))).sum()))
        with kc3: _kpi("Sur 30 jours",
                       int((df["__dt"]
                            >= (df["__dt"].max() - pd.Timedelta(days=30))).sum()))

        df["__day"] = df["__dt"].dt.strftime("%Y-%m-%d")
        # Limit to ~120 most recent so Streamlit doesn't choke when filters open.
        df = df.head(120)
        for day, group in df.groupby("__day", sort=False):
            st.markdown(
                f"<div style='margin-top:0.6rem;font-weight:600;color:#0B2E4A;"
                f"border-bottom:1px solid #E2E6EA;padding-bottom:0.2rem;'>"
                f"📅 {day} <span style='color:#6c757d;font-weight:400;'>"
                f"&middot; {len(group)} signal(aux)</span></div>",
                unsafe_allow_html=True,
            )
            for _, r in group.iterrows():
                icon = _SIG_KIND_ICONS.get(r.get("signal_type") or "", "•")
                ts = r["__dt"].strftime("%H:%M")
                who = r.get("person_name") or r.get("company_name") or "—"
                role = r.get("person_role") or r.get("role_category") or ""
                country = r.get("country") or ""
                prio = r.get("sales_priority") or ""
                src = r.get("source_url") or ""
                src_link = (
                    f"<a href='{src}' target='_blank' style='font-size:0.78rem;'>"
                    f"source</a>" if src else ""
                )
                bits: list[str] = []
                if isinstance(role, str) and role: bits.append(role)
                if isinstance(country, str) and country: bits.append(country)
                if isinstance(prio, str) and prio:
                    bits.append(f"prio {prio}")
                stype = r.get("signal_type")
                if isinstance(stype, str) and stype:
                    bits.append(stype)
                meta_html = (
                    f"<span style='color:#6c757d;font-size:0.78rem;'>"
                    f"{' · '.join(bits)}</span>" if bits else ""
                )
                st.markdown(
                    f"<div style='padding:0.25rem 0;"
                    f"border-bottom:1px dashed #EFF2F5;'>"
                    f"<span style='color:#6c757d;font-variant-numeric:tabular-nums;"
                    f"margin-right:0.5rem;'>{ts}</span>"
                    f"<span style='margin-right:0.4rem;'>{icon}</span>"
                    f"<strong>{who}</strong> "
                    f"{meta_html} {src_link}</div>",
                    unsafe_allow_html=True,
                )


_VALIDATION_BADGE = {
    "Pending": ("#6c757d", "En attente"),
    "Review required": ("#D97706", "À revoir"),
    "Validated": ("#1F7A4D", "Validé"),
    "Rejected": ("#C1121F", "Rejeté"),
}

_PRIO_BADGE_ATT = {
    "A": ("#C1121F", "white"),
    "B": ("#D97706", "white"),
    "C": ("#1F3A5F", "white"),
    "D": ("#6c757d", "white"),
}


def _render_attendance_detail(signal_id: int) -> None:
    """Detail card for one ``AttendanceSignal``.

    Sections : identité (entity, role, company, country) ; signal (type,
    text, source, snippet) ; scoring (priority, presence, commercial
    relevance, GDPR) ; validation (status badge + quick action buttons) ;
    cluster (sibling signals sharing dedupe key / canonical names) ;
    pont vers la fiche société quand un exposant matche le nom canonique.
    """
    sig = attend_get_signal(signal_id)
    if sig is None:
        st.error(f"Signal #{signal_id} introuvable.")
        return

    st.divider()
    h1, h2 = st.columns([4, 1])
    with h1:
        title = (
            sig.person_name or sig.canonical_company_name or sig.company_name
            or "(entité non identifiée)"
        )
        prio_color, prio_fg = _PRIO_BADGE_ATT.get(
            (sig.sales_priority or "D"), ("#6c757d", "white")
        )
        prio_html = (
            f"<span class='badge' style='background:{prio_color};"
            f"color:{prio_fg};'>Prio {sig.sales_priority or 'D'}</span>"
        )
        val_color, val_label = _VALIDATION_BADGE.get(
            sig.manual_validation_status or "Pending", ("#6c757d", "—")
        )
        val_html = (
            f"<span class='badge' style='background:{val_color};color:white;'>"
            f"{val_label}</span>"
        )
        # Quality badges — shown right after the title so the operator
        # sees immediately what outreach data they have.
        notes = (sig.notes or "")
        has_email = bool(re.search(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", notes,
        ))
        has_phone = bool(re.search(r"\+\d[\d\s().\-]{8,}\d", notes))
        has_linkedin = bool(
            sig.source_url and "linkedin.com/" in sig.source_url.lower()
        ) or bool(re.search(r"linkedin\.com/", notes, re.IGNORECASE))
        has_role = bool((sig.person_role or "").strip())
        quality_badges = []
        if has_email:
            quality_badges.append(
                "<span class='badge' style='background:#1F7A4D;color:white;"
                "font-size:0.7rem;'>✉ Email</span>"
            )
        if has_phone:
            quality_badges.append(
                "<span class='badge' style='background:#2F5D7C;color:white;"
                "font-size:0.7rem;'>📞 Tel</span>"
            )
        if has_linkedin:
            quality_badges.append(
                "<span class='badge' style='background:#0A66C2;color:white;"
                "font-size:0.7rem;'>🔗 LinkedIn</span>"
            )
        if not has_role:
            quality_badges.append(
                "<span class='badge' style='background:#D97706;color:white;"
                "font-size:0.7rem;'>❓ Rôle inconnu</span>"
            )
        st.markdown(
            f"### 📡 {title} &nbsp; {prio_html} {val_html} "
            + " ".join(quality_badges),
            unsafe_allow_html=True,
        )
        sub = " · ".join(
            x for x in (
                f"Édition {sig.edition_year}" if sig.edition_year else None,
                sig.country,
                sig.entity_type,
                sig.role_category if sig.role_category != "Unknown" else None,
                f"Score {sig.presence_score:.0f}" if sig.presence_score is not None else None,
                f"Confiance {sig.presence_confidence}" if sig.presence_confidence else None,
            ) if x
        )
        st.caption(sub)
    with h2:
        if st.button("✕ Fermer la fiche",
                     key=f"att_detail_close_{signal_id}",
                     use_container_width=True):
            st.session_state.pop("att_detail_id", None)
            st.rerun()

    # Section 1 — Source. We expose :
    #  - the ``source_url`` whenever it points to LinkedIn (public link
    #    safe to share, even when the intel platform itself is
    #    proprietary).
    #  - the ``source_platform`` only when it IS LinkedIn-native.
    # The proprietary intel platforms (ADS / GICAT / Lemlist / etc.)
    # stay confidential to the client.
    is_linkedin_platform = bool(
        sig.source_platform
        and "linkedin" in sig.source_platform.lower()
    )
    src_url = sig.source_url or ""
    url_is_linkedin = "linkedin.com" in src_url.lower()
    st.markdown('<div class="section-title">1 · Source</div>',
                unsafe_allow_html=True)
    sc1, sc2 = st.columns([3, 2])
    with sc1:
        if url_is_linkedin:
            st.markdown(f"**URL LinkedIn** : [{src_url}]({src_url})")
            if is_linkedin_platform:
                if sig.source_title:
                    st.markdown(f"**Titre** : {sig.source_title}")
                if sig.source_platform:
                    st.markdown(f"**Plateforme** : `{sig.source_platform}`")
                if sig.search_query_used:
                    st.caption(
                        f"🔎 Recherche utilisée : `{sig.search_query_used}`"
                    )
        else:
            st.caption(
                "🔒 Source confidentielle — détail non exposé "
                "(intelligence propriétaire)."
            )
    with sc2:
        st.markdown(f"**1ʳᵉ apparition** : {sig.first_seen_at:%Y-%m-%d %H:%M}"
                     if sig.first_seen_at else "—")
        st.markdown(f"**Dernière vérif** : {sig.last_checked_at:%Y-%m-%d %H:%M}"
                     if sig.last_checked_at else "—")
        if sig.dedupe_key:
            st.caption(f"`dedupe_key={sig.dedupe_key}`")

    if sig.source_snippet:
        st.markdown("**📰 Extrait source**")
        st.markdown(
            f"<div style='border-left:3px solid #2F5D7C;padding:0.4rem 0.75rem;"
            f"background:#F4F6F8;border-radius:4px;font-size:0.9rem;'>"
            f"{sig.source_snippet}</div>",
            unsafe_allow_html=True,
        )

    # Section 2 — Nature du signal
    st.markdown('<div class="section-title">2 · Nature du signal</div>',
                unsafe_allow_html=True)
    nc1, nc2 = st.columns(2)
    with nc1:
        st.markdown(f"**Type** : {sig.signal_type or '—'}")
        st.markdown(f"**Entité** : {sig.entity_type or '—'}")
        if sig.person_name:
            st.markdown(f"**Personne** : {sig.person_name}")
        if sig.person_role:
            st.markdown(f"**Rôle (brut)** : {sig.person_role}")
        if sig.role_category:
            st.markdown(f"**Catégorie de rôle** : {sig.role_category}")
    with nc2:
        if sig.company_name:
            st.markdown(f"**Société** : {sig.company_name}")
        if sig.canonical_company_name and sig.canonical_company_name != sig.company_name:
            st.caption(f"_canonical : {sig.canonical_company_name}_")
        if sig.country:
            st.markdown(f"**Pays** : {sig.country}")
        flags = []
        if sig.is_company_post:
            flags.append("🏢 Post entreprise")
        if sig.is_personal_post:
            flags.append("👤 Post personnel")
        if sig.is_official_delegation:
            flags.append("🇺🇳 Délégation officielle")
        if sig.is_exhibitor_employee:
            flags.append("🛡 Employé exposant")
        if flags:
            st.markdown("**Flags** : " + " · ".join(flags))

    # Bridge → catalog exhibitor (when a match exists)
    bridge = attend_find_exhibitor(signal_id)
    if bridge:
        bc1, bc2 = st.columns([4, 1])
        with bc1:
            st.markdown(
                f"<div style='border-left:3px solid #1F7A4D;padding:0.4rem "
                f"0.75rem;background:#F0F7F2;border-radius:4px;margin-top:"
                f"0.4rem;'>🛡 Société catalogue trouvée : <strong>"
                f"{bridge['account_name']}</strong> · {bridge['country'] or '—'} · "
                f"<code>{bridge['account_id']}</code></div>",
                unsafe_allow_html=True,
            )
        with bc2:
            if st.button("📂 Fiche société", key=f"att_open_company_{signal_id}",
                         use_container_width=True, type="primary",
                         help="Mémorise l'id de la fiche pour l'onglet "
                         "Companies — clique ensuite sur 🏢 Companies."):
                st.session_state["jump_to_id"] = bridge["exhibitor_id"]
                st.toast(
                    f"📂 {bridge['account_name']} prêt à ouvrir — clique "
                    "sur l'onglet **🏢 Companies**.",
                    icon="📂",
                )

    if sig.signal_text:
        st.markdown("**🗒 Texte du signal**")
        st.markdown(
            f"<div style='border-left:3px solid #C1121F;padding:0.4rem 0.75rem;"
            f"background:#FFFFFF;border-radius:4px;font-size:0.9rem;'>"
            f"{sig.signal_text}</div>",
            unsafe_allow_html=True,
        )
    if sig.signal_strength_reason:
        st.caption(f"💡 **Pourquoi ce signal pèse** : {sig.signal_strength_reason}")

    # ---- Trade-association enrichment block (ADS UK + BDSV DE + GICAT FR + AIAD IT) ----
    ASSOC_PLATFORMS = ("ads-group-uk", "bdsv-de", "gicat-fr",
                        "gicat-fr-dirigeant", "ads-group-uk-2nd-contact",
                        "aiad-it", "aiad-it-officer")
    if sig.source_platform in ASSOC_PLATFORMS and sig.notes:
        import json as _json
        import re as _re
        notes = sig.notes or ""
        is_ads = sig.source_platform.startswith("ads-group-uk")
        is_bdsv = sig.source_platform == "bdsv-de"
        is_gicat = sig.source_platform.startswith("gicat-fr")
        is_aiad = sig.source_platform.startswith("aiad-it")
        title_emoji = (
            "🇬🇧" if is_ads
            else "🇩🇪" if is_bdsv
            else "🇫🇷" if is_gicat
            else "🇮🇹"
        )
        title_label = (
            "ADS Group UK directory" if is_ads
            else "BDSV — Bundesverband DE" if is_bdsv
            else "GICAT — Groupement FR" if is_gicat
            else "AIAD — Aerospazio Difesa Sicurezza IT"
        )
        if is_ads:
            cat_marker, tier_marker = "ads-categories", "ads-tier"
        elif is_bdsv:
            cat_marker, tier_marker = "bdsv-categories", "bdsv-tier"
        elif is_gicat:
            cat_marker, tier_marker = "gicat-categories", "gicat-tier"
        else:
            cat_marker, tier_marker = "aiad-categories", "aiad-tier"

        m_cat = _re.search(rf"\[{cat_marker}\]\s*([^\n]+)", notes)
        m_tier = _re.search(rf"\[{tier_marker}\]\s*([^\n]+)", notes)
        cats_str = (m_cat.group(1).strip() if m_cat else "")
        tier_str = (m_tier.group(1).strip() if m_tier else "")

        # ADS-specific structured fields
        knows: list[str] = []
        addr: dict = {}
        website: str = ""
        if is_ads:
            m_blob = _re.search(
                r"\[ads-detail\]\s*(\{.*?\})\s*(?:\n|$)", notes, _re.DOTALL,
            )
            if m_blob:
                try:
                    blob = _json.loads(m_blob.group(1))
                    knows = blob.get("knowsAbout") or []
                    addr = blob.get("address") or {}
                    website = blob.get("website") or ""
                except Exception:  # noqa: BLE001
                    pass

        # BDSV / GICAT / AIAD-specific extracted lines from notes
        bdsv_data: dict[str, str] = {}
        if not is_ads:
            extra_markers = ("address", "phone", "fax", "website",
                             "linkedin", "email", "portfolio",
                             "secteurs", "domaines", "produits",
                             "chiffres", "correspondant",
                             "dimension", "share_capital",
                             "other_locations")
            for marker in extra_markers:
                m = _re.search(
                    rf"\[{marker}\]\s*(.+?)(?=\n\[|\Z)",
                    notes, _re.DOTALL,
                )
                if m:
                    bdsv_data[marker] = m.group(1).strip()

        st.markdown(
            f'<div class="section-title">{title_emoji} {title_label}</div>',
            unsafe_allow_html=True,
        )
        ac1, ac2 = st.columns(2)
        with ac1:
            if tier_str and tier_str != "(aucune)":
                st.markdown(f"**Niveau supply chain** : {tier_str}")
            if cats_str and cats_str != "(aucune)":
                st.markdown(f"**Catégories produits** : {cats_str}")
            if knows:
                st.markdown(f"**Capabilities ({len(knows)})** :")
                badges = " ".join(
                    f"<span class='badge' style='background:#E2E8F0;"
                    f"color:#1E293B;font-size:0.72rem;margin:1px;'>"
                    f"{k}</span>"
                    for k in knows
                )
                st.markdown(badges, unsafe_allow_html=True)
            if bdsv_data.get("portfolio"):
                with st.expander("📋 Portfolio (texte)", expanded=False):
                    st.write(bdsv_data["portfolio"])
            if bdsv_data.get("secteurs"):
                st.markdown(f"**Secteurs d'intervention** : {bdsv_data['secteurs']}")
            if bdsv_data.get("domaines"):
                with st.expander("🎯 Domaines d'activité (taxonomie GICAT)", expanded=False):
                    st.write(bdsv_data["domaines"].replace(" · ", "  ·  "))
            if bdsv_data.get("produits"):
                st.markdown(f"**Produits nouveaux** : {bdsv_data['produits']}")
            if bdsv_data.get("chiffres"):
                st.markdown(f"**Chiffres clés** : {bdsv_data['chiffres']}")
            if bdsv_data.get("correspondant"):
                st.markdown(f"**Correspondant GICAT** : {bdsv_data['correspondant']}")
            if bdsv_data.get("dimension"):
                st.markdown(f"**Taille société** : {bdsv_data['dimension']}")
            if bdsv_data.get("share_capital"):
                st.markdown(f"**Capital social** : {bdsv_data['share_capital']}")
            if bdsv_data.get("other_locations"):
                with st.expander("📍 Autres locations", expanded=False):
                    st.write(bdsv_data["other_locations"])
            if bdsv_data.get("fax"):
                st.markdown(f"**Fax** : {bdsv_data['fax']}")
        with ac2:
            if addr:
                bits = [
                    addr.get("streetAddress"),
                    addr.get("addressLocality"),
                    addr.get("postalCode"),
                ]
                bits = [b for b in bits if b]
                if bits:
                    st.markdown("**Adresse** :")
                    st.markdown("<br>".join(bits), unsafe_allow_html=True)
            elif bdsv_data.get("address"):
                st.markdown("**Adresse** :")
                st.markdown(bdsv_data["address"].replace(" | ", "<br>"),
                            unsafe_allow_html=True)
            if website:
                st.markdown(f"**Website** : [{website}]({website})")
            elif bdsv_data.get("website"):
                ws = bdsv_data["website"]
                href = ws if ws.startswith("http") else f"http://{ws}"
                st.markdown(f"**Website** : [{ws}]({href})")
            if bdsv_data.get("phone"):
                st.markdown(f"**Téléphone** : {bdsv_data['phone']}")
            if bdsv_data.get("email"):
                em = bdsv_data["email"]
                st.markdown(f"**Email** : [{em}](mailto:{em})")
            if bdsv_data.get("linkedin"):
                st.markdown(f"**LinkedIn** : {bdsv_data['linkedin']}")

    # Section 3 — Scoring & GDPR
    st.markdown('<div class="section-title">3 · Scoring &amp; GDPR</div>',
                unsafe_allow_html=True)
    kp = st.columns(5)
    with kp[0]: _kpi("Sales priority", sig.sales_priority or "D",
                     css_class="alert" if sig.sales_priority == "A" else "")
    with kp[1]: _kpi("Score", f"{sig.presence_score:.0f}"
                     if sig.presence_score is not None else "—")
    with kp[2]: _kpi("Confiance", sig.presence_confidence or "—")
    with kp[3]: _kpi("Rel. commerciale", sig.commercial_relevance or "—")
    with kp[4]: _kpi("GDPR", sig.gdpr_risk_level or "—",
                     css_class="alert" if sig.gdpr_risk_level == "High"
                     else "warning" if sig.gdpr_risk_level == "Medium"
                     else "")
    if sig.target_type:
        st.caption(f"🎯 Target type : {sig.target_type}")

    # Section 4 — CRM action
    if sig.next_best_action or sig.reason_to_contact or sig.recommended_angle:
        st.markdown('<div class="section-title">4 · CRM action</div>',
                    unsafe_allow_html=True)
        if sig.next_best_action:
            st.markdown(f"**Prochaine action** : {sig.next_best_action}")
        if sig.reason_to_contact:
            st.markdown(f"**Raison de contact** : {sig.reason_to_contact}")
        if sig.recommended_angle:
            st.markdown(f"**Angle recommandé** : {sig.recommended_angle}")

    # Section 5 — Validation (quick actions)
    st.markdown('<div class="section-title">5 · Validation</div>',
                unsafe_allow_html=True)
    bc1, bc2, bc3, bc4 = st.columns(4)
    with bc1:
        if st.button("✅ Valider", key=f"att_val_yes_{signal_id}",
                     use_container_width=True, type="primary"):
            attend_set_validation(signal_id, "Validated")
            st.cache_data.clear()
            st.toast("✅ Signal validé.", icon="✅")
            st.rerun()
    with bc2:
        if st.button("❌ Rejeter", key=f"att_val_no_{signal_id}",
                     use_container_width=True):
            attend_set_validation(signal_id, "Rejected")
            st.cache_data.clear()
            st.toast("❌ Signal rejeté.", icon="❌")
            st.rerun()
    with bc3:
        if st.button("🔍 À revoir", key=f"att_val_review_{signal_id}",
                     use_container_width=True):
            attend_set_validation(signal_id, "Review required")
            st.cache_data.clear()
            st.toast("🔍 Marqué à revoir.", icon="🔍")
            st.rerun()
    with bc4:
        if st.button("↺ Pending", key=f"att_val_reset_{signal_id}",
                     use_container_width=True):
            attend_set_validation(signal_id, "Pending")
            st.cache_data.clear()
            st.toast("Statut remis en attente.", icon="⏳")
            st.rerun()

    if sig.notes:
        with st.expander("🗒 Historique des notes / validations"):
            st.text(sig.notes)

    # Section 6 — Cluster (sibling signals on the same canonical entity / dedupe)
    siblings = attend_sibling_signals(signal_id, limit=12)
    if siblings:
        st.markdown('<div class="section-title">6 · Cluster — autres '
                    'signaux sur la même entité</div>',
                    unsafe_allow_html=True)
        st.caption(
            f"{len(siblings)} signal(aux) sœurs (même `dedupe_key` ou même "
            "société/personne canonique). Pratique pour confirmer un fait."
        )
        sib_df = pd.DataFrame(siblings)
        if not sib_df.empty:
            keep = [c for c in [
                "edition_year", "entity_type", "person_name", "company_name",
                "country", "signal_type", "presence_score",
                "manual_validation_status", "source_url",
            ] if c in sib_df.columns]
            st.dataframe(
                sib_df[keep],
                use_container_width=True, hide_index=True, height=240,
                column_config={
                    "source_url": st.column_config.LinkColumn(
                        "Source", display_text=r"https?://(?:www\.)?([^/]+).*",
                    ),
                    "presence_score": st.column_config.ProgressColumn(
                        "Score", min_value=0, max_value=100, format="%.0f",
                    ),
                },
            )


def _render_attendance_bulk(signal_ids: list[int]) -> None:
    n = len(signal_ids)
    st.markdown(
        f'<div class="section-title">⚡ Bulk actions — {n} signal(aux) sélectionné(s)</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**✅ Validation status**")
        new_status = st.selectbox(
            "Statut",
            ["Pending", "Review required", "Validated", "Rejected"],
            key="attbulk_status", label_visibility="collapsed",
        )
        if st.button("Appliquer", key="attbulk_status_apply", use_container_width=True):
            from app.database import AttendanceSignal, session_scope
            with session_scope() as s:
                for sid in signal_ids:
                    sig = s.get(AttendanceSignal, sid)
                    if sig:
                        sig.manual_validation_status = new_status
            st.cache_data.clear()
            st.success(f"{n} signal(aux) → {new_status}")
            st.rerun()
    with cols[1]:
        st.markdown("**📝 Note**")
        note_text = st.text_input("Note", key="attbulk_note",
                                  label_visibility="collapsed",
                                  placeholder="Note partagée à appliquer")
        if st.button("Ajouter la note", key="attbulk_note_apply", use_container_width=True):
            if not note_text.strip():
                st.warning("Note vide")
            else:
                from app.database import AttendanceSignal, session_scope
                with session_scope() as s:
                    for sid in signal_ids:
                        sig = s.get(AttendanceSignal, sid)
                        if sig:
                            existing = (sig.notes or "").rstrip()
                            sig.notes = (existing + "\n" + note_text).strip() if existing else note_text
                st.cache_data.clear()
                st.success(f"Note ajoutée sur {n} signal(aux).")
                st.rerun()
    with cols[2]:
        st.markdown("**🚫 Reject batch**")
        if st.button("Marquer Rejected", key="attbulk_reject", use_container_width=True):
            from app.database import AttendanceSignal, session_scope
            with session_scope() as s:
                for sid in signal_ids:
                    sig = s.get(AttendanceSignal, sid)
                    if sig:
                        sig.manual_validation_status = "Rejected"
            st.cache_data.clear()
            st.success(f"{n} signal(aux) rejeté(s).")
            st.rerun()

    # --- Add to custom list (the companies behind these persons) ----------
    st.markdown(
        '<div class="section-title">📋 Ajouter à une liste de prospection'
        '</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Map les sociétés des personnes sélectionnées vers leurs fiches "
        "exposants catalogue (quand match) et les ajoute à une liste "
        "commerciale custom — réutilise les listes définies dans "
        "« ⭐ Favoris »."
    )
    # Resolve unique exhibitor IDs behind these signals
    from app.database import AttendanceSignal, Exhibitor, session_scope as _ss
    matched_ids: set[int] = set()
    unmatched_companies: set[str] = set()
    with _ss() as s:
        from sqlalchemy import select as _sel, func as _f
        # Build a normalised name → id map of the catalog (cheap : 2580 rows)
        catalog: dict[str, int] = {}
        for cn, eid in s.execute(
            _sel(Exhibitor.company_name, Exhibitor.id)
        ).all():
            if cn:
                k = " ".join(str(cn).lower().split())
                catalog[k] = eid
        for sid in signal_ids:
            sig = s.get(AttendanceSignal, sid)
            if not sig or not sig.company_name:
                continue
            k = " ".join(sig.company_name.lower().split())
            eid = catalog.get(k)
            if eid is None:
                # Substring fallback
                for cn, e in catalog.items():
                    if len(cn) >= 4 and (cn in k or k in cn):
                        eid = e
                        break
            if eid is not None:
                matched_ids.add(int(eid))
            else:
                unmatched_companies.add(sig.company_name)
    cc1, cc2 = st.columns([1, 1])
    with cc1:
        st.markdown(
            f"**🛡 Sociétés matchées** : {len(matched_ids)} fiches "
            f"catalogue."
        )
        if unmatched_companies:
            with st.expander(
                f"⚠️ {len(unmatched_companies)} sociétés non-matchées "
                "(elles ne seront PAS ajoutées)"
            ):
                for cn in sorted(unmatched_companies)[:30]:
                    st.caption(f"• {cn}")
    with cc2:
        all_lists = list_custom_lists()
        target_list = st.selectbox(
            "Liste cible",
            [""] + [l.name for l in all_lists],
            key="attbulk_list_target",
            label_visibility="collapsed",
        )
        new_list_name = st.text_input(
            "ou nouveau nom",
            key="attbulk_list_new",
            placeholder="Nouvelle liste (ex: GICAT prospects 2026)",
            label_visibility="collapsed",
        )
        if st.button(
            f"📋 Ajouter {len(matched_ids)} société(s) à la liste",
            key="attbulk_list_apply", type="primary",
            disabled=not matched_ids,
            use_container_width=True,
        ):
            list_id: int | None = None
            if new_list_name.strip():
                list_id = create_custom_list(name=new_list_name.strip())
            elif target_list:
                lst = next(
                    (l for l in all_lists if l.name == target_list), None
                )
                list_id = lst.id if lst else None
            if list_id is None:
                st.warning("Choisis une liste ou saisis un nouveau nom.")
            else:
                added = add_to_list(list_id, list(matched_ids))
                st.cache_data.clear()
                st.success(
                    f"📋 {added} société(s) ajoutée(s) à la liste — clique "
                    "sur l'onglet **📋 Custom lists** pour la voir."
                )
                st.rerun()


def _render_add_signal_form() -> None:
    st.markdown("**➕ Ajouter un signal manuel**")
    with st.form("add_signal_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            year = st.number_input("Edition year *", 2018, 2030, 2026)
            entity = st.selectbox(
                "Entity type *",
                ["person", "company", "delegation", "institution", "media", "unknown"],
            )
            url = st.text_input("Source URL *")
            search_query = st.text_input("Search query used")
        with c2:
            person = st.text_input("Person name")
            role = st.text_input("Person role / job title")
            company = st.text_input("Company name")
            country = st.text_input("Country")
        with c3:
            signal_type = st.selectbox(
                "Signal type",
                ["", "company_announcement", "personal_linkedin_post",
                 "official_delegation", "national_pavilion", "press_release",
                 "event_page", "social_post", "media_article"],
            )
            source_platform = st.selectbox(
                "Source platform",
                ["", "corporate_site", "press", "event_page", "linkedin",
                 "x", "wayback", "other"],
            )
            is_official = st.checkbox("Official delegation")
            is_exhib_emp = st.checkbox("Exhibitor employee")

        snippet = st.text_area("Source snippet / signal text",
                               help="Excerpt of the public post / page", height=80)
        notes = st.text_input("Notes")
        submit = st.form_submit_button("Add signal")
        if submit:
            if not url:
                st.error("source URL is required")
                return
            try:
                raw = RawSignal(
                    edition_year=int(year),
                    entity_type=entity,
                    source_url=url.strip(),
                    person_name=person or None,
                    person_role=role or None,
                    company_name=company or None,
                    country=country or None,
                    source_platform=source_platform or None,
                    source_title=None,
                    source_snippet=snippet or None,
                    search_query_used=search_query or None,
                    signal_type=signal_type or None,
                    signal_text=snippet or None,
                    is_official_delegation=bool(is_official),
                    is_exhibitor_employee=bool(is_exhib_emp),
                    notes=notes or None,
                )
                obj, is_new = upsert_signal(raw)
                st.cache_data.clear()
                st.success(
                    f"{'Created' if is_new else 'Updated'} — score "
                    f"{obj.presence_score:.0f}, priority {obj.sales_priority}, "
                    f"GDPR {obj.gdpr_risk_level}"
                )
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Failed to add: {e}")


def _render_queries_panel() -> None:
    with st.expander("🔍 OSINT queries — to run manually in your browser"):
        st.caption(
            "Templates ; copier/coller dans Google. **Ne jamais** se logguer "
            "sur LinkedIn pour scraper — utiliser uniquement ce qui est indexé "
            "publiquement par Google."
        )
        years = st.multiselect("Années", [2024, 2025, 2026], default=[2026])
        if years:
            for q in queries_for_years(years):
                st.code(q, language=None)


# Pre-built Google search queries the operator can run in their browser
# to surface visitor-style URLs to bulk-import. Designed to bias toward
# publicly-indexed LinkedIn slugs + defense press articles that name
# specific people.
_ATT_QUERY_CHEATSHEET: list[tuple[str, str]] = [
    ("LinkedIn — visiteurs Eurosatory 2026",
     'site:linkedin.com/in "Eurosatory 2026"'),
    ("LinkedIn — invités Eurosatory 2026",
     'site:linkedin.com/in "I will be at Eurosatory 2026"'),
    ("LinkedIn — défense procurement",
     'site:linkedin.com/in "procurement officer" "defense"'),
    ("LinkedIn — chief of staff armée FR",
     'site:linkedin.com/in "chef d\'état-major" "armée"'),
    ("LinkedIn — délégations gouvernementales",
     'site:linkedin.com/in "Ministry of Defence" "Eurosatory"'),
    ("LinkedIn — DGA / direction générale armement",
     'site:linkedin.com/in "DGA" OR "Direction Générale de l\'Armement"'),
    ("Presse — délégation Eurosatory",
     '"delegation" "Eurosatory" 2026 site:opex360.com OR '
     'site:meta-defense.fr OR site:forcesoperations.com'),
    ("Presse anglo — defense attaché",
     '"defense attaché" "Eurosatory" -exhibitor'),
    ("Communiqués gouvernementaux",
     '"Eurosatory 2026" site:defense.gouv.fr OR site:gov.uk OR '
     'site:bundeswehr.de'),
]


def _render_attendance_quick_import() -> None:
    """Bulk-paste UI : URLs in → AttendanceSignal rows out, with a cheat
    sheet of pre-built Google queries to seed the URLs.

    Admin-only : data-curation tool, hidden from buyers.
    """
    from app.ui.auth import is_admin
    if not is_admin():
        return
    with st.expander(
        "➕ Enrichir la liste rapidement (bulk paste URLs / "
        "requêtes Google)",
        expanded=False,
    ):
        st.caption(
            "**Workflow** : (1) clique sur une requête ci-dessous, elle s'ouvre "
            "dans Google ; (2) copie les URLs qui t'intéressent (en particulier "
            "les profils LinkedIn `linkedin.com/in/...` — on déduit le nom "
            "depuis le slug, sans besoin de connexion) ; (3) colle-les dans "
            "la zone en bas, une par ligne ; (4) clique **Analyser**."
        )

        # Google query cheat sheet — opens in a new tab.
        from urllib.parse import quote_plus
        st.markdown("**🔎 Requêtes Google prêtes à lancer**")
        for label, query in _ATT_QUERY_CHEATSHEET:
            url = f"https://www.google.com/search?q={quote_plus(query)}"
            st.markdown(
                f"- [{label}]({url}) "
                f"<span style='color:#6c757d;font-size:0.78rem;'>"
                f"<code>{query}</code></span>",
                unsafe_allow_html=True,
            )

        st.markdown("---")
        st.markdown("**📋 URLs à analyser**")
        cc1, cc2 = st.columns([1, 4])
        with cc1:
            paste_year = st.number_input(
                "Édition cible",
                min_value=2020, max_value=2030, value=2026, step=1,
                key="att_quickpaste_year",
            )
        with cc2:
            urls_raw = st.text_area(
                "URLs (une par ligne)",
                key="att_quickpaste_urls",
                height=160,
                placeholder=(
                    "https://www.linkedin.com/in/jean-dupont-ceo/\n"
                    "https://www.linkedin.com/in/marie-martin-procurement-officer/\n"
                    "https://www.opex360.com/2026/04/article-eurosatory…\n"
                    "https://breakingdefense.com/2026/article-attendees…"
                ),
                label_visibility="collapsed",
            )

        if st.button("▶ Analyser & importer",
                     key="att_quickpaste_run",
                     type="primary"):
            urls = [
                u.strip() for u in (urls_raw or "").splitlines()
                if u.strip()
            ]
            if not urls:
                st.warning("Colle au moins une URL.")
                return
            from app.attendance.auto_collect import bulk_paste_signals
            progress = st.progress(0.0, text="Démarrage…")

            def _cb(scanned: int, total: int, hits: int) -> None:
                pct = scanned / total if total else 1.0
                progress.progress(
                    min(pct, 1.0),
                    text=f"Scanné {scanned}/{total} · {hits} importés",
                )

            with st.spinner("Fetch en cours…"):
                result = bulk_paste_signals(
                    urls, target_year=int(paste_year), progress_cb=_cb,
                )
            progress.progress(1.0, text="Terminé.")
            cols = st.columns(4)
            with cols[0]: _kpi("Scanné", result["scanned"])
            with cols[1]: _kpi(
                "➕ Nouveaux", result["new"], css_class="success",
            )
            with cols[2]: _kpi("🔄 Mis à jour", result["updated"])
            with cols[3]: _kpi(
                "⏭ Skip / err",
                result["skipped"] + result["errors"],
                css_class="warning"
                if (result["skipped"] + result["errors"]) else "",
            )
            if result["results"]:
                st.markdown("**Détails par URL**")
                df = pd.DataFrame(result["results"])
                st.dataframe(
                    df, use_container_width=True,
                    hide_index=True, height=200,
                )
            st.cache_data.clear()
            st.toast(
                f"📋 +{result['new']} nouveaux signaux importés.",
                icon="📋",
            )


# Quick-filter chips : labels → regex on the ``person_role`` field.
_ATT_ROLE_CHIPS: dict[str, str] = {
    "👔 C-level": (
        r"\b(CEO|CTO|COO|CFO|CIO|CISO|PDG|DG|Pr[ée]sident(?:e)?|"
        r"Directeur\s+G[ée]n[ée]ral|Managing\s+Director|"
        r"Founder|Co-?Founder|Fondateur|Chairman)\b"
    ),
    "💼 Direction": (
        r"\b(VP|Vice\s+Pr[ée]sident|Director|Directeur|Directrice|"
        r"Head\s+of|Chef|Responsable|General\s+Manager)\b"
    ),
    "🛒 Achats": (
        r"\b(Procurement|Acheteur|Achats|Purchasing|Buyer|Sourcing)\b"
    ),
    "💰 Sales": (
        r"\b(Sales|Commercial|Business\s+Development|BD|Account\s+Manager|"
        r"Vente|Ventes)\b"
    ),
    "🧪 R&D / Tech": (
        r"\b(Research|R&?D|Engineer|Ing[ée]nieur|Architect|Technical|"
        r"Lead\s+Tech|CTO|Technique)\b"
    ),
    "🇺🇳 Defense / Mil.": (
        r"\b(Minist[èe]r(?:e|y)|Ministre|G[ée]n[ée]ral|General|Colonel|"
        r"Lieutenant|Major|Capitaine|Commander|Admiral|Defense|D[ée]fense|"
        r"Armed\s+Forces|Arm[ée]e)\b"
    ),
}


def _render_attendance_role_chips() -> Optional[str]:
    """Render quick-filter buttons on top of the filters. Returns the
    label of the currently-active chip (or None)."""
    cols = st.columns(len(_ATT_ROLE_CHIPS) + 1)
    active = st.session_state.get("att_role_chip")
    for i, label in enumerate(_ATT_ROLE_CHIPS):
        with cols[i]:
            is_on = active == label
            if st.button(
                ("✓ " + label) if is_on else label,
                key=f"att_chip_{label}",
                use_container_width=True,
                type="primary" if is_on else "secondary",
            ):
                st.session_state["att_role_chip"] = (
                    None if is_on else label
                )
                st.rerun()
    with cols[-1]:
        if st.button("✕ Reset", key="att_chip_reset",
                     use_container_width=True,
                     disabled=active is None):
            st.session_state["att_role_chip"] = None
            st.rerun()
    return active


def _apply_role_chip_filter(df: pd.DataFrame, chip: str) -> pd.DataFrame:
    pattern = _ATT_ROLE_CHIPS.get(chip)
    if not pattern:
        return df
    role_col = df.get("person_role")
    if role_col is None:
        return df.iloc[0:0]
    mask = role_col.fillna("").astype(str).str.contains(
        pattern, regex=True, case=False, na=False,
    )
    return df[mask]


def _render_attendance_grouped(filtered: pd.DataFrame) -> None:
    """Group view : one expandable card per company, listing all the
    persons attached to it. ABM-friendly — quickly see "10 contacts at
    Thales" type summaries.
    """
    if filtered.empty:
        st.info("Aucun signal après filtres.")
        return
    df = filtered.copy()
    df["__company_key"] = (
        df["company_name"].fillna("(société inconnue)").astype(str)
    )
    by_company = df.groupby("__company_key", sort=False)
    # Sort companies by contact count desc.
    company_order = sorted(
        by_company.groups.keys(),
        key=lambda k: -len(by_company.groups[k]),
    )
    total_companies = len(company_order)

    # ── Pagination ──────────────────────────────────────────────────────
    # Render N companies per page with ◀ / ▶ navigation. The default
    # page size of 50 keeps the DOM snappy even on a 5500-company
    # filtered set, while still letting the user browse the whole tail
    # via pagination (no more "5270 hidden, refine to see").
    cap_col, page_col = st.columns([1.4, 4])
    with cap_col:
        page_size = st.selectbox(
            "Sociétés / page",
            [25, 50, 100, 200, "Tout"],
            index=1,
            key="att_grouped_page_size",
            format_func=lambda v: (
                f"Tout ({total_companies})" if v == "Tout" else str(v)
            ),
            label_visibility="collapsed",
        )

    if page_size == "Tout":
        size = total_companies or 1
    else:
        size = int(page_size)
    total_pages = max(1, (total_companies + size - 1) // size)

    page_key = "att_grouped_page"
    # Reset the page when filters / page-size change leaves us past the end
    raw_page = int(st.session_state.get(page_key, 0))
    page = max(0, min(raw_page, total_pages - 1))

    with page_col:
        if total_pages > 1:
            pc1, pc2, pc3 = st.columns([1, 1.6, 1])

            def _prev_page() -> None:
                cur = int(st.session_state.get(page_key, 0))
                st.session_state[page_key] = max(0, cur - 1)

            def _next_page() -> None:
                cur = int(st.session_state.get(page_key, 0))
                st.session_state[page_key] = min(total_pages - 1, cur + 1)

            with pc1:
                st.button(
                    "◀", key="att_grouped_prev",
                    disabled=page <= 0,
                    use_container_width=True,
                    on_click=_prev_page,
                )
            with pc2:
                st.markdown(
                    f"<div style='text-align:center;line-height:2.3rem;"
                    f"font-size:0.85rem;'>Page <b>{page + 1}</b> / "
                    f"{total_pages}</div>",
                    unsafe_allow_html=True,
                )
            with pc3:
                st.button(
                    "▶", key="att_grouped_next",
                    disabled=page >= total_pages - 1,
                    use_container_width=True,
                    on_click=_next_page,
                )

    st.caption(
        f"**{total_companies} sociétés** · **{len(df)} contacts** au total · "
        f"affichage : sociétés {page * size + 1}–"
        f"{min((page + 1) * size, total_companies)}"
    )

    page_slice = company_order[page * size : (page + 1) * size]

    for company in page_slice:
        group = by_company.get_group(company)
        n = len(group)
        countries = sorted(set(
            c for c in group["country"].dropna().astype(str).tolist() if c
        ))
        country_str = " · ".join(countries[:3])
        matched_ex = next(
            (m for m in group["matched_exhibitor"].dropna().tolist()),
            None,
        )
        with st.expander(
            f"🏢 {company} — {n} contact(s)"
            + (f" · {country_str}" if country_str else "")
        ):
            if matched_ex and matched_ex != company:
                st.markdown(
                    f"<div style='background:#F0F7F2;padding:0.3rem 0.6rem;"
                    f"border-left:3px solid #1F7A4D;border-radius:4px;"
                    f"font-size:0.85rem;margin-bottom:0.4rem;'>"
                    f"🛡 Match exposant catalogue : <strong>{matched_ex}"
                    f"</strong></div>",
                    unsafe_allow_html=True,
                )
            display = group[[
                "person_name", "person_role", "derived_email",
                "derived_phone", "derived_linkedin", "source_url",
            ]].copy()
            st.dataframe(
                display, hide_index=True, use_container_width=True,
                column_config={
                    "person_name": st.column_config.TextColumn(
                        "Personne", width="medium",
                    ),
                    "person_role": st.column_config.TextColumn(
                        "Rôle", width="medium",
                    ),
                    "derived_email": st.column_config.TextColumn(
                        "✉ Email", width="medium",
                    ),
                    "derived_phone": st.column_config.TextColumn(
                        "📞 Tel", width="small",
                    ),
                    "derived_linkedin": st.column_config.LinkColumn(
                        "🔗 LinkedIn", width="small",
                        display_text=
                        r".*linkedin\.com/(?:in|company)/([^/?]+).*",
                    ),
                    "source_url": st.column_config.LinkColumn(
                        "Source", width="small",
                        display_text=r"https?://(?:www\.)?([^/]+).*",
                    ),
                },
            )


def _render_attendance_prospection_export(filtered: pd.DataFrame) -> None:
    """One-click CSV export of the filtered list with the columns a
    sales rep needs : name / role / company / country / email / phone /
    linkedin / source. Drop-in for Outlook / Lemlist / mail-merge."""
    if filtered.empty:
        return
    st.divider()
    st.markdown(
        '<div class="section-title">⬇ Export prospection</div>',
        unsafe_allow_html=True,
    )
    cols_export = [
        "person_name", "person_role", "company_name", "country",
        "derived_email", "derived_phone", "derived_linkedin",
        "matched_exhibitor", "source_url", "edition_year",
    ]
    out = filtered[
        [c for c in cols_export if c in filtered.columns]
    ].rename(columns={
        "person_name": "Nom",
        "person_role": "Rôle",
        "company_name": "Société",
        "country": "Pays",
        "derived_email": "Email",
        "derived_phone": "Téléphone",
        "derived_linkedin": "LinkedIn",
        "matched_exhibitor": "Exposant catalogue",
        "source_url": "Source",
        "edition_year": "Édition",
    })
    csv = out.to_csv(index=False).encode("utf-8")
    n_with_email = int(out.get("Email", pd.Series()).notna().sum())
    cc1, cc2 = st.columns([2, 1])
    with cc1:
        st.caption(
            f"**{len(out)} contacts** · {n_with_email} avec email direct · "
            "format prêt pour Outlook / Lemlist / mail-merge."
        )
    with cc2:
        st.download_button(
            "⬇ Télécharger CSV",
            data=csv,
            file_name=
            f"prospection_eurosatory_{datetime.utcnow():%Y%m%d_%H%M}.csv",
            mime="text/csv",
            use_container_width=True,
            key="att_prospection_export",
        )


def render_attendance_signals_tab() -> None:
    st.markdown(
        '<div class="section-title">📡 Attendance Signals — '
        'qui sera potentiellement présent</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Liste enrichie des **personnes** et **sociétés** détectées via "
        "OSINT public (sites corporate, communiqués, posts indexés). "
        "Chaque ligne porte le **lien vers la source** qui justifie sa "
        "présence dans la liste."
    )

    # Quick-import expander — bulk-paste LinkedIn / press URLs to enrich
    # the list on the fly. Collapsed by default so the table stays
    # front-and-center, but easy to access.
    _render_attendance_quick_import()

    df = _load_signals()

    # ── DEMO HARD LIMIT (same rationale as load_crm slicing in main) ──
    # Cap at 50 signals when in demo mode. The buyer can play with all
    # filters but never extract more than 50 rows.
    if _is_demo_mode():
        df = df.head(50).copy()

    # Slim filter row
    filters = _render_attendance_filters(df)
    filtered = attend_apply_filters(df, **filters)

    # View mode : grouped-by-company (default, ABM) vs flat list.
    # We prefer the ABM view because buyers map signals → accounts when
    # preparing their target list ; the flat list is the "drill into one
    # signal" view, useful but secondary.
    view_mode = st.radio(
        "Vue",
        ["🏢 Groupé par société", "📋 Liste"],
        horizontal=True,
        key="att_view_mode",
        label_visibility="collapsed",
    )

    if view_mode.startswith("🏢"):
        _render_attendance_grouped(filtered)
    else:
        detail_id, bulk_ids = _render_attendance_table(filtered)
        if detail_id is not None:
            st.session_state["att_detail_id"] = detail_id
            _render_attendance_detail(detail_id)
        elif bulk_ids:
            st.session_state.pop("att_detail_id", None)
            _render_attendance_bulk(bulk_ids)

    # Prospection export — small CSV download with the contact-ready
    # columns for outreach (Outlook / Lemlist / mail-merge).
    _render_attendance_prospection_export(filtered)


main()
