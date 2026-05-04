"""Pipeline: deep-crawl an exhibitor's site, extract structured intelligence,
optionally refine with Claude, score on the defense scale, persist.

Run order
---------
1. ``crawl_site`` collects priority HTML pages + a few public PDFs.
2. ``extract_pdf_text`` flattens PDFs.
3. ``extract_intelligence`` (rules) → first-pass intelligence.
4. ``classify_defense`` → defense_categories.
5. Optional: ``LLMIntelligenceRefiner.refine`` → tighter + sales-card fields.
6. ``score_defense`` → 0-100 defense score, A+/A/B/C/D priority, maturity.
7. ``derive_commercial_targeting`` (sales-card module) when LLM was unavailable.
8. Upsert into ``ExhibitorIntelligence`` and log every fetched URL into
   ``CrawledPage`` for audit.

The pipeline is idempotent on ``finderr_guid``: re-running updates the same
``ExhibitorIntelligence`` row.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.database import (
    CrawledPage,
    Exhibitor,
    ExhibitorIntelligence,
    ScrapingRun,
    session_scope,
)
from app.processors.defense_scorer import score_defense
from app.processors.defense_taxonomy import OTHER as DEFENSE_OTHER
from app.processors.defense_taxonomy import classify_defense_labels
from app.processors.intelligence import IntelligenceResult, extract_intelligence
from app.processors.llm_intelligence import (
    LLMIntelligenceRefiner,
    RefinedIntelligence,
)
from app.processors.sales_card import (
    CommercialTargeting,
    derive_commercial_targeting,
    render_sales_card,
)
from app.scrapers.deep_crawler import crawl_site
from app.scrapers.pdf_extractor import extract_pdf_text


def _truncate(text: Optional[str], n: int) -> Optional[str]:
    if not text:
        return None
    return text if len(text) <= n else text[:n]


def _html_to_clean_text(html: str) -> str:
    """Strip HTML to a readable plain-text excerpt.

    We keep meta description and title as a header, then append the visible body
    text — that way a downstream summary picker can find a one-liner pitch even
    when the body is mostly menu / footer noise.
    """
    if not html:
        return ""
    try:
        from selectolax.parser import HTMLParser

        parser = HTMLParser(html)
        parts: list[str] = []
        title_node = parser.css_first("title")
        if title_node and title_node.text(strip=True):
            parts.append(title_node.text(strip=True))
        for sel in ('meta[name="description"]', 'meta[property="og:description"]'):
            node = parser.css_first(sel)
            if node and (val := node.attributes.get("content")):
                parts.append(val.strip())
                break
        if parser.body:
            body_text = parser.body.text(separator=" ")
            parts.append(" ".join(body_text.split()))
        return " | ".join(p for p in parts if p)
    except Exception:  # noqa: BLE001
        return html


def _merge_with_llm(
    rules: IntelligenceResult, refined: RefinedIntelligence
) -> dict:
    """Combine rule-based and LLM outputs into a single dict ready for DB.

    Strategy: LLM wins on free-text fields and on classifications, but we
    *union* lists where both have signal so we don't lose evidence the LLM
    might have ignored.  Anything LLM left empty falls back to rules.
    """
    def _union(a: list, b: list) -> list:
        out: list = []
        for x in (a or []) + (b or []):
            if x and x not in out:
                out.append(x)
        return out

    return {
        "activity_summary": refined.activity_summary or rules.activity_summary,
        "built_products": _union(refined.built_products, rules.built_products),
        "built_product_summary": refined.built_product_summary or None,
        "sold_offerings": _union(refined.sold_offerings, rules.sold_offerings),
        "services": _union(refined.services, rules.services),
        "technologies": _union(refined.technologies, rules.technologies),
        "target_clients": _union(refined.target_clients, rules.target_clients),
        "markets_served": _union(refined.markets_served, rules.markets_served),
        "programs_use_cases": list(refined.programs_use_cases) or [],
        "business_model": refined.business_model or rules.business_model,
        "company_type": refined.company_type or rules.company_type,
        "company_size_estimate": refined.company_size_estimate,
        "probable_buying_needs": _union(
            refined.probable_buying_needs, rules.probable_buying_needs
        ),
        "buying_need_confidence": refined.buying_need_confidence or rules.buying_need_confidence,
        "buying_need_reasoning": refined.buying_need_reasoning or rules.buying_need_reasoning,
        "supplier_needs": list(refined.supplier_needs) or [],
        "partnership_opportunities": list(refined.partnership_opportunities) or [],
        "defense_categories": list(refined.defense_categories) or [],
        "commercial_target_type": list(refined.commercial_target_type) or [],
        "commercial_interest_level": refined.commercial_interest_level,
        "interest_reason": refined.interest_reason,
        "recommended_sales_angle": refined.recommended_sales_angle,
        "recommended_pitch": refined.recommended_pitch,
        "probable_objections": list(refined.probable_objections) or [],
        "prospecting_keywords": list(refined.prospecting_keywords) or [],
        "summary_for_sales": refined.summary_for_sales,
        "fields_to_verify": _union(refined.fields_to_verify, rules.fields_to_verify),
        "extraction_method": "mixed",
    }


def _from_rules_only(
    rules: IntelligenceResult, defense_labels: list[str], company_name: str,
    country_iso2: Optional[str], defense_total: float,
) -> dict:
    """Sales card / targeting derived from rules (no LLM available)."""
    targeting: CommercialTargeting = derive_commercial_targeting(
        company_name=company_name,
        country_iso2=country_iso2,
        business_model=rules.business_model,
        built_products=rules.built_products,
        sold_offerings=rules.sold_offerings,
        technologies=rules.technologies,
        target_clients=rules.target_clients,
        markets_served=rules.markets_served,
        probable_buying_needs=rules.probable_buying_needs,
        activity_summary=rules.activity_summary,
        defense_categories=defense_labels,
        defense_score=defense_total,
    )
    return {
        "activity_summary": rules.activity_summary,
        "built_products": rules.built_products,
        "built_product_summary": ", ".join(rules.built_products) if rules.built_products else None,
        "sold_offerings": rules.sold_offerings,
        "services": rules.services,
        "technologies": rules.technologies,
        "target_clients": rules.target_clients,
        "markets_served": rules.markets_served,
        "programs_use_cases": [],
        "business_model": rules.business_model,
        "company_type": rules.company_type,
        "company_size_estimate": None,
        "probable_buying_needs": rules.probable_buying_needs,
        "buying_need_confidence": rules.buying_need_confidence,
        "buying_need_reasoning": rules.buying_need_reasoning,
        "supplier_needs": [],
        "partnership_opportunities": [],
        "defense_categories": defense_labels,
        "commercial_target_type": targeting.target_types,
        "commercial_interest_level": targeting.interest_level,
        "interest_reason": targeting.interest_reason,
        "recommended_sales_angle": targeting.recommended_sales_angle,
        "recommended_pitch": targeting.recommended_pitch,
        "probable_objections": targeting.probable_objections,
        "prospecting_keywords": targeting.prospecting_keywords,
        "summary_for_sales": targeting.summary_for_sales,
        "fields_to_verify": rules.fields_to_verify,
        "extraction_method": "rules",
    }


async def _process_one(
    exhibitor_id: int,
    refiner: LLMIntelligenceRefiner,
    max_pages: int,
    max_pdfs: int,
) -> bool:
    with session_scope() as s:
        exh = s.get(Exhibitor, exhibitor_id)
        if exh is None or not exh.website_url:
            return False
        snapshot = {
            "id": exh.id,
            "guid": exh.finderr_guid,
            "name": exh.company_name,
            "country_iso2": exh.country_iso2,
            "website": exh.website_url,
            "eurosatory_description": exh.short_presentation or exh.presentation,
            "is_featured": exh.is_featured,
            "is_new_exhibitor": exh.is_new_exhibitor,
            "is_lab": exh.is_lab,
            "stands": exh.stands,
            "address1": exh.address1,
            "city": exh.city,
            "phone": exh.phone,
            "contact_email": exh.contact_email,
            "linkedin_url": exh.linkedin_url,
            "field_confidence": exh.field_confidence or {},
            "generic_sales_email": exh.generic_sales_email,
            "employee_range": exh.employee_range,
        }

    crawl = await crawl_site(snapshot["website"], max_pages=max_pages, max_pdfs=max_pdfs)
    pdf_extracts: list[tuple[str, str]] = []
    for pdf in crawl.pdfs:
        if pdf.content:
            t = extract_pdf_text(pdf.content, url=pdf.url)
            if t.text:
                pdf_extracts.append((pdf.url, t.text))

    page_texts = crawl.texts() + [(f"pdf:{u}", u, t) for u, t in pdf_extracts]
    rules = extract_intelligence(
        page_texts,
        base_summary=snapshot["eurosatory_description"],
        company_name_hint=snapshot["name"],
    )
    defense_labels = classify_defense_labels(
        " ".join(t for _, _, t in page_texts) + " " + (snapshot["eurosatory_description"] or "")
    )
    if defense_labels == [DEFENSE_OTHER] and rules.built_products:
        # rules already classified business; if defense regex fails but built_products exist,
        # keep "Other" but flag for review later.
        pass

    refined = refiner.refine(
        rule_based={
            "built_products": rules.built_products,
            "sold_offerings": rules.sold_offerings,
            "services": rules.services,
            "technologies": rules.technologies,
            "target_clients": rules.target_clients,
            "markets_served": rules.markets_served,
            "business_model": rules.business_model,
            "company_type": rules.company_type,
            "probable_buying_needs": rules.probable_buying_needs,
            "buying_need_confidence": rules.buying_need_confidence,
            "fields_to_verify": rules.fields_to_verify,
            "defense_categories": defense_labels,
        },
        context={
            "company": {
                "name": snapshot["name"],
                "country_iso2": snapshot["country_iso2"],
                "website": snapshot["website"],
                "eurosatory_description": snapshot["eurosatory_description"],
            },
            "pages": page_texts,
            "pdfs": pdf_extracts,
        },
    ) if refiner.is_enabled else None

    # rough first score using whatever we already have, so the sales card
    # can use it; we recompute after we know the LLM's interest_level.
    pre_score = score_defense(snapshot, {
        "defense_categories": defense_labels,
        "built_products": rules.built_products,
        "sold_offerings": rules.sold_offerings,
        "technologies": rules.technologies,
        "markets_served": rules.markets_served,
        "probable_buying_needs": rules.probable_buying_needs,
        "buying_need_confidence": rules.buying_need_confidence,
        "business_model": rules.business_model,
        "services": rules.services,
        "activity_summary": rules.activity_summary,
    })

    if refined is not None:
        merged = _merge_with_llm(rules, refined)
    else:
        merged = _from_rules_only(
            rules, defense_labels, snapshot["name"], snapshot["country_iso2"], pre_score.total
        )
    # ensure defense_categories is at least classification output
    if not merged.get("defense_categories"):
        merged["defense_categories"] = defense_labels

    # final score using merged output (LLM may have added evidence)
    final_score = score_defense(snapshot, merged)
    eurosatory_url = (
        f"https://eurosatory.finderr.cloud/standalone/catalog/company-details?company={snapshot['guid']}"
        if snapshot.get("guid") else None
    )

    field_sources = dict(rules.field_sources or {})
    if eurosatory_url:
        field_sources["eurosatory_profile_url"] = eurosatory_url
    for kind, url, _ in page_texts:
        field_sources.setdefault(f"page::{kind}", url)
    for url, _ in pdf_extracts:
        field_sources.setdefault(f"pdf::{url}", url)

    field_confidence = dict(rules.field_confidence or {})
    if refined is not None:
        for key in (
            "built_products", "sold_offerings", "technologies",
            "target_clients", "markets_served", "defense_categories",
            "probable_buying_needs",
        ):
            if merged.get(key):
                field_confidence[key] = "high"
        for key in (
            "recommended_pitch", "recommended_sales_angle",
            "summary_for_sales", "interest_reason",
        ):
            if merged.get(key):
                field_confidence[key] = "medium"

    with session_scope() as s:
        intel = s.scalar(
            select(ExhibitorIntelligence).where(
                ExhibitorIntelligence.exhibitor_id == exhibitor_id
            )
        )
        if intel is None:
            intel = ExhibitorIntelligence(exhibitor_id=exhibitor_id)
            s.add(intel)
        intel.eurosatory_profile_url = eurosatory_url
        for k, v in merged.items():
            if hasattr(intel, k):
                setattr(intel, k, v)
        intel.defense_commercial_score = final_score.total
        intel.defense_score_breakdown = {
            "components": final_score.breakdown,
            "explanation": final_score.explanation,
        }
        intel.defense_priority_level = final_score.priority_level
        intel.defense_maturity_score = final_score.maturity_score
        intel.field_sources = field_sources
        intel.field_confidence = field_confidence
        intel.last_crawled_at = datetime.utcnow()
        intel.last_analyzed_at = datetime.utcnow()

        # log crawled pages for audit
        s.query(CrawledPage).filter_by(exhibitor_id=exhibitor_id).delete()
        for page in crawl.pages:
            cleaned = _html_to_clean_text(page.html or "") if page.html else ""
            s.add(
                CrawledPage(
                    exhibitor_id=exhibitor_id,
                    url=page.url,
                    kind=page.kind,
                    status_code=page.status,
                    bytes=page.bytes_,
                    text_excerpt=_truncate(cleaned or (page.html or ""), 6000),
                    error=page.error,
                )
            )
        for pdf in crawl.pdfs:
            s.add(
                CrawledPage(
                    exhibitor_id=exhibitor_id,
                    url=pdf.url,
                    kind="pdf",
                    status_code=pdf.status,
                    bytes=pdf.bytes_,
                    text_excerpt=None,
                    error=pdf.error,
                )
            )

    return True


async def run_intelligence(
    limit: Optional[int] = None,
    only_missing: bool = True,
    max_pages: Optional[int] = None,
    max_pdfs: Optional[int] = None,
    concurrency: int = 3,
    use_llm: bool = True,
) -> dict:
    """Run the intelligence pipeline.

    ``only_missing`` skips exhibitors that already have an intelligence row.
    Set to ``False`` to refresh.
    """
    summary = {"requested": 0, "ok": 0, "errors": 0, "llm_enabled": False}
    refiner = LLMIntelligenceRefiner() if use_llm else LLMIntelligenceRefiner(api_key="")
    summary["llm_enabled"] = refiner.is_enabled

    with session_scope() as s:
        q = (
            select(Exhibitor.id)
            .where(Exhibitor.website_url.is_not(None))
            .order_by(Exhibitor.commercial_relevance_score.desc().nulls_last())
        )
        if only_missing:
            already = select(ExhibitorIntelligence.exhibitor_id)
            q = q.where(Exhibitor.id.notin_(already))
        if limit:
            q = q.limit(limit)
        ids = [r[0] for r in s.execute(q).all()]

    summary["requested"] = len(ids)
    if not ids:
        logger.info("intelligence pipeline: nothing to do")
        return summary

    sem = asyncio.Semaphore(concurrency)

    async def _wrapped(eid: int) -> bool:
        async with sem:
            try:
                return await _process_one(eid, refiner, max_pages or 12, max_pdfs or 2)
            except Exception as e:  # noqa: BLE001
                logger.warning("intelligence pipeline failed for {}: {}", eid, e)
                return False

    with session_scope() as s:
        run = ScrapingRun(kind="intelligence", status="running")
        s.add(run)
        s.flush()
        run_id = run.id

    results = await asyncio.gather(*(_wrapped(i) for i in ids))
    summary["ok"] = sum(1 for r in results if r)
    summary["errors"] = sum(1 for r in results if not r)

    with session_scope() as s:
        run = s.get(ScrapingRun, run_id)
        if run is not None:
            run.finished_at = datetime.utcnow()
            run.status = "ok"
            run.items_processed = summary["requested"]
            run.items_succeeded = summary["ok"]
            run.items_failed = summary["errors"]
            run.summary = summary

    logger.info("intelligence pipeline: {}", summary)
    return summary


def render_card(exhibitor_id: int) -> Optional[str]:
    """Render the verbatim sales card for an exhibitor (for CLI / API)."""
    with session_scope() as s:
        exh = s.get(Exhibitor, exhibitor_id)
        intel = s.scalar(
            select(ExhibitorIntelligence).where(
                ExhibitorIntelligence.exhibitor_id == exhibitor_id
            )
        )
        if exh is None or intel is None:
            return None
        return render_sales_card(
            company_name=exh.company_name,
            country_iso2=exh.country_iso2,
            activity_summary=intel.activity_summary,
            built_products=intel.built_products or [],
            sold_offerings=intel.sold_offerings or [],
            probable_buying_needs=intel.probable_buying_needs or [],
            interest_reason=intel.interest_reason or "",
            sales_angle=intel.recommended_sales_angle or "",
            pitch=intel.recommended_pitch or "",
            priority_level=intel.defense_priority_level,
            fields_to_verify=intel.fields_to_verify or [],
            field_sources=intel.field_sources or {},
        )


def main() -> None:
    asyncio.run(run_intelligence())


if __name__ == "__main__":  # pragma: no cover
    main()
