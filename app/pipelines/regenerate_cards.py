"""Re-derive the sales-card / targeting fields on existing intelligence rows
without re-crawling.  Useful when ``sales_card.py`` evolves.

Only touches: commercial_target_type, commercial_interest_level,
interest_reason, recommended_sales_angle, recommended_pitch,
probable_objections, prospecting_keywords, summary_for_sales.

Everything else (built_products, sold_offerings, defense_categories, scores,
sources) is preserved.
"""
from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy import select

from app.database import (
    CrawledPage,
    Exhibitor,
    ExhibitorIntelligence,
    session_scope,
)
from app.crm.affiliations import (
    extract_associations,
    extract_offices,
    extract_parent_group,
)
from app.crm.certifications import extract_certifications
from app.crm.firmographics import extract_firmographics
from app.crm.headline import extract_headline
from app.crm.linkedin_extract import extract_linkedin
from app.processors.defense_taxonomy import filter_defense_by_anchors
from app.processors.intelligence import (
    _looks_like_keyword_stuffing,
    _pick_prose_summary,
)
from app.processors.sales_card import derive_commercial_targeting


_META_DESC_RE = __import__("re").compile(
    r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']{30,500})["\']',
    __import__("re").IGNORECASE,
)
_OG_DESC_RE = __import__("re").compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]*content=["\']([^"\']{30,500})["\']',
    __import__("re").IGNORECASE,
)
_TITLE_RE = __import__("re").compile(
    r"<title[^>]*>([^<]{8,200})</title>", __import__("re").IGNORECASE
)


def _maybe_upgrade_summary(s, intel: ExhibitorIntelligence, exh: Exhibitor) -> bool:
    """Replace a keyword-stuffed (or empty) ``activity_summary`` with something
    more prose-like, derived from stored ``CrawledPage.text_excerpt``.

    Strategy:
    1. Try to parse visible body text and pick prose sentences (works when
       ``text_excerpt`` already holds plain text from a recent crawl).
    2. Fall back to ``<meta name="description">`` then ``og:description`` then
       ``<title>``: those land inside the first 4 KB of HTML head and survive
       even when ``text_excerpt`` is the truncated raw HTML from earlier crawls.

    Returns ``True`` if the summary was updated.
    """
    current = intel.activity_summary or ""
    if current and not _looks_like_keyword_stuffing(current):
        return False

    pages = list(
        s.execute(
            select(CrawledPage).where(
                CrawledPage.exhibitor_id == exh.id,
                CrawledPage.kind != "pdf",
                CrawledPage.text_excerpt.is_not(None),
            )
        ).scalars()
    )
    if not pages:
        return False

    from selectolax.parser import HTMLParser

    # 1) try visible body prose
    page_tuples: list[tuple[str, str, str]] = []
    for p in pages:
        if not p.text_excerpt:
            continue
        try:
            body = HTMLParser(p.text_excerpt).body
            text = body.text(separator=" ") if body else ""
        except Exception:  # noqa: BLE001
            text = ""
        text = " ".join(text.split())
        if text and len(text) > 80:
            page_tuples.append((p.kind or "page", p.url, text))
    prose = _pick_prose_summary(page_tuples, company_name_hint=exh.company_name)
    if prose:
        intel.activity_summary = prose[:1500]
        return True

    # 2) fall back to meta description / og:description / title — try ALL pages,
    # not just the homepage (some sites have a richer about-page meta tag).
    for p in sorted(pages, key=lambda p: 0 if p.kind == "homepage" else 1 if p.kind == "about" else 2):
        raw = p.text_excerpt or ""
        for regex in (_META_DESC_RE, _OG_DESC_RE):
            m = regex.search(raw)
            if m:
                desc = " ".join(m.group(1).split())
                if not _looks_like_keyword_stuffing(desc):
                    intel.activity_summary = desc[:1500]
                    return True

    # 3) last resort: any clean prose sentence found in any page
    for p in pages:
        raw = p.text_excerpt or ""
        prose = _pick_prose_summary(
            [(p.kind or "page", p.url, raw)], company_name_hint=exh.company_name
        )
        if prose:
            intel.activity_summary = prose[:1500]
            return True

    # 4) homepage <title> as graceful fallback
    homepage = next((p for p in pages if p.kind == "homepage"), pages[0])
    raw = homepage.text_excerpt or ""
    m = _TITLE_RE.search(raw)
    if m:
        title = " ".join(m.group(1).split())
        if title and not _looks_like_keyword_stuffing(title):
            intel.activity_summary = title[:1500]
            return True
    return False


def regenerate_all() -> dict:
    summary = {
        "processed": 0, "updated": 0, "summary_upgraded": 0,
        "categories_pruned": 0, "headlines_added": 0,
        "linkedin_added": 0, "founding_year_added": 0,
        "employee_hint_added": 0, "certifications_added": 0,
        "associations_added": 0, "parent_group_added": 0,
        "offices_added": 0,
    }
    with session_scope() as s:
        rows = list(
            s.execute(
                select(ExhibitorIntelligence, Exhibitor)
                .join(Exhibitor, Exhibitor.id == ExhibitorIntelligence.exhibitor_id)
            ).all()
        )
        for intel, exh in rows:
            summary["processed"] += 1
            if _maybe_upgrade_summary(s, intel, exh):
                summary["summary_upgraded"] += 1
            # Headline = the company's own one-liner from their homepage.
            # Recompute even when one already exists — the extractor improves
            # over time and cost is negligible.
            new_headline = extract_headline(
                s, exh.id, fallback_summary=intel.activity_summary,
            )
            if new_headline and new_headline != intel.headline:
                intel.headline = new_headline
                summary["headlines_added"] += 1

            # Backfill LinkedIn URL from crawled homepage if Exhibitor missing it
            if not exh.linkedin_url:
                li = extract_linkedin(s, exh.id)
                if li:
                    exh.linkedin_url = li
                    summary["linkedin_added"] += 1

            # Firmographics — founding_year + employee count hint
            if intel.founding_year is None or intel.employee_count_hint is None:
                firmo = extract_firmographics(s, exh.id)
                if firmo.founding_year and intel.founding_year is None:
                    intel.founding_year = firmo.founding_year
                    summary["founding_year_added"] += 1
                if firmo.employee_count_hint and intel.employee_count_hint is None:
                    intel.employee_count_hint = firmo.employee_count_hint
                    if firmo.employee_range and not intel.company_size_estimate:
                        intel.company_size_estimate = firmo.employee_range
                    summary["employee_hint_added"] += 1

            # Certifications — defense procurement filters on these
            if not intel.certifications:
                certs = extract_certifications(s, exh.id)
                if certs:
                    intel.certifications = certs
                    summary["certifications_added"] += 1

            # Industry associations / trade groups
            if not intel.industry_associations:
                assocs = extract_associations(s, exh.id)
                if assocs:
                    intel.industry_associations = assocs
                    summary["associations_added"] += 1

            # Parent group / subsidiary status
            if not intel.parent_group:
                parent = extract_parent_group(
                    s, exh.id, company_name=exh.company_name,
                )
                if parent:
                    intel.parent_group = parent
                    summary["parent_group_added"] += 1

            # Additional offices
            if not intel.additional_offices:
                offices = extract_offices(s, exh.id)
                if offices:
                    intel.additional_offices = offices
                    summary["offices_added"] += 1
            # Prune defense categories with no anchor built_product — kills the
            # holster-classified-as-AI/data class of false positives.
            original_cats = list(intel.defense_categories or [])
            pruned = filter_defense_by_anchors(original_cats, intel.built_products or [])
            if pruned != original_cats:
                intel.defense_categories = pruned
                summary["categories_pruned"] += 1
            targeting = derive_commercial_targeting(
                company_name=exh.company_name,
                country_iso2=exh.country_iso2,
                business_model=intel.business_model,
                built_products=intel.built_products or [],
                sold_offerings=intel.sold_offerings or [],
                technologies=intel.technologies or [],
                target_clients=intel.target_clients or [],
                markets_served=intel.markets_served or [],
                probable_buying_needs=intel.probable_buying_needs or [],
                activity_summary=intel.activity_summary,
                defense_categories=intel.defense_categories or [],
                defense_score=intel.defense_commercial_score,
            )
            intel.commercial_target_type = targeting.target_types
            intel.commercial_interest_level = targeting.interest_level
            intel.interest_reason = targeting.interest_reason
            intel.recommended_sales_angle = targeting.recommended_sales_angle
            intel.recommended_pitch = targeting.recommended_pitch
            intel.probable_objections = targeting.probable_objections
            intel.prospecting_keywords = targeting.prospecting_keywords
            intel.summary_for_sales = targeting.summary_for_sales
            intel.last_analyzed_at = datetime.utcnow()
            summary["updated"] += 1

    logger.info("regenerate-cards: {}", summary)
    return summary


def main() -> None:
    regenerate_all()


if __name__ == "__main__":  # pragma: no cover
    main()
