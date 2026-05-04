"""Quality report: counts, completeness, dedup candidates."""
from __future__ import annotations

from collections import Counter
from typing import Any

from loguru import logger
from sqlalchemy import func, select

from app.database import (
    CrawledPage,
    Exhibitor,
    ExhibitorClassification,
    ExhibitorIntelligence,
    session_scope,
)
from app.processors.deduplicator import find_duplicates


def build_report() -> dict[str, Any]:
    with session_scope() as s:
        total = s.scalar(select(func.count(Exhibitor.id))) or 0
        with_website = s.scalar(
            select(func.count(Exhibitor.id)).where(Exhibitor.website_url.is_not(None))
        ) or 0
        with_email = s.scalar(
            select(func.count(Exhibitor.id)).where(Exhibitor.contact_email.is_not(None))
        ) or 0
        with_phone = s.scalar(
            select(func.count(Exhibitor.id)).where(Exhibitor.phone.is_not(None))
        ) or 0
        with_linkedin = s.scalar(
            select(func.count(Exhibitor.id)).where(Exhibitor.linkedin_url.is_not(None))
        ) or 0
        with_address = s.scalar(
            select(func.count(Exhibitor.id)).where(Exhibitor.address1.is_not(None))
        ) or 0
        needs_review = s.scalar(
            select(func.count(Exhibitor.id)).where(Exhibitor.needs_review.is_(True))
        ) or 0

        priority_counts = dict(
            s.execute(
                select(Exhibitor.priority_level, func.count(Exhibitor.id))
                .group_by(Exhibitor.priority_level)
            ).all()
        )
        country_counts = dict(
            s.execute(
                select(Exhibitor.country_iso2, func.count(Exhibitor.id))
                .group_by(Exhibitor.country_iso2)
                .order_by(func.count(Exhibitor.id).desc())
                .limit(15)
            ).all()
        )
        labels = Counter(
            l for (l,) in s.execute(select(ExhibitorClassification.label)).all()
        )

        # confidence breakdown for contact_email
        email_conf = Counter()
        for (fc,) in s.execute(select(Exhibitor.field_confidence)).all():
            email_conf[(fc or {}).get("contact_email", "none")] += 1

        # dedup
        rows = [
            {
                "id": r.id,
                "company_name": r.company_name,
                "website_url_normalized": r.website_url_normalized,
                "country_iso2": r.country_iso2,
            }
            for r in s.execute(
                select(
                    Exhibitor.id,
                    Exhibitor.company_name,
                    Exhibitor.website_url_normalized,
                    Exhibitor.country_iso2,
                )
            ).all()
        ]
        dup_clusters = find_duplicates(rows)

    def pct(n):
        return round((n / total * 100) if total else 0, 1)

    # ----- Intelligence layer metrics -----
    with session_scope() as s:
        intel_total = s.scalar(select(func.count(ExhibitorIntelligence.id))) or 0
        with_built = s.scalar(
            select(func.count(ExhibitorIntelligence.id)).where(
                ExhibitorIntelligence.built_products.is_not(None),
                func.json_array_length(ExhibitorIntelligence.built_products) > 0,
            )
        ) or 0
        with_sold = s.scalar(
            select(func.count(ExhibitorIntelligence.id)).where(
                ExhibitorIntelligence.sold_offerings.is_not(None),
                func.json_array_length(ExhibitorIntelligence.sold_offerings) > 0,
            )
        ) or 0
        with_buying = s.scalar(
            select(func.count(ExhibitorIntelligence.id)).where(
                ExhibitorIntelligence.probable_buying_needs.is_not(None),
                func.json_array_length(ExhibitorIntelligence.probable_buying_needs) > 0,
            )
        ) or 0
        with_pitch = s.scalar(
            select(func.count(ExhibitorIntelligence.id)).where(
                ExhibitorIntelligence.recommended_pitch.is_not(None),
                ExhibitorIntelligence.recommended_pitch != "",
            )
        ) or 0
        defense_priorities = dict(
            s.execute(
                select(ExhibitorIntelligence.defense_priority_level, func.count(ExhibitorIntelligence.id))
                .group_by(ExhibitorIntelligence.defense_priority_level)
            ).all()
        )
        interest_levels = dict(
            s.execute(
                select(ExhibitorIntelligence.commercial_interest_level, func.count(ExhibitorIntelligence.id))
                .group_by(ExhibitorIntelligence.commercial_interest_level)
            ).all()
        )
        extraction_methods = dict(
            s.execute(
                select(ExhibitorIntelligence.extraction_method, func.count(ExhibitorIntelligence.id))
                .group_by(ExhibitorIntelligence.extraction_method)
            ).all()
        )
        crawl_stats = s.execute(
            select(
                func.count(CrawledPage.id),
                func.sum(func.iif(CrawledPage.kind == "pdf", 1, 0)),
                func.sum(func.iif(CrawledPage.error.is_not(None), 1, 0)),
            )
        ).one()
        crawl_total, crawl_pdfs, crawl_errors = crawl_stats

        # taxonomy distribution from the intelligence layer (English labels)
        defense_label_counts: Counter[str] = Counter()
        for (cats,) in s.execute(select(ExhibitorIntelligence.defense_categories)).all():
            for label in (cats or []):
                defense_label_counts[label] += 1

        # field-level confidence distribution
        intel_confidence: Counter[str] = Counter()
        for (fc,) in s.execute(select(ExhibitorIntelligence.field_confidence)).all():
            for v in (fc or {}).values():
                intel_confidence[v] += 1

    a_plus_a = (defense_priorities.get("A+", 0) + defense_priorities.get("A", 0))

    def ipct(n):
        return round((n / intel_total * 100) if intel_total else 0, 1)

    report = {
        "total_exhibitors": total,
        "completeness_pct": {
            "website": pct(with_website),
            "contact_email": pct(with_email),
            "phone": pct(with_phone),
            "linkedin": pct(with_linkedin),
            "address": pct(with_address),
        },
        "needs_review": needs_review,
        "priority_distribution": priority_counts,
        "top_countries": country_counts,
        "taxonomy_distribution": dict(labels.most_common()),
        "email_confidence_distribution": dict(email_conf),
        "duplicate_candidates": {
            "total_clusters": len(dup_clusters),
            "by_method": Counter(c.method for c in dup_clusters),
            "examples": [
                {
                    "method": c.method,
                    "confidence": c.confidence,
                    "key": c.key,
                    "ids": c.members[:5],
                }
                for c in dup_clusters[:20]
            ],
        },
        # ----- Premium intelligence metrics -----
        "intelligence": {
            "analyzed": intel_total,
            "coverage_pct": pct(intel_total),
            "with_built_products_pct": ipct(with_built),
            "with_sold_offerings_pct": ipct(with_sold),
            "with_buying_needs_pct": ipct(with_buying),
            "with_recommended_pitch_pct": ipct(with_pitch),
            "a_or_aplus_pct": ipct(a_plus_a),
            "defense_priority_distribution": defense_priorities,
            "interest_level_distribution": interest_levels,
            "extraction_method_distribution": extraction_methods,
            "defense_taxonomy_distribution": dict(defense_label_counts.most_common()),
            "field_confidence_distribution": dict(intel_confidence),
            "crawl": {
                "pages_fetched": crawl_total or 0,
                "pdfs_fetched": crawl_pdfs or 0,
                "fetch_errors": crawl_errors or 0,
            },
        },
    }
    return report


def main() -> None:
    import json

    r = build_report()
    print(json.dumps(r, indent=2, ensure_ascii=False))
    logger.info("report total={} email%={}", r["total_exhibitors"], r["completeness_pct"]["contact_email"])


if __name__ == "__main__":  # pragma: no cover
    main()
