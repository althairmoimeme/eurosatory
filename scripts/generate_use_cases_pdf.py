"""Generate the customer-facing PDF "10 use-cases commerciaux".

This is the third deliverable in the bundle (after the XLSX and the data
dictionary). It shows ten concrete commercial questions a defense rep
might ask the database, with :
  - the exact filter combination to apply in Excel
  - the count of matching companies (computed from the live DB)
  - 3 hand-picked example companies
  - the commercial angle ("why this matters")

Output : ``data/exports/Eurosatory_2026_use_cases.pdf``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm, mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    BaseDocTemplate, Frame, KeepTogether, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

EXPORT_DIR = ROOT / "data" / "exports"

# Theme colours (aligned with dictionary PDF + Streamlit header)
NAVY = colors.HexColor("#0B2E4A")
NAVY_SOFT = colors.HexColor("#162C50")
RED_ACCENT = colors.HexColor("#C1121F")
SLATE = colors.HexColor("#2A3A4D")
TEAL = colors.HexColor("#1F7A4D")
WARN = colors.HexColor("#D97706")
GRID = colors.HexColor("#DDE4EE")
BG_SOFT = colors.HexColor("#F4F8FB")
BG_NAVY = colors.HexColor("#0F1B2D")
WHITE = colors.HexColor("#FFFFFF")


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "T", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=24, leading=28, textColor=WHITE,
        ),
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=14, leading=18, textColor=NAVY,
            spaceBefore=10, spaceAfter=4,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=NAVY_SOFT,
            spaceBefore=6, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "B", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13, textColor=SLATE,
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "S", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=11, textColor=SLATE,
        ),
        "caption": ParagraphStyle(
            "C", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8.5, leading=11, textColor=colors.HexColor("#6c757d"),
        ),
        "code": ParagraphStyle(
            "Co", parent=base["Normal"], fontName="Courier",
            fontSize=8.5, leading=11, textColor=NAVY,
            backColor=BG_SOFT,
        ),
        "case_n": ParagraphStyle(
            "CaseN", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=22, leading=22, textColor=WHITE,
        ),
        "case_title": ParagraphStyle(
            "CaseT", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=12, leading=16, textColor=WHITE,
        ),
        "case_lead": ParagraphStyle(
            "CaseL", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, leading=12, textColor=colors.HexColor("#B5C6D9"),
        ),
    }


# ---------------------------------------------------------------------------
# Page painters
# ---------------------------------------------------------------------------


def _draw_first_page_header(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(BG_NAVY)
    canvas.rect(0, A4[1] - 5*cm, A4[0], 5*cm, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 24)
    canvas.setFillColor(WHITE)
    canvas.drawString(2*cm, A4[1] - 2.4*cm,
                      "Eurosatory 2026 — 10 use-cases commerciaux")
    canvas.setFont("Helvetica", 10.5)
    canvas.setFillColor(colors.HexColor("#B5C6D9"))
    canvas.drawString(2*cm, A4[1] - 3.2*cm,
                      "Comment trouver tes cibles en 30 secondes — "
                      "filtres prêts à l'emploi sur les 2580 exposants")
    canvas.setFont("Helvetica", 9)
    canvas.drawString(2*cm, A4[1] - 4.0*cm,
                      "À utiliser en complément du fichier "
                      "Eurosatory_2026_targeting.xlsx")
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(WHITE)
    canvas.drawRightString(A4[0] - 2*cm, A4[1] - 4.0*cm,
                           "EUROSATORY · 2026")
    canvas.restoreState()
    _draw_footer(canvas, doc)


def _draw_normal_page_header(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, A4[1] - 1.4*cm, A4[0], 1.4*cm, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 9.5)
    canvas.setFillColor(WHITE)
    canvas.drawString(2*cm, A4[1] - 0.95*cm,
                      "Eurosatory 2026 — 10 use-cases commerciaux")
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(colors.HexColor("#B5C6D9"))
    canvas.drawRightString(A4[0] - 2*cm, A4[1] - 0.95*cm,
                           "EUROSATORY · 2026")
    canvas.restoreState()
    _draw_footer(canvas, doc)


def _draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#6c757d"))
    canvas.setFont("Helvetica", 8)
    canvas.drawString(2*cm, 1*cm,
                      "Eurosatory 2026 Targeting CRM · livrable commercial v1")
    canvas.drawRightString(A4[0] - 2*cm, 1*cm, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Use-case definitions (filters + hand-picked examples + angle)
# ---------------------------------------------------------------------------


EU_PAYS = {
    "France", "Germany", "United-Kingdom", "Italy", "Spain", "Sweden",
    "Norway", "Denmark", "Finland", "Belgium", "Netherlands", "Luxembourg",
    "Czech Republic", "Slovakia", "Poland", "Hungary", "Croatia",
    "Bulgaria", "Romania", "Greece", "Portugal", "Austria", "Cyprus",
    "Ireland", "Estonia", "Latvia", "Lithuania",
    "Bosnia and Herzegovina", "Slovenia", "Switzerland",
}

ME_PAYS = {"United Arab Emirates", "Saudi Arabia", "Israel"}


USE_CASES: list[dict] = [
    {
        "n": "01",
        "title": "« Trouve-moi tous les fabricants de drones aériens en Europe »",
        "buyer_role": "Commercial qui vend des nacelles EO/IR ou batteries militaires",
        "filters": [
            ("Catégories produits", "Drones aériens (UAV)"),
            ("Pays", "France · Allemagne · UK · Italie · Espagne · Suède · …"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(product_cat_in=["Drones aériens (UAV)"],
                              country_in=EU_PAYS, min_score=60),
        "examples": ["AIRBUS", "PARROT DRONES", "Bayraktar Grubu"],
        "angle": "Acheteurs potentiels de capteurs EO/IR, batteries Li-ion "
                 "haute densité, liaisons de données chiffrées et composites "
                 "carbone. Plus la flotte est grande, plus le besoin "
                 "d'approvisionnement Tier-1 est récurrent.",
    },
    {
        "n": "02",
        "title": "« Trouve-moi les fournisseurs d'optronique infrarouge »",
        "buyer_role": "Commercial qui vend des détecteurs IR refroidis (Lynred, AIM, Sofradir)",
        "filters": [
            ("Catégories technologies", "Imagerie infrarouge"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(tech_cat_in=["Imagerie infrarouge"], min_score=60),
        "examples": ["Teledyne FLIR Defense", "AIM Infrarot-Module", "IRnova AB"],
        "angle": "Concurrence directe sur les détecteurs IR — utile pour "
                 "benchmarker positionnement, prix et roadmap. Aussi : "
                 "clients indirects via les primes optronique qui intègrent "
                 "ces composants.",
    },
    {
        "n": "03",
        "title": "« Qui sont mes compétiteurs sur véhicules blindés ? »",
        "buyer_role": "Commercial chez un prime véhicules (KNDS, Rheinmetall, Iveco DV)",
        "filters": [
            ("Catégories produits", "Véhicules blindés"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(product_cat_in=["Véhicules blindés"], min_score=60),
        "examples": ["Rheinmetall AG", "GDELS", "Oshkosh Defense"],
        "angle": "Vue exhaustive du paysage concurrentiel mondial sur le "
                 "segment blindés — chars, IFV, MRAP. Permet de planifier "
                 "le démarchage avant le salon (RDV stand, événements "
                 "secondaires) sans rater un rival.",
    },
    {
        "n": "04",
        "title": "« Trouve-moi des sous-traitants mécanique en France »",
        "buyer_role": "Acheteur prime français cherchant à sécuriser sa supply chain locale",
        "filters": [
            ("Catégories services", "Sous-traitance industrielle"),
            ("Pays", "France"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(svc_cat_in=["Sous-traitance industrielle"],
                              country_in={"France"}, min_score=60),
        "examples": ["ALPES USINAGE - SAS DECOPREM", "JPB SYSTÈME",
                     "AMETRA INTEGRATION"],
        "angle": "Tissu PME français de la mécanique de précision et "
                 "EMS — particulièrement utile en contexte de "
                 "souveraineté industrielle et BITD (Base Industrielle "
                 "et Technologique de Défense).",
    },
    {
        "n": "05",
        "title": "« Quelles sont les pépites IA défense ? »",
        "buyer_role": "Investisseur deep-tech ou prime cherchant des acquisitions",
        "filters": [
            ("Catégories technologies", "IA / machine learning"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(tech_cat_in=["IA / machine learning"],
                              min_score=60),
        "examples": ["HELSING", "BUSTER.AI", "MIDGARD"],
        "angle": "Cartographie des éditeurs IA défense (vision, fusion, "
                 "décision). Combine avec « Origine = manual » pour ne "
                 "voir que les fiches enrichies haute qualité — souvent "
                 "les start-ups deep-tech à fort potentiel.",
    },
    {
        "n": "06",
        "title": "« Qui produit du jamming RF / EW ? »",
        "buyer_role": "Acheteur prime anti-drone / EW intégrateur",
        "filters": [
            ("Catégories produits",
             "Systèmes de guerre électronique (EW) · Radios tactiques & SDR"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(
            product_cat_in=["Systèmes de guerre électronique (EW)",
                            "Radios tactiques & SDR"],
            min_score=60,
        ),
        "examples": ["ROHDE &amp; SCHWARZ", "Aselsan", "Allen-Vanguard"],
        "angle": "Cible double : (a) compétiteurs sur le marché EW pour "
                 "primes intégrateurs ; (b) fournisseurs de briques "
                 "(brouilleurs, antennes phased-array) pour intégrateurs "
                 "anti-drone événementiel.",
    },
    {
        "n": "07",
        "title": "« Mes contacts au Moyen-Orient (Émirats, Saoudia, Israël) »",
        "buyer_role": "Commercial export, structuration de partenariats locaux",
        "filters": [
            ("Cibles clients", "Export / international"),
            ("Pays", "United Arab Emirates · Saudi Arabia · Israel"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(target_in=["Export / international"],
                              country_in=ME_PAYS, min_score=60),
        "examples": ["EDGE GROUP", "Calidus", "Saudi Chemical Company Limited"],
        "angle": "Cible export structurée — passages obligés pour vendre "
                 "à l'étranger. EDGE Group, conglomérat émirati de défense, "
                 "absorbe une part majeure des contrats locaux. Saudi "
                 "Chemical = explosifs souverains (programme SAMI 2030).",
    },
    {
        "n": "08",
        "title": "« Qui produit du matériel NRBC ? »",
        "buyer_role": "Commercial filtres / charbons actifs / capteurs gaz spécialisés",
        "filters": [
            ("Catégories produits", "NRBC (masques, tenues, détecteurs)"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(
            product_cat_in=["NRBC (masques, tenues, détecteurs)"],
            min_score=60,
        ),
        "examples": ["DRÄGER FRANCE SAS", "CRISTANINI", "AVEC CHEM S.R.O."],
        "angle": "Marché de niche en pleine croissance post-Ukraine. "
                 "Acheteurs Tier-1 de filtres NBC, charbons actifs, "
                 "détecteurs de gaz. Souvent spécifications militaires "
                 "spécifiques (STANAG 4555).",
    },
    {
        "n": "09",
        "title": "« Trouve-moi les munitionnaires européens »",
        "buyer_role": "Sous-traitant chimie / mécanique / certif AQAP cherchant des contrats",
        "filters": [
            ("Catégories produits",
             "Munitions petit/moyen calibre · Munitions gros calibre &amp; obus · "
             "Explosifs &amp; matériaux énergétiques"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(
            product_cat_in=["Munitions petit/moyen calibre",
                            "Munitions gros calibre & obus",
                            "Explosifs & matériaux énergétiques"],
            min_score=60,
        ),
        "examples": ["EURENCO S.A.S.", "FIOCCHI MUNIZIONI", "EXPLOSIA A.S."],
        "angle": "Le secteur munitions est en montée en cadence forte "
                 "(programmes 155mm, 5.56, 7.62). Tous achètent "
                 "matériaux énergétiques, mécanique de précision, "
                 "amorçages, certifications AQAP / NATO STANAG.",
    },
    {
        "n": "10",
        "title": "« Cybersécurité OT / infrastructures critiques »",
        "buyer_role": "Commercial qui vend audits ICS, SOC managé, NDR",
        "filters": [
            ("Catégories technologies",
             "Cybersécurité OT / ICS · Cybersécurité"),
            ("Score min", "≥ 60"),
        ],
        "filters_logic": dict(
            tech_cat_in=["Cybersécurité OT / ICS", "Cybersécurité"],
            min_score=60,
        ),
        "examples": ["CybExer Technologies", "GATEWATCHER", "GLIMPS"],
        "angle": "Le marché cyber défense (NIS2, COMCYBER, OIV) explose "
                 "en Europe. Cible double : (a) compétiteurs pour "
                 "benchmark ; (b) intégrateurs cherchant des briques "
                 "souveraines (NDR, malware analysis, cyber range).",
    },
]


# ---------------------------------------------------------------------------
# Filter resolution against the live DB
# ---------------------------------------------------------------------------


def _matching_count(records: list[dict], filt: dict) -> int:
    n = 0
    for r in records:
        if "product_cat_in" in filt:
            cats = " · ".join(r.get("products_categories") or [])
            if not any(c in cats for c in filt["product_cat_in"]):
                continue
        if "tech_cat_in" in filt:
            cats = " · ".join(r.get("technologies_categories") or [])
            if not any(c in cats for c in filt["tech_cat_in"]):
                continue
        if "svc_cat_in" in filt:
            cats = " · ".join(r.get("services_categories") or [])
            if not any(c in cats for c in filt["svc_cat_in"]):
                continue
        if "target_in" in filt:
            tb = r.get("target_buyers") or []
            if not any(c in tb for c in filt["target_in"]):
                continue
        if "country_in" in filt:
            if r.get("country") not in filt["country_in"]:
                continue
        if "min_score" in filt and r.get("completeness_score", 0) < filt["min_score"]:
            continue
        n += 1
    return n


def _find(records: list[dict], name: str) -> dict | None:
    needle = name.replace("&amp;", "&").lower().strip()
    for r in records:
        if r["company_name"].lower().strip() == needle:
            return r
    return None


# ---------------------------------------------------------------------------
# Use-case block builder
# ---------------------------------------------------------------------------


def _build_use_case(case: dict, S: dict, records: list[dict]) -> list:
    n = case["n"]
    title = case["title"]
    role = case["buyer_role"]
    n_match = _matching_count(records, case["filters_logic"])

    # === Top dark band (case number + title) ===
    head_data = [[
        Paragraph(f"<b>{n}</b>", S["case_n"]),
        Paragraph(
            f"<b>{title}</b><br/>"
            f"<font size='8.5' color='#B5C6D9'>"
            f"<i>{role}</i></font>",
            S["case_title"],
        ),
        Paragraph(
            f"<b>{n_match}</b><br/>"
            f"<font size='8' color='#B5C6D9'>sociétés</font>",
            S["case_title"],
        ),
    ]]
    head = Table(head_data, colWidths=[1.5*cm, 13*cm, 2.5*cm])
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (-1, 0), (-1, 0), "RIGHT"),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    # === Filter rows ===
    filt_rows = [[
        Paragraph(f"<b>{label}</b>", S["small"]),
        Paragraph(f"<font face='Courier'>{value}</font>", S["small"]),
    ] for label, value in case["filters"]]
    filt_table = Table(filt_rows, colWidths=[3.5*cm, 13.5*cm])
    filt_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID),
        ("BACKGROUND", (0, 0), (0, -1), BG_SOFT),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))

    # === Examples (3 hand-picked companies) ===
    ex_rows: list[list] = [[
        Paragraph("<b>Société</b>", S["small"]),
        Paragraph("<b>Pays</b>", S["small"]),
        Paragraph("<b>Activité (extrait)</b>", S["small"]),
        Paragraph("<b>Score</b>", S["small"]),
    ]]
    for name in case["examples"]:
        r = _find(records, name)
        if not r:
            ex_rows.append([
                Paragraph(name, S["small"]),
                Paragraph("?", S["small"]),
                Paragraph("(non trouvé)", S["small"]),
                Paragraph("—", S["small"]),
            ])
            continue
        # Truncate the activity to ~110 chars so the row stays one line
        act = (r.get("activity_1liner") or "")
        # Escape HTML special chars
        act = act.replace("&", "&amp;").replace("<", "&lt;")
        if len(act) > 110:
            act = act[:107].rstrip(" ,.;-") + "…"
        ex_rows.append([
            Paragraph(f"<b>{r['company_name'].replace('&', '&amp;')}</b>",
                      S["small"]),
            Paragraph(r.get("country") or "—", S["small"]),
            Paragraph(act, S["small"]),
            Paragraph(str(r.get("completeness_score", "?")), S["small"]),
        ])
    ex_table = Table(ex_rows, colWidths=[4*cm, 2*cm, 9.5*cm, 1.5*cm])
    ex_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), BG_SOFT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID),
        ("ALIGN", (-1, 1), (-1, -1), "CENTER"),
        ("FONT", (-1, 1), (-1, -1), "Helvetica-Bold", 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))

    # === Angle ===
    angle_para = Paragraph(
        "<b>Angle commercial.</b> "
        + case["angle"].replace("&", "&amp;"),
        S["body"],
    )

    # === Assemble (KeepTogether so a use case never splits across pages) ===
    block = [
        head,
        Spacer(1, 1*mm),
        Paragraph("<b>Filtres à appliquer dans Excel</b>", S["h2"]),
        filt_table,
        Spacer(1, 1*mm),
        Paragraph("<b>3 exemples concrets</b>", S["h2"]),
        ex_table,
        Spacer(1, 2*mm),
        angle_para,
        Spacer(1, 5*mm),
    ]
    return [KeepTogether(block)]


# ---------------------------------------------------------------------------
# Story
# ---------------------------------------------------------------------------


def build_story(S: dict, records: list[dict]) -> list:
    story: list = []
    # Cover page lead-in
    story.append(Spacer(1, 5.6*cm))
    story.append(Paragraph(
        "Ce document montre <b>10 questions concrètes</b> qu'un commercial "
        "défense peut poser à la base de données <b>Eurosatory 2026 "
        "Targeting</b>, avec :",
        S["body"],
    ))
    story.append(Spacer(1, 2*mm))
    bullets = [
        "✓ La <b>combinaison de filtres</b> exacte à appliquer dans Excel",
        "✓ Le <b>nombre de sociétés</b> qui matchent (calculé sur la base "
        "actuelle)",
        "✓ <b>3 exemples concrets</b> de sociétés tirés du fichier",
        "✓ L'<b>angle commercial</b> — pourquoi cette liste vaut quelque chose",
    ]
    for b in bullets:
        story.append(Paragraph(b, S["body"]))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        "Tous les filtres correspondent aux dropdowns de l'autofilter Excel "
        "déjà activé sur le fichier <b>Eurosatory_2026_targeting.xlsx</b>. "
        "Tu peux reproduire chaque cas en moins de 30 secondes.",
        S["caption"],
    ))
    story.append(Spacer(1, 3*mm))

    for case in USE_CASES:
        story.extend(_build_use_case(case, S, records))

    # Closing tip
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        "Combine les filtres entre eux", S["h1"],
    ))
    story.append(Paragraph(
        "Excel autorise des filtres simultanés sur n'importe quelle "
        "combinaison de colonnes. Quelques recettes utiles :",
        S["body"],
    ))
    recipes = [
        ("Cible précise", "Catégorie produit + Pays + Score ≥ 80"),
        ("Compétiteurs",
         "Même catégorie produit que ton offre + Score ≥ 80"),
        ("Acheteurs potentiels",
         "Catégorie produit = ta cible (ex : Drones) + Cible client = MoD"),
        ("Distribution / canal",
         "Catégorie services = Distribution &amp; représentation + Pays"),
        ("Pépites tech",
         "Catégorie technologie + Origine = manual (haute qualité)"),
    ]
    rows = [[
        Paragraph(f"<b>{l}</b>", S["body"]),
        Paragraph(r, S["body"]),
    ] for l, r in recipes]
    t = Table(rows, colWidths=[5*cm, 12*cm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID),
        ("BACKGROUND", (0, 0), (0, -1), BG_SOFT),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    return story


def main() -> int:
    out = EXPORT_DIR / "Eurosatory_2026_use_cases.pdf"
    S = _styles()
    records = json.loads(
        (EXPORT_DIR / "targeting_profiles_final.json").read_text(encoding="utf-8")
    )

    doc = BaseDocTemplate(
        str(out), pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title="Eurosatory 2026 — 10 use-cases commerciaux",
        author="Eurosatory 2026 Targeting CRM",
    )
    frame_cover = Frame(
        2*cm, 2*cm, A4[0] - 4*cm, A4[1] - 7*cm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    frame_normal = Frame(
        2*cm, 2*cm, A4[0] - 4*cm, A4[1] - 4*cm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame_cover],
                     onPage=_draw_first_page_header),
        PageTemplate(id="normal", frames=[frame_normal],
                     onPage=_draw_normal_page_header),
    ])
    story = build_story(S, records)
    doc.build(story)
    print(f"Wrote {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
