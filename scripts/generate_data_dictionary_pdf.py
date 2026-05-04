"""Generate the customer-facing PDF data dictionary.

This is the second deliverable in the bundle (alongside the XLSX). It
explains every column the buyer sees in ``Eurosatory_2026_targeting.xlsx``
so they can filter / interpret with confidence and trust the data.

Sections
--------
1. Vue d'ensemble du livrable
2. Méthodologie de collecte et d'enrichissement
3. Le 6-champs de ciblage (activité, produits, services, cibles, technos, pourquoi)
4. Catégories canoniques (75 produits / 23 services / 31 technos)
5. Score de complétude — formule détaillée
6. Origine de la donnée (manual / high / medium / low / very_low)
7. Statistiques globales (snapshot du run actuel)
8. Mode d'emploi des filtres Excel

Output : ``data/exports/Eurosatory_2026_dictionnaire.pdf``
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

# Theme colours — keep aligned with the Streamlit header (Palantir-ish)
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
            alignment=0, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "ST", parent=base["Normal"], fontName="Helvetica",
            fontSize=11, leading=14, textColor=colors.HexColor("#B5C6D9"),
        ),
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=15, leading=20, textColor=NAVY,
            spaceBefore=14, spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11.5, leading=15, textColor=NAVY_SOFT,
            spaceBefore=10, spaceAfter=2,
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
            backColor=BG_SOFT, borderPadding=4,
        ),
    }


# ---------------------------------------------------------------------------
# Header / footer painter
# ---------------------------------------------------------------------------


def _draw_first_page_header(canvas, doc):
    canvas.saveState()
    # Full-bleed dark band with title
    canvas.setFillColor(BG_NAVY)
    canvas.rect(0, A4[1] - 5*cm, A4[0], 5*cm, fill=1, stroke=0)
    # Title
    canvas.setFont("Helvetica-Bold", 24)
    canvas.setFillColor(WHITE)
    canvas.drawString(2*cm, A4[1] - 2.4*cm,
                      "Eurosatory 2026 — Dictionnaire des données")
    canvas.setFont("Helvetica", 10.5)
    canvas.setFillColor(colors.HexColor("#B5C6D9"))
    canvas.drawString(2*cm, A4[1] - 3.2*cm,
                      "Defense commercial intelligence · 2580 exhibitors · "
                      "75 catégories produits · 23 services · 31 technos · "
                      "5 cibles")
    canvas.setFont("Helvetica", 9)
    canvas.drawString(2*cm, A4[1] - 4.0*cm,
                      "Document de référence livré avec le fichier "
                      "Eurosatory_2026_targeting.xlsx")
    # Right brand mark
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(WHITE)
    canvas.drawRightString(A4[0] - 2*cm, A4[1] - 4.0*cm,
                           "EUROSATORY · 2026")
    canvas.restoreState()
    _draw_footer(canvas, doc)


def _draw_normal_page_header(canvas, doc):
    canvas.saveState()
    # Slim navy band with running title
    canvas.setFillColor(NAVY)
    canvas.rect(0, A4[1] - 1.4*cm, A4[0], 1.4*cm, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 9.5)
    canvas.setFillColor(WHITE)
    canvas.drawString(2*cm, A4[1] - 0.95*cm,
                      "Eurosatory 2026 — Dictionnaire des données")
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
# Builders
# ---------------------------------------------------------------------------


def _kpi_table(stats: dict, S: dict) -> Table:
    rows = [
        ["Total sociétés", "Score moyen", "≥ 80", "≥ 60", "Origine manuelle"],
        [
            f"{stats['n']:,}".replace(",", " "),
            f"{stats['avg']:.1f} / 100",
            f"{stats['ge80']} ({100*stats['ge80']/stats['n']:.1f}%)",
            f"{stats['ge60']} ({100*stats['ge60']/stats['n']:.1f}%)",
            f"{stats['n_manual']}",
        ],
    ]
    t = Table(rows, colWidths=[3.4*cm]*5)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 14),
        ("TEXTCOLOR", (0, 1), (-1, 1), NAVY),
        ("BACKGROUND", (0, 1), (-1, 1), BG_SOFT),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, 1), [BG_SOFT]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, NAVY),
        ("BOX", (0, 0), (-1, -1), 0.25, GRID),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _two_col_table(rows: list[tuple[str, str]], S: dict, col1_w=4.5*cm) -> Table:
    """Render a key/value table with wrapping in the value column."""
    data = []
    for k, v in rows:
        data.append([
            Paragraph(f"<b>{k}</b>", S["body"]),
            Paragraph(v, S["body"]),
        ])
    t = Table(data, colWidths=[col1_w, 17*cm - col1_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (0, -1), BG_SOFT),
    ]))
    return t


def _category_columns_table(items: list[str], S: dict, n_cols: int = 3) -> Table:
    """Render a categories list in N columns, balanced."""
    # Pad to multiple of n_cols
    while len(items) % n_cols:
        items.append("")
    n_rows = len(items) // n_cols
    rows = []
    for r in range(n_rows):
        rows.append([
            Paragraph(items[c * n_rows + r], S["small"])
            for c in range(n_cols)
        ])
    col_w = 17*cm / n_cols
    t = Table(rows, colWidths=[col_w]*n_cols)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _score_formula_table(S: dict) -> Table:
    rows = [
        ["+15", "Headline (description ≥ 25c, non-noise)"],
        ["+25", "Activité 1 ligne renseignée et exploitable"],
        ["+20", "≥ 3 produits concrets identifiés"],
        ["+15", "≥ 1 cible client identifiée"],
        ["+15", "≥ 1 technologie identifiée"],
        ["+10", "Pourquoi cibler renseigné et actionnable"],
    ]
    data = [[
        Paragraph(f"<b>{p}</b>", S["body"]),
        Paragraph(d, S["body"]),
    ] for p, d in rows]
    # Final "100" row uses an explicit white-on-navy paragraph style so
    # the colour actually applies (TableStyle TEXTCOLOR is overridden by
    # Paragraph's own style).
    white_style = ParagraphStyle(
        "white_total", parent=S["body"], fontName="Helvetica-Bold",
        fontSize=10.5, textColor=WHITE,
    )
    data.append([
        Paragraph("<b>100</b>", white_style),
        Paragraph("<b>Total — fiche &quot;vendable&quot; complète</b>",
                  white_style),
    ])
    t = Table(data, colWidths=[2*cm, 15*cm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, GRID),
        ("BACKGROUND", (0, -1), (-1, -1), NAVY),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _source_strength_table(S: dict) -> Table:
    rows = [
        ["manual", colors.HexColor("#1F7A4D"),
         "Fiche enrichie à la main par notre équipe — qualité maximale, "
         "vocabulaire commercial, validation cohérence inter-fiches."],
        ["high", colors.HexColor("#5D9CEC"),
         "Données rule-based avec headline + crawl 5+ pages + ≥ 2 signaux "
         "(produits / cibles)."],
        ["medium", colors.HexColor("#F1B656"),
         "Données rule-based avec headline propre OU crawl substantiel "
         "OU signaux partiels."],
        ["low", colors.HexColor("#E07A5F"),
         "Headline absente / corrompue OU peu de signaux. La fiche peut "
         "afficher des produits trop génériques."],
        ["very_low", colors.HexColor("#7B2D2D"),
         "Pas de site, pas de description Finderr — on identifie la "
         "société par son nom et son pavillon, pas plus."],
    ]
    data = []
    for label, col, desc in rows:
        data.append([
            Paragraph(f"<font color='white'><b>{label}</b></font>", S["body"]),
            Paragraph(desc, S["body"]),
        ])
    t = Table(data, colWidths=[3*cm, 14*cm])
    style_cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]
    for i, (_, col, _) in enumerate(rows):
        style_cmds.append(("BACKGROUND", (0, i), (0, i), col))
    t.setStyle(TableStyle(style_cmds))
    return t


def _target_buyers_table(S: dict) -> Table:
    rows = [
        ["MoD / Armées",
         "Ministères de la défense et armées régulières (FR, DE, NL, UK, US, "
         "etc.) achetant pour leurs forces."],
        ["Primes défense",
         "Grands intégrateurs (Thales, Airbus DS, KNDS, Rheinmetall, BAE, "
         "Lockheed, Leonardo, …) qui sous-traitent."],
        ["Sécurité civile",
         "Police, gendarmerie, douanes, pompiers, SAR, sécurité privée, "
         "protection critique."],
        ["Industriels défense",
         "Sous-traitants Tier-1/Tier-2 (mécanique, électronique, "
         "composites, optronique de second rang)."],
        ["Export / international",
         "Forces armées étrangères hors UE, marchés export structurés "
         "(Moyen-Orient, Asie, Afrique, Amérique latine)."],
    ]
    data = [[
        Paragraph(f"<b>{l}</b>", S["body"]),
        Paragraph(d, S["body"]),
    ] for l, d in rows]
    t = Table(data, colWidths=[4.5*cm, 12.5*cm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (0, -1), BG_SOFT),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


# ---------------------------------------------------------------------------
# Stats from current run
# ---------------------------------------------------------------------------


def _gather_stats() -> dict:
    src = EXPORT_DIR / "targeting_profiles_final.json"
    records = json.loads(src.read_text(encoding="utf-8"))
    n = len(records)
    avg = sum(r["completeness_score"] for r in records) / max(n, 1)
    ge80 = sum(1 for r in records if r["completeness_score"] >= 80)
    ge60 = sum(1 for r in records if r["completeness_score"] >= 60)
    n_manual = sum(1 for r in records if r["data_source_strength"] == "manual")
    return {
        "n": n, "avg": avg, "ge80": ge80, "ge60": ge60,
        "n_manual": n_manual,
    }


# ---------------------------------------------------------------------------
# Main story
# ---------------------------------------------------------------------------


def build_story(S: dict, stats: dict) -> list:
    from app.processors.taxonomy_normalize import (  # noqa: WPS433
        PRODUCT_CATEGORIES, SERVICE_CATEGORIES, TECHNOLOGY_CATEGORIES,
    )

    story: list = []

    # ---- Page 1 : start under the dark header ----
    story.append(Spacer(1, 5.6*cm))
    story.append(Paragraph("Vue d'ensemble du livrable", S["h1"]))
    story.append(Paragraph(
        "Ce dictionnaire accompagne le fichier "
        "<b>Eurosatory_2026_targeting.xlsx</b> — la base de données "
        "commerciale enrichie de tous les exposants annoncés au salon "
        "Eurosatory 2026. Il vous permet de comprendre ce que chaque "
        "colonne contient, comment elle a été produite, et comment "
        "l'utiliser pour cibler vos prospects en 10 secondes par fiche.",
        S["body"],
    ))
    story.append(Spacer(1, 4*mm))
    story.append(_kpi_table(stats, S))

    story.append(Paragraph("Méthodologie de collecte", S["h1"]))
    story.append(Paragraph(
        "Les données sont collectées et fusionnées en quatre étapes :",
        S["body"],
    ))
    story.append(_two_col_table([
        ("1. Source officielle Finderr",
         "L'API Finderr (officielle Eurosatory) fournit la liste des "
         "exposants validés avec leurs métadonnées primaires : nom, "
         "pays, pavillon, site officiel, courte description, halls."),
        ("2. Crawl ciblé du site officiel",
         "Pour chaque société, le site officiel est crawlé (homepage + "
         "jusqu'à 4 pages clés : produits, services, à propos, contact). "
         "Le contenu est extrait en respectant les règles robots.txt et "
         "les délais inter-requêtes (1s/host)."),
        ("3. Extraction règle-basée",
         "Un transformeur déterministe convertit les signaux extraits "
         "(headline, mots-clés, business areas) en 6 champs commerciaux "
         "exploitables : activité (1 ligne), produits, services, cibles, "
         "technologies, pourquoi cibler."),
        ("4. Enrichissement manuel",
         f"Les fiches à fort enjeu commercial (primes, sub-tiers Tier-1, "
         f"PME défense françaises) sont systématiquement validées et "
         f"enrichies manuellement par notre équipe : "
         f"<b>{stats['n_manual']} fiches manuelles</b> sur les "
         f"{stats['n']} (≈{100*stats['n_manual']/stats['n']:.0f}%)."),
    ], S))

    story.append(PageBreak())

    # ---- Le 6-champs ----
    story.append(Paragraph("Les six champs de ciblage", S["h1"]))
    story.append(Paragraph(
        "Chaque fiche contient ces six champs commerciaux, conçus pour "
        "qu'un commercial défense puisse <b>qualifier la société en "
        "10 secondes</b> :",
        S["body"],
    ))
    story.append(_two_col_table([
        ("Activité (1 ligne)",
         "Une phrase ≤ 140 caractères, en français, qui commence par un "
         "verbe d'action conjugué (Conçoit, Fabrique, Édite, Distribue, "
         "Intègre, Forme, Maintient, …) et résume précisément ce que la "
         "société FAIT vraiment — pas du marketing."),
        ("Produits",
         "Liste de 3 à 7 produits physiques ou logiciels concrets que la "
         "société manufacture ou édite. Spécifiques (ex : « viseur ACOG "
         "4x32 », « drone HALE Akinci ») — jamais marketing-vague (ex : "
         "« solutions innovantes »)."),
        ("Services",
         "Liste de 0 à 5 services commerciaux que la société vend (et qui "
         "ne sont pas des produits). Exemples : MCO, formation, "
         "intégration, certification, distribution."),
        ("Cibles clients",
         "Une à quatre valeurs choisies dans une <b>taxonomie fermée à "
         "5 valeurs</b> — on ne peut pas inventer une cible. C'est ce "
         "qui rend le filtrage Excel utile."),
        ("Technologies",
         "Liste de 0 à 5 technologies que la société maîtrise "
         "<i>démontrablement</i> (pas marketing). Exemples : IA / vision, "
         "RF jamming, composites carbone, radar AESA, imagerie SWIR."),
        ("Pourquoi cibler",
         "Une phrase contextuelle qui répond : « pourquoi un commercial "
         "qui vend X devrait s'intéresser à cette société ? ». Cinq "
         "angles : acheteur potentiel · compétiteur direct · intégrateur "
         "partenaire · canal de distribution · cible export."),
    ], S, col1_w=4*cm))

    # ---- Cibles fermées ----
    story.append(Paragraph("Cibles clients — taxonomie fermée à 5 valeurs",
                           S["h1"]))
    story.append(Paragraph(
        "C'est la colonne la plus utile pour filtrer. Aucune autre valeur "
        "ne peut apparaître dans cette colonne — toute fiche y a au plus "
        "4 valeurs choisies parmi :",
        S["body"],
    ))
    story.append(_target_buyers_table(S))

    story.append(PageBreak())

    # ---- Catégories ----
    story.append(Paragraph(
        "Catégories canoniques (filtres Excel)", S["h1"],
    ))
    story.append(Paragraph(
        "Pour rendre le filtrage utilisable, chaque produit / service / "
        "technologie est ALSO mappé sur une catégorie canonique. Les "
        "valeurs spécifiques (« viseur ACOG 4x32 ») restent visibles "
        "dans la cellule pour la lecture, mais le filtre Excel opère sur "
        "les catégories qui agrègent toutes les variantes.",
        S["body"],
    ))
    story.append(Paragraph(
        f"<b>{len([c for c in PRODUCT_CATEGORIES if not c.startswith('Autre')])} "
        f"catégories produits</b>", S["h2"],
    ))
    products_escaped = [
        c.replace("&", "&amp;")
        for c in PRODUCT_CATEGORIES if not c.startswith("Autre")
    ]
    story.append(_category_columns_table(products_escaped, S, n_cols=3))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        f"<b>{len([c for c in SERVICE_CATEGORIES if not c.startswith('Autre')])} "
        f"catégories services</b>", S["h2"],
    ))
    # Escape ampersands in service category labels — Paragraph treats `&`
    # as the start of an HTML entity (e.g. "R&D" was read as "R&D;").
    services_escaped = [
        c.replace("&", "&amp;")
        for c in SERVICE_CATEGORIES if not c.startswith("Autre")
    ]
    story.append(_category_columns_table(services_escaped, S, n_cols=3))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        f"<b>{len([c for c in TECHNOLOGY_CATEGORIES if not c.startswith('Autre')])} "
        f"catégories technologies</b>", S["h2"],
    ))
    tech_escaped = [
        c.replace("&", "&amp;")
        for c in TECHNOLOGY_CATEGORIES if not c.startswith("Autre")
    ]
    story.append(_category_columns_table(tech_escaped, S, n_cols=3))

    story.append(PageBreak())

    # ---- Score ----
    story.append(Paragraph("Score de complétude — formule", S["h1"]))
    story.append(Paragraph(
        "Chaque fiche a un score 0 → 100 mesurant la qualité commerciale "
        "de l'extraction. Le score n'évalue PAS la pertinence de la "
        "société — il mesure UNIQUEMENT à quel point la fiche est "
        "exploitable pour un commercial. Une fiche à 100 contient :",
        S["body"],
    ))
    story.append(_score_formula_table(S))
    story.append(Paragraph(
        "<b>Conseil d'usage</b> : démarre tes filtres avec un seuil score "
        "≥ 60 — au-dessous, les fiches manquent souvent un élément clé "
        "et le commercial doit qualifier en discovery call.",
        S["caption"],
    ))

    # ---- Source strength ----
    story.append(Paragraph("Origine de la donnée", S["h1"]))
    story.append(Paragraph(
        "La colonne <b>Origine</b> indique le mode de production de la "
        "fiche. Les valeurs sont, du plus fiable au plus indicatif :",
        S["body"],
    ))
    story.append(_source_strength_table(S))

    # ---- How to filter ----
    story.append(Paragraph("Mode d'emploi des filtres Excel", S["h1"]))
    story.append(Paragraph(
        "L'onglet principal a l'autofilter Excel activé sur chaque "
        "colonne. Pour trouver des cibles :",
        S["body"],
    ))
    story.append(_two_col_table([
        ("Ouvrir l'autofilter",
         "Clique sur la flèche ▼ dans l'en-tête de colonne. Coche les "
         "valeurs que tu veux conserver."),
        ("Filtrer par CATÉGORIE plutôt que par produit spécifique",
         "Pour « tous les fabricants de drones », filtre sur "
         "<b>Catégories produits → Drones aériens (UAV)</b>, pas sur "
         "<b>Produits</b> (qui contient des libellés spécifiques différents "
         "pour chaque société)."),
        ("Combiner plusieurs colonnes",
         "Excel autorise le filtrage simultané sur plusieurs colonnes. "
         "Ex : Catégories produits = « Optronique & viseurs » ET Pays = "
         "« France » ET Score ≥ 80."),
        ("Trier par Score décroissant",
         "Le tri par défaut est déjà score décroissant — les fiches du "
         "haut sont les plus complètes."),
        ("Voir le 2e onglet « Long tail »",
         "Les 60 fiches restant à enrichir sont isolées dans le 2e "
         "onglet. Elles sont mises à jour à chaque release."),
    ], S))

    return story


def main() -> int:
    out = EXPORT_DIR / "Eurosatory_2026_dictionnaire.pdf"
    S = _styles()
    stats = _gather_stats()

    # Build a 2-template doc: cover page (big header) + content pages (slim
    # running header).
    doc = BaseDocTemplate(
        str(out), pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title="Eurosatory 2026 — Dictionnaire des données",
        author="Eurosatory 2026 Targeting CRM",
    )
    frame_cover = Frame(
        2*cm, 2*cm, A4[0] - 4*cm, A4[1] - 7*cm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        showBoundary=0,
    )
    frame_normal = Frame(
        2*cm, 2*cm, A4[0] - 4*cm, A4[1] - 4*cm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        showBoundary=0,
    )
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame_cover],
                     onPage=_draw_first_page_header),
        PageTemplate(id="normal", frames=[frame_normal],
                     onPage=_draw_normal_page_header),
    ])

    story = build_story(S, stats)
    doc.build(story)
    print(f"Wrote {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
