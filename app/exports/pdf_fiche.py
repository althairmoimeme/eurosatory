"""Generate a 1-page PDF for a single company fiche.

Used from the Streamlit detail card via the "📄 Export PDF" button.  The
output is a single-page A4 PDF with: logo (if available) + identity block +
headline + key fields (build/sell/buying/cert) + sales pitch + sources.

Renders entirely with `fpdf2` — no headless browser, no LaTeX.  Latin-1 only
to keep the binary tiny; non-latin characters fall back to '?'.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

from fpdf import FPDF


_NAVY = (7, 26, 51)
_NAVY_SOFT = (11, 46, 74)
_STEEL = (47, 93, 124)
_LIGHT_BG = (244, 246, 248)
_TEXT = (43, 47, 51)
_ALERT = (193, 18, 31)


class _CompanyPdf(FPDF):
    def header(self) -> None:
        # Solid navy band at the top
        self.set_fill_color(*_NAVY)
        self.rect(0, 0, 210, 14, "F")
        self.set_y(4)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 6, "Eurosatory 2026 Targeting CRM", align="L")
        self.set_font("Helvetica", "", 9)
        self.cell(0, 6, "Fiche société", align="R")

    def footer(self) -> None:
        self.set_y(-12)
        self.set_text_color(150, 150, 150)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 6, _safe(f"Généré automatiquement - page {self.page_no()}"),
                  align="C")


_REPLACEMENTS = str.maketrans({
    # Smart quotes / dashes / ellipsis → ASCII fallbacks Helvetica understands.
    "\u2014": "-", "\u2013": "-",  # em / en dash
    "\u2018": "'", "\u2019": "'",  # curly single quotes
    "\u201C": '"', "\u201D": '"',  # curly double quotes
    "\u00AB": '"', "\u00BB": '"',  # guillemets
    "\u2022": "-",                  # bullet
    "\u2026": "...",               # ellipsis
    "\u00A0": " ",                  # nbsp
})


def _safe(text: Any) -> str:
    """fpdf2 with the default Helvetica font is Latin-1 only.  Convert smart
    typography to ASCII first, then drop anything still outside Latin-1.
    """
    if text is None:
        return ""
    s = str(text).translate(_REPLACEMENTS)
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _section_title(pdf: FPDF, label: str) -> None:
    pdf.ln(2)
    pdf.set_text_color(*_STEEL)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 5, _safe(label.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_STEEL)
    pdf.set_line_width(0.3)
    pdf.line(pdf.get_x(), pdf.get_y(), 200, pdf.get_y())
    pdf.ln(1.5)


def _kv(pdf: FPDF, key: str, value: Any) -> None:
    pdf.set_text_color(*_NAVY)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(38, 4.5, _safe(key))
    pdf.set_text_color(*_TEXT)
    pdf.set_font("Helvetica", "", 9)
    # ``wrapmode="CHAR"`` lets fpdf break URLs / long tokens mid-word so we
    # never hit "not enough horizontal space" on long source URLs.
    pdf.multi_cell(
        0, 4.5, _safe(value or "—"),
        wrapmode="CHAR", new_x="LMARGIN", new_y="NEXT",
    )


def _block(pdf: FPDF, text: str) -> None:
    pdf.set_text_color(*_TEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(
        0, 4.5, _safe(text),
        wrapmode="CHAR", new_x="LMARGIN", new_y="NEXT",
    )


def render_company_pdf(row: dict[str, Any]) -> bytes:
    """Render a single CRM row to a PDF (bytes).

    ``row`` is the dict produced by ``app.crm.transformer.to_crm`` — already
    contains every CRM column we need.
    """
    pdf = _CompanyPdf(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=12, top=20, right=12)
    pdf.add_page()

    # ----- Identity strip -----
    pdf.set_text_color(*_NAVY)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 8, _safe(row.get("account_name") or "Société"),
             new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*_TEXT)
    pdf.set_font("Helvetica", "", 9)
    sub = " · ".join(
        x for x in (
            row.get("country"),
            row.get("city"),
            row.get("account_id"),
            f"booth: {row.get('booth_number')}" if row.get("booth_number") else None,
        ) if x
    )
    pdf.cell(0, 5, _safe(sub), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    # Priority + score badge band
    prio = row.get("priority_level") or "D"
    score = row.get("lead_score") or 0
    pdf.set_fill_color(*_ALERT)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(20, 6, f" {prio} ", fill=True)
    pdf.set_fill_color(*_NAVY_SOFT)
    pdf.cell(40, 6, f" Score {score:.0f} / 100 ", fill=True)
    pdf.set_fill_color(*_STEEL)
    pdf.cell(50, 6, f" {row.get('data_confidence') or '—'} confidence ", fill=True)
    pdf.ln(8)

    # ----- Headline (own words) -----
    headline = (row.get("headline") or "").strip()
    if headline and not headline.startswith("À enrichir"):
        pdf.set_fill_color(*_LIGHT_BG)
        pdf.set_text_color(*_NAVY)
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_x(12)
        pdf.cell(186, 6, _safe(f'"{headline}"'), fill=True,
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # ----- Identity block -----
    _section_title(pdf, "Identity")
    _kv(pdf, "Pays", row.get("country"))
    _kv(pdf, "Site web", row.get("website_url"))
    _kv(pdf, "LinkedIn", row.get("linkedin_company_url"))
    _kv(pdf, "Booth", row.get("booth_number"))
    _kv(pdf, "Type", row.get("company_type"))
    _kv(pdf, "Business model", row.get("business_model"))
    _kv(pdf, "Taille", row.get("company_size"))
    fy = row.get("founding_year")
    if fy and not (isinstance(fy, float) and fy != fy):  # not NaN
        try:
            _kv(pdf, "Fondée", str(int(fy)))
        except (ValueError, TypeError):
            pass
    if row.get("parent_group"):
        _kv(pdf, "Parent group", row.get("parent_group"))

    # ----- What they do -----
    _section_title(pdf, "What they do")
    if row.get("core_business"):
        _kv(pdf, "Cœur de métier", row.get("core_business"))
    if row.get("description_short"):
        _block(pdf, row["description_short"])
    if row.get("products_built"):
        _kv(pdf, "Fabrique", row["products_built"])
    if row.get("products_sold"):
        _kv(pdf, "Vend", row["products_sold"])
    if row.get("services_sold"):
        _kv(pdf, "Services", row["services_sold"])
    if row.get("technologies"):
        _kv(pdf, "Technos", row["technologies"])
    if row.get("certifications"):
        _kv(pdf, "Certifications", row["certifications"])
    if row.get("industry_associations"):
        _kv(pdf, "Associations", row["industry_associations"])
    if row.get("markets_served"):
        _kv(pdf, "Marchés", row["markets_served"])

    # ----- Why a target -----
    _section_title(pdf, "Why a target")
    _kv(pdf, "Target type", row.get("target_type"))
    _kv(pdf, "Buying need", row.get("buying_need_main"))
    if row.get("commercial_relevance_summary"):
        _block(pdf, row["commercial_relevance_summary"])

    # ----- Sales approach -----
    _section_title(pdf, "Sales approach")
    if row.get("ideal_seller_profile"):
        _kv(pdf, "Ideal seller", row["ideal_seller_profile"])
    if row.get("recommended_sales_angle"):
        _kv(pdf, "Sales angle", row["recommended_sales_angle"])
    if row.get("short_pitch"):
        _kv(pdf, "Pitch", row["short_pitch"])
    if row.get("next_best_action"):
        _kv(pdf, "Next action", row["next_best_action"])

    # ----- Sources -----
    if row.get("source_urls"):
        _section_title(pdf, "Sources")
        _block(pdf, row["source_urls"])

    out = pdf.output(dest="S")
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    # fpdf2 returns str on older versions
    return out.encode("latin-1", errors="replace")
