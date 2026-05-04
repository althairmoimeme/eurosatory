"""Pipeline: assign taxonomy labels and the commercial score to every exhibitor."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.database import (
    Exhibitor,
    ExhibitorCategory,
    ExhibitorClassification,
    ScrapingRun,
    session_scope,
)
from app.processors.classifier import (
    OTHER_LABEL,
    build_corpus,
    classify_exhibitor,
    keywords_from_corpus,
)
from app.processors.scoring import score_exhibitor


def _classify_one(s, exh: Exhibitor) -> tuple[list[str], dict]:
    cat_links = list(s.execute(select(ExhibitorCategory).where(ExhibitorCategory.exhibitor_id == exh.id)).scalars())
    cat_labels = [link.label_en or link.label_fr or "" for link in cat_links]

    exh_dict = {
        "company_name": exh.company_name,
        "one_liner": exh.one_liner,
        "short_presentation": exh.short_presentation,
        "presentation": exh.presentation,
        "business_areas": exh.business_areas or [],
    }
    classifications = classify_exhibitor(exh_dict, cat_labels)

    # replace previous classifications atomically
    s.query(ExhibitorClassification).filter_by(exhibitor_id=exh.id).delete()
    for c in classifications:
        s.add(
            ExhibitorClassification(
                exhibitor_id=exh.id,
                label=c.label,
                confidence=c.confidence,
                rationale=c.rationale,
            )
        )

    exh.last_classified_at = datetime.utcnow()
    labels = [c.label for c in classifications]

    # extract keywords for display
    corpus = build_corpus(exh_dict, cat_labels)
    exh.keywords = keywords_from_corpus(corpus)

    score = score_exhibitor(
        {
            **exh_dict,
            "country_iso2": exh.country_iso2,
            "website_url": exh.website_url,
            "contact_email": exh.contact_email,
            "phone": exh.phone,
            "linkedin_url": exh.linkedin_url,
            "address1": exh.address1,
            "city": exh.city,
            "is_featured": exh.is_featured,
            "is_new_exhibitor": exh.is_new_exhibitor,
            "is_lab": exh.is_lab,
            "stands": exh.stands,
            "field_confidence": exh.field_confidence or {},
            "generic_sales_email": exh.generic_sales_email,
            "employee_range": exh.employee_range,
        },
        labels,
    )
    exh.commercial_relevance_score = score.total
    exh.score_breakdown = {
        "components": score.breakdown,
        "explanation": score.explanation,
    }
    exh.priority_level = score.priority_level

    # if every label is OTHER and confidence is low -> needs review
    exh.needs_review = (
        len(labels) == 1 and labels[0] == OTHER_LABEL and score.total < 35
    )
    return labels, score.breakdown


def run_classify(limit: Optional[int] = None) -> dict:
    summary = {"processed": 0, "needs_review": 0}
    with session_scope() as s:
        run = ScrapingRun(kind="classify", status="running")
        s.add(run)
        s.flush()

        q = select(Exhibitor)
        if limit:
            q = q.limit(limit)
        for exh in s.execute(q).scalars():
            _classify_one(s, exh)
            summary["processed"] += 1
            if exh.needs_review:
                summary["needs_review"] += 1

        run.finished_at = datetime.utcnow()
        run.status = "ok"
        run.items_processed = summary["processed"]
        run.summary = summary

    logger.info("classify pipeline: {}", summary)
    return summary


def main() -> None:
    run_classify()


if __name__ == "__main__":  # pragma: no cover
    main()
