"""Pipeline: enrich exhibitors from their official website (homepage / contact / about)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.database import (
    Exhibitor,
    ExhibitorEnrichmentSource,
    ScrapingRun,
    session_scope,
)
from app.scrapers.website_enricher import enrich_many


def _apply_enrichment(exh: Exhibitor, result, s) -> bool:
    if result.error:
        s.add(
            ExhibitorEnrichmentSource(
                exhibitor_id=exh.id,
                source_type="website",
                source_url=result.homepage_url or exh.website_url,
                http_status=0,
                notes=result.error[:500],
            )
        )
        exh.last_enriched_at = datetime.utcnow()
        return False

    sources = exh.field_sources or {}
    confidences = exh.field_confidence or {}

    # email: only fill if better than what we already have
    if result.emails:
        from app.processors.normalizer import is_generic_email

        existing_email = exh.contact_email
        existing_conf = (exh.field_confidence or {}).get("contact_email")
        # take the result's "best" choice via confidence map
        pref_conf = result.field_confidence.get("contact_email", "low")
        chosen = None
        chosen_src = None
        for e, src in result.emails:
            chosen, chosen_src = e, src
            if is_generic_email(e):
                break
        if chosen and (
            not existing_email
            or (existing_conf in (None, "low") and pref_conf in ("medium", "high"))
        ):
            exh.contact_email = chosen
            sources["contact_email"] = chosen_src or result.homepage_url
            confidences["contact_email"] = pref_conf
            if is_generic_email(chosen):
                exh.generic_sales_email = chosen

    if result.phones and not exh.phone:
        exh.phone, src = result.phones[0]
        sources["phone"] = src
        confidences["phone"] = "low"  # regex only

    for attr in ("linkedin_url", "twitter_url", "facebook_url", "youtube_url"):
        v = getattr(result, attr)
        if v and not getattr(exh, attr):
            setattr(exh, attr, v)
            sources[attr] = result.field_sources.get(attr, result.homepage_url)
            confidences[attr] = result.field_confidence.get(attr, "medium")

    exh.field_sources = sources
    exh.field_confidence = confidences
    exh.last_enriched_at = datetime.utcnow()

    s.add(
        ExhibitorEnrichmentSource(
            exhibitor_id=exh.id,
            source_type="website",
            source_url=result.homepage_url,
            http_status=200,
            fields_extracted=[k for k in (
                "contact_email", "phone", "linkedin_url", "twitter_url",
                "facebook_url", "youtube_url", "meta_description"
            ) if (k == "meta_description" and result.meta_description) or sources.get(k)],
            notes=f"visited={len(result.visited_urls)}",
        )
    )
    if result.meta_description and not exh.short_presentation:
        exh.short_presentation = result.meta_description
    return True


async def run_enrich_websites(limit: Optional[int] = None, only_missing_email: bool = True) -> dict:
    summary = {"requested": 0, "ok": 0, "errors": 0}
    with session_scope() as s:
        q = select(Exhibitor.id, Exhibitor.website_url).where(
            Exhibitor.website_url.is_not(None)
        )
        if only_missing_email:
            q = q.where(Exhibitor.contact_email.is_(None))
        if limit:
            q = q.limit(limit)
        rows = list(s.execute(q).all())

    summary["requested"] = len(rows)
    if not rows:
        logger.info("nothing to enrich from websites")
        return summary

    pairs = [(r.id, r.website_url) for r in rows]
    enriched = await enrich_many(pairs, concurrency=5)

    with session_scope() as s:
        run = ScrapingRun(kind="website_enrich", status="running")
        s.add(run)
        s.flush()
        for eid, result in enriched.items():
            exh = s.get(Exhibitor, eid)
            if exh is None:
                continue
            ok = _apply_enrichment(exh, result, s)
            summary["ok" if ok else "errors"] += 1
        run.finished_at = datetime.utcnow()
        run.status = "ok"
        run.items_processed = summary["requested"]
        run.items_succeeded = summary["ok"]
        run.items_failed = summary["errors"]
        run.summary = summary

    logger.info("website enrichment: {}", summary)
    return summary


def main() -> None:
    asyncio.run(run_enrich_websites())


if __name__ == "__main__":  # pragma: no cover
    main()
