"""Target lists — pre-built prospection lists for the defense commercial.

Replaces the old "Data Quality" tab with an actionable, sober interface.
Each list is a single click that pre-loads the Companies tab filters.

Design : monochrome, typography-driven, monospace numbers — built for
defense / B2B context. No emojis in the cards themselves.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# List definitions
# ---------------------------------------------------------------------------
# Each list maps filter_* session-state keys (the same ones used by the
# sidebar) to the pre-loaded values. Values match the canonical taxonomies
# (PRODUCT_CATEGORIES, supply chain tiers, country names).

TARGET_LISTS: list[dict[str, Any]] = [
    {
        "id": "premium_oem",
        "title": "OEM — vendeurs de système final",
        "subtitle": (
            "Sociétés au sommet de la pyramide industrielle : véhicules, "
            "drones, missiles, navires, systèmes complets livrés au client final."
        ),
        "filters": {
            "filter_supply_chain_tier": ["OEM"],
        },
        "tags": ["TIER OEM", "SYSTÈMES FINAUX"],
    },
    {
        "id": "mro",
        "title": "MRO — maintenance & overhaul",
        "subtitle": (
            "Sociétés dont le métier principal est la maintenance, "
            "réparation et remise à niveau de systèmes (aéronefs, navires, "
            "véhicules, équipements). Ex : Sabena Technics, Babcock, Millog."
        ),
        "filters": {
            "filter_supply_chain_tier": ["MRO"],
        },
        "tags": ["MRO", "MCO", "AFTERMARKET"],
    },
    {
        "id": "uav_makers",
        "title": "Fabricants drones aériens",
        "subtitle": (
            "OEM et Tier 1 spécialisés UAV tactiques, ISR, "
            "munitions rôdeuses et systèmes anti-drone."
        ),
        "filters": {
            "filter_supply_chain_tier": ["OEM", "Tier 1"],
            "filter_targeting_prod_cats": [
                "Drones aériens (UAV)",
                "Drones FPV & munitions rôdeuses",
                "Systèmes anti-drone (C-UAS)",
            ],
        },
        "tags": ["DRONES", "C-UAS", "ISR"],
    },
    {
        "id": "small_arms",
        "title": "Armement individuel & accessoires",
        "subtitle": (
            "Armes légères, optiques d'armes, viseurs, munitions petit / "
            "moyen calibre, équipement du fantassin connecté."
        ),
        "filters": {
            "filter_targeting_prod_cats": [
                "Armes légères & accessoires",
                "Optiques d'armes (viseurs)",
                "Munitions petit/moyen calibre",
                "Équipement du fantassin (général)",
            ],
        },
        "tags": ["ARMEMENT", "FANTASSIN"],
    },
    {
        "id": "missiles_heavy",
        "title": "Missiles & armements lourds",
        "subtitle": (
            "Missiles guidés, roquettes, munitions gros calibre, "
            "explosifs et matériaux énergétiques, tourelles téléopérées."
        ),
        "filters": {
            "filter_targeting_prod_cats": [
                "Missiles & armements guidés",
                "Roquettes & lance-roquettes",
                "Munitions gros calibre & obus",
                "Explosifs & matériaux énergétiques",
                "Tourelles téléopérées (RWS)",
            ],
        },
        "tags": ["MISSILES", "ARTILLERIE"],
    },
    {
        "id": "naval",
        "title": "Naval & sous-systèmes maritimes",
        "subtitle": (
            "Navires militaires, sous-marins, sonars, systèmes ASM, "
            "ingénierie navale et chantiers."
        ),
        "filters": {
            "filter_targeting_prod_cats": [
                "Navires & sous-systèmes navals",
                "Sonars & systèmes ASM",
            ],
        },
        "tags": ["NAVAL", "ASM"],
    },
    {
        "id": "optronics",
        "title": "Optronique, radar & EW",
        "subtitle": (
            "EO/IR, détecteurs IR, vision nocturne, radars et "
            "systèmes de guerre électronique."
        ),
        "filters": {
            "filter_targeting_prod_cats": [
                "Optronique & viseurs (EO/IR)",
                "Détecteurs IR refroidis & non-refroidis",
                "Vision nocturne & jumelles",
                "Radars & traitement signal",
                "Systèmes de guerre électronique (EW)",
            ],
        },
        "tags": ["OPTRONIQUE", "RADAR", "EW"],
    },
    {
        "id": "cyber_software",
        "title": "Cyber, IA & logiciels défense",
        "subtitle": (
            "Cybersécurité, plateformes IA et vision, logiciels métier "
            "et environnements de simulation."
        ),
        "filters": {
            "filter_targeting_prod_cats": [
                "Cybersécurité (logiciels & appliances)",
                "Plateformes IA / vision défense",
                "Logiciels métier défense (autres)",
                "Logiciels de simulation & cyber range",
                "Simulateurs d'entraînement & VR/AR",
            ],
        },
        "tags": ["CYBER", "IA", "SIMULATION"],
    },
    {
        "id": "tier2_components",
        "title": "Sous-traitance Tier 2 — composants",
        "subtitle": (
            "Cartes électroniques, capteurs, connectique, alimentations "
            "et antennes pour intégrateurs Tier 1."
        ),
        "filters": {
            "filter_supply_chain_tier": ["Tier 2"],
            "filter_targeting_prod_cats": [
                "Composants électroniques & cartes",
                "Capteurs embarqués (généraux)",
                "Connectique & câblage durcis",
                "Alimentations & convertisseurs durcis",
                "Antennes & infrastructures RF",
            ],
        },
        "tags": ["TIER 2", "ÉLECTRONIQUE"],
    },
    {
        "id": "tier3_machining",
        "title": "Sous-traitance Tier 3 — mécanique",
        "subtitle": (
            "Pièces mécaniques, usinage, hydraulique, transmission, "
            "fixations aéronautiques."
        ),
        "filters": {
            "filter_supply_chain_tier": ["Tier 3"],
            "filter_targeting_prod_cats": [
                "Pièces mécaniques & usinage",
                "Hydraulique & motion control",
                "Mécanique de transmission",
                "Fixations & visserie aéronautique",
            ],
        },
        "tags": ["TIER 3", "USINAGE"],
    },
    {
        "id": "tier4_materials",
        "title": "Matières premières — Tier 4",
        "subtitle": (
            "Aciers spéciaux, composites, céramiques balistiques, "
            "batteries et piles à combustible."
        ),
        "filters": {
            "filter_supply_chain_tier": ["Tier 4"],
        },
        "tags": ["TIER 4", "MATÉRIAUX"],
    },
    {
        "id": "armor_protection",
        "title": "Protection corporelle & blindage",
        "subtitle": (
            "Casques de combat, gilets pare-balles, vitrages blindés, "
            "NRBC, matériaux composites & céramiques balistiques."
        ),
        "filters": {
            "filter_targeting_prod_cats": [
                "Casques de combat",
                "Gilets & plaques pare-balles",
                "Vitrages blindés",
                "NRBC (masques, tenues, détecteurs)",
                "Matériaux composites & blindage",
                "Céramiques techniques & balistiques",
            ],
        },
        "tags": ["PROTECTION", "BLINDAGE"],
    },
    {
        "id": "fr_smes",
        "title": "PME défense françaises",
        "subtitle": (
            "Sociétés défense françaises Tier 2 et Tier 3 — cible "
            "sous-traitance pour primes nationaux."
        ),
        "filters": {
            "filter_countries": ["France"],
            "filter_supply_chain_tier": ["Tier 2", "Tier 3"],
        },
        "tags": ["FR", "PME"],
    },
    {
        "id": "services_only",
        "title": "Services autour de la défense",
        "subtitle": (
            "Prestataires sans produit propre — MCO/MRO, intégration de "
            "systèmes, formation, conseil, audit cyber, sous-traitance, "
            "certification, R&D sur contrat."
        ),
        "filters": {
            "filter_supply_chain_tier": ["N/A"],
            "filter_targeting_svc_cats": [
                "MCO / MRO",
                "Intégration de systèmes",
                "Ingénierie & conseil",
                "Formation & entraînement",
                "Audit & cybersécurité OT",
                "Sous-traitance industrielle",
                "Certification & tests",
                "Services cloud & data",
                "Support opérationnel & OPEX",
                "Modernisation de flottes",
                "Logistique militaire & transit",
                "R&D sur contrat",
                "Métrologie & essais matériaux",
                "Documentation technique & ILS",
                "Conseil en gestion de programme",
            ],
        },
        "tags": ["SERVICES", "MCO", "CONSEIL"],
    },
    {
        "id": "institutional",
        "title": "Institutionnels & délégations",
        "subtitle": (
            "Clusters, fédérations professionnelles, ministères, agences "
            "publiques, R&D académique, médias spécialisés, organisateurs "
            "de salons, financement et assurance défense."
        ),
        "filters": {
            "filter_targeting_prod_cats": [
                "Représentation institutionnelle (cluster, fédération, chambre)",
                "Achat public défense & politique industrielle",
                "Médias & publications défense",
                "Organisation de salons & conférences défense",
                "Financement, banque & assurance défense",
                "R&D académique & laboratoires",
                "Conseil stratégique & due-diligence M&A",
                "Logistique export défense & transit",
            ],
        },
        "tags": ["INSTITUTIONNEL", "R&D", "GOUVERNEMENT"],
    },
]


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


_CSS = """
<style>
.tlc-grid { padding: 0; }
.tlc-card {
    background: #ffffff;
    border: 1px solid #E2E8F0;
    border-left: 3px solid #475569;
    padding: 1.1rem 1.25rem 0.85rem 1.25rem;
    margin-bottom: 0.4rem;
    transition: border-color 0.12s ease;
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
    height: 195px;        /* fixed height so all cards + buttons align */
    box-sizing: border-box;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.tlc-card:hover {
    border-color: #94A3B8;
    border-left-color: #1E293B;
}
.tlc-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.75rem;
}
.tlc-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: #0F172A;
    line-height: 1.25;
    flex: 1;
    letter-spacing: -0.01em;
}
.tlc-volume {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    font-size: 1.65rem;
    font-weight: 500;
    color: #1E293B;
    line-height: 1;
    white-space: nowrap;
    letter-spacing: -0.02em;
}
.tlc-subtitle {
    font-size: 0.8rem;
    color: #475569;
    line-height: 1.45;
    margin: 0;
    /* Clamp to 3 lines max so all cards have consistent description height */
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
.tlc-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.5rem;
    align-items: center;
    margin-top: auto;
    padding-top: 0.4rem;
    border-top: 1px dashed #E2E8F0;
}
.tlc-tag {
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 0.65rem;
    font-weight: 500;
    color: #475569;
    letter-spacing: 0.06em;
    background: transparent;
    padding: 0.1rem 0;
    border-right: 1px solid #CBD5E1;
    padding-right: 0.5rem;
}
.tlc-tag:last-of-type { border-right: none; padding-right: 0; }
.tlc-countries {
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 0.7rem;
    color: #64748B;
    letter-spacing: 0.04em;
    margin-left: auto;
}
.tlc-section-title {
    font-size: 1.45rem;
    font-weight: 600;
    color: #0F172A;
    letter-spacing: -0.02em;
    margin: 0.2rem 0 0.2rem 0;
}
.tlc-section-sub {
    color: #64748B;
    font-size: 0.875rem;
    margin: 0 0 1.5rem 0;
    line-height: 1.5;
}
.tlc-stats-bar {
    display: flex;
    gap: 2rem;
    align-items: baseline;
    border-top: 1px solid #E2E8F0;
    border-bottom: 1px solid #E2E8F0;
    padding: 0.85rem 0;
    margin-bottom: 1.4rem;
}
.tlc-stat-item { display: flex; flex-direction: column; gap: 0.1rem; }
.tlc-stat-label {
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #64748B;
    font-weight: 500;
}
.tlc-stat-value {
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 1.35rem;
    font-weight: 500;
    color: #1E293B;
}
/* Streamlit button override under cards */
.tlc-action-row .stButton > button {
    background: #ffffff;
    color: #1E293B;
    border: 1px solid #CBD5E1;
    border-radius: 0;
    font-size: 0.78rem;
    font-weight: 500;
    letter-spacing: 0.02em;
    height: 32px;
    padding: 0 0.85rem;
    box-shadow: none;
    transition: all 0.12s ease;
    width: 100%;
}
.tlc-action-row .stButton > button:hover {
    background: #0F172A;
    color: #ffffff;
    border-color: #0F172A;
}
.tlc-action-row .stButton > button:active { background: #1E293B; }
</style>
"""


def inject_css() -> None:
    if not st.session_state.get("_tlc_css_injected"):
        st.markdown(_CSS, unsafe_allow_html=True)
        st.session_state["_tlc_css_injected"] = True


# ---------------------------------------------------------------------------
# Filter computation
# ---------------------------------------------------------------------------


_COUNTRY_ISO = {
    # Both "United Kingdom" and "United-Kingdom" forms — the source data
    # mixes hyphenated and spaced variants.
    "France": "FR", "Germany": "DE",
    "United Kingdom": "UK", "United-Kingdom": "UK", "UK": "UK",
    "Italy": "IT", "Italia": "IT",
    "Spain": "ES", "España": "ES",
    "Netherlands": "NL", "Belgium": "BE",
    "Sweden": "SE", "Finland": "FI", "Norway": "NO", "Denmark": "DK",
    "Switzerland": "CH", "Austria": "AT",
    "Poland": "PL", "Portugal": "PT",
    "Czech Republic": "CZ", "Czech-Republic": "CZ",
    "Slovakia": "SK", "Slovenia": "SI",
    "Greece": "GR", "Hungary": "HU", "Romania": "RO", "Bulgaria": "BG",
    "USA": "US", "United States": "US", "United-States": "US",
    "United States of America": "US",
    "Canada": "CA", "Israel": "IL", "Turkey": "TR",
    "Japan": "JP",
    "South Korea": "KR", "South-Korea": "KR", "Korea": "KR",
    "Australia": "AU", "China": "CN",
    "India": "IN", "Brazil": "BR",
    "Lithuania": "LT", "Latvia": "LV", "Estonia": "EE", "Ireland": "IE",
    "Luxembourg": "LU", "Iceland": "IS", "Croatia": "HR",
    "United Arab Emirates": "AE", "Saudi Arabia": "SA",
    "Russia": "RU", "Ukraine": "UA",
    "South Africa": "ZA", "Singapore": "SG",
}


def _filter_dataframe(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """Mirror of the sidebar filter logic — used to compute live counts."""
    sub = df

    if (vals := filters.get("filter_supply_chain_tier")):
        if "supply_chain_tier" in sub.columns:
            sub = sub[sub["supply_chain_tier"].isin(vals)]

    if (vals := filters.get("filter_countries")):
        if "country" in sub.columns:
            sub = sub[sub["country"].isin(vals)]

    if (vals := filters.get("filter_targeting_prod_cats")):
        if "products_categories" in sub.columns:
            def _hit(v):
                if not isinstance(v, str):
                    return False
                return any(c in v for c in vals)
            sub = sub[sub["products_categories"].fillna("").apply(_hit)]

    if (vals := filters.get("filter_target_buyers")):
        if "target_buyers" in sub.columns:
            def _hit(v):
                if not isinstance(v, str):
                    return False
                return any(b in v for b in vals)
            sub = sub[sub["target_buyers"].fillna("").apply(_hit)]

    if (vals := filters.get("filter_targeting_svc_cats")):
        if "services_categories" in sub.columns:
            def _hit(v):
                if not isinstance(v, str):
                    return False
                return any(c in v for c in vals)
            sub = sub[sub["services_categories"].fillna("").apply(_hit)]

    if (vals := filters.get("filter_targeting_tech_cats")):
        if "technologies_categories" in sub.columns:
            def _hit(v):
                if not isinstance(v, str):
                    return False
                return any(c in v for c in vals)
            sub = sub[sub["technologies_categories"].fillna("").apply(_hit)]

    return sub


def _country_summary(sub_df: pd.DataFrame) -> str:
    """Return top 4 country ISO codes joined by ' · '.

    Prefer the canonical ``country_iso2`` field (always 2-letter ISO);
    fall back to a translated lookup on the French ``country`` field
    only if the ISO column isn't available.
    """
    if sub_df.empty:
        return ""
    if "country_iso2" in sub_df.columns:
        s = sub_df["country_iso2"].dropna()
        s = s[s.astype(str).str.len() == 2]
        counts = s.str.upper().value_counts().head(4)
        return " · ".join(counts.index.tolist())
    if "country" in sub_df.columns:
        counts = sub_df["country"].dropna().value_counts().head(4)
        bits = []
        for c, _ in counts.items():
            iso = _COUNTRY_ISO.get(str(c), str(c)[:3].upper())
            bits.append(iso)
        return " · ".join(bits)
    return ""


# ---------------------------------------------------------------------------
# Apply a target list = inject filters into session_state, toast, rerun
# ---------------------------------------------------------------------------


_FILTER_KEYS = (
    "filter_supply_chain_tier",
    "filter_countries",
    "filter_targeting_prod_cats",
    "filter_targeting_svc_cats",
    "filter_targeting_tech_cats",
    "filter_target_buyers",
    "filter_min_targeting_score",
    "filter_search",
    "filter_min_score",
    "filter_priorities",
    "filter_target_types",
    "filter_segments",
    "filter_lead_statuses",
    "filter_crm_stages",
    "filter_only_priority",
    "filter_only_favorites",
    "filter_only_high_conf",
    "filter_only_website",
)


def _apply_target_list(lst: dict) -> None:
    # Reset all known filter keys so we get a clean slate
    for k in _FILTER_KEYS:
        if k in st.session_state:
            try:
                del st.session_state[k]
            except KeyError:
                pass
    # Inject this list's filters
    for k, v in lst["filters"].items():
        st.session_state[k] = v
    st.session_state["_tlc_just_applied"] = lst["title"]
    st.rerun()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_target_lists_tab(df: pd.DataFrame) -> None:
    inject_css()

    # Toast feedback after re-run
    just_applied = st.session_state.pop("_tlc_just_applied", None)
    if just_applied:
        st.success(
            f"Filtres « {just_applied} » appliqués. "
            f"Ouvrez l'onglet Companies pour voir le résultat.",
            icon=None,
        )

    # ---- Header
    st.markdown(
        '<h2 class="tlc-section-title">Listes ciblées</h2>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="tlc-section-sub">Sélections pré-construites pour la '
        "prospection commerciale défense. Un clic charge les filtres "
        "correspondants dans l'onglet Companies — vous arrivez sur la "
        "table déjà filtrée, prête à l'export ou à la qualification "
        "manuelle.</p>",
        unsafe_allow_html=True,
    )

    # ---- Stats bar (sober)
    n_total = len(df)
    n_with_cat = (
        int((df.get("products_categories", pd.Series(dtype=str))
             .fillna("").astype(bool)).sum())
        if "products_categories" in df.columns else 0
    )
    n_with_act = (
        int((df.get("activity_1liner", pd.Series(dtype=str))
             .fillna("").astype(bool)).sum())
        if "activity_1liner" in df.columns else 0
    )
    pct_cat = (n_with_cat * 100 // n_total) if n_total else 0
    pct_act = (n_with_act * 100 // n_total) if n_total else 0
    st.markdown(
        f'''<div class="tlc-stats-bar">
            <div class="tlc-stat-item">
                <span class="tlc-stat-label">Sociétés</span>
                <span class="tlc-stat-value">{n_total:,}</span>
            </div>
            <div class="tlc-stat-item">
                <span class="tlc-stat-label">Activité qualifiée</span>
                <span class="tlc-stat-value">{pct_act}%</span>
            </div>
            <div class="tlc-stat-item">
                <span class="tlc-stat-label">Catégorie produit</span>
                <span class="tlc-stat-value">{pct_cat}%</span>
            </div>
            <div class="tlc-stat-item">
                <span class="tlc-stat-label">Listes disponibles</span>
                <span class="tlc-stat-value">{len(TARGET_LISTS)}</span>
            </div>
        </div>'''.replace(",", " "),
        unsafe_allow_html=True,
    )

    # ---- Grid of cards (3 columns)
    cols_per_row = 3
    rows = [
        TARGET_LISTS[i:i + cols_per_row]
        for i in range(0, len(TARGET_LISTS), cols_per_row)
    ]
    for row in rows:
        cols = st.columns(cols_per_row, gap="small")
        for i, lst in enumerate(row):
            with cols[i]:
                sub = _filter_dataframe(df, lst["filters"])
                volume = len(sub)
                country_summary = _country_summary(sub)
                tags_html = "".join(
                    f'<span class="tlc-tag">{t}</span>'
                    for t in lst.get("tags", [])
                )
                st.markdown(
                    f'''<div class="tlc-card">
                        <div class="tlc-header">
                            <div class="tlc-title">{lst["title"]}</div>
                            <div class="tlc-volume">{volume:,}</div>
                        </div>
                        <div class="tlc-subtitle">{lst["subtitle"]}</div>
                        <div class="tlc-meta">
                            {tags_html}
                            <span class="tlc-countries">{country_summary}</span>
                        </div>
                    </div>'''.replace(",", " "),
                    unsafe_allow_html=True,
                )
                # Action button below the card
                st.markdown('<div class="tlc-action-row">',
                            unsafe_allow_html=True)
                if st.button(
                    "Charger ces filtres",
                    key=f"tlc_apply_{lst['id']}",
                    use_container_width=True,
                ):
                    _apply_target_list(lst)
                st.markdown('</div>', unsafe_allow_html=True)

    # Footer note
    st.markdown(
        '<p class="tlc-section-sub" style="margin-top: 1.5rem; '
        "font-size: 0.78rem;\">Les volumes sont calculés en direct sur "
        "la base actuelle (taxonomie produit canonique 75 catégories, "
        "5 niveaux supply-chain).</p>",
        unsafe_allow_html=True,
    )
