"""Pipeline: enrich each exhibitor with the Finderr v3 detail endpoint.

The detail endpoint provides addresses, contacts, social networks and the
``Presentation`` field that's missing from the bulk search response.  We hit
it with the same X-API-KEY and our throttled HTTP client.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.database import (
    Exhibitor,
    ExhibitorCategory,
    ExhibitorContact,
    ExhibitorEnrichmentSource,
    ScrapingRun,
    session_scope,
)
from app.processors.normalizer import (
    clean_str,
    is_generic_email,
    merge_finderr_detail,
    normalize_email,
    normalize_phone,
)
from app.scrapers.eurosatory_scraper import fetch_exhibitor_details


async def _gather(guids: list[str]) -> dict[str, dict]:
    return await fetch_exhibitor_details(guids)


def _apply_detail(s, exh: Exhibitor, detail: dict) -> None:
    skeleton = {col.name: getattr(exh, col.name) for col in exh.__table__.columns}
    merged = merge_finderr_detail(skeleton, detail)
    for k, v in merged.items():
        if k.startswith("_") or k == "id":
            continue
        if hasattr(exh, k):
            current = getattr(exh, k)
            if current in (None, "", [], {}) and v not in (None, "", [], {}):
                setattr(exh, k, v)

    # contacts
    s.query(ExhibitorContact).filter_by(exhibitor_id=exh.id).delete()
    for c in merged.get("_contacts") or []:
        email = normalize_email(c.get("Email"))
        phone = normalize_phone(c.get("Phone"))
        full_name = (c.get("FullName") or "").strip() or None
        if not (email or phone or full_name):
            continue
        s.add(
            ExhibitorContact(
                exhibitor_id=exh.id,
                full_name=full_name,
                function=clean_str(c.get("Function"))
                if (c.get("Function") or "") != "NOT AVAILABLE LANGUAGE"
                else None,
                email=email,
                phone=phone,
                linkedin=clean_str(c.get("LinkedIn")),
                is_generic=is_generic_email(email) if email else False,
                confidence="high",  # comes straight from the official catalog
                source_url="https://eurosatory.finderr.cloud/api/v3/catalog/get_exhibitor",
            )
        )

    # update category labels with detail's translation if available
    label_pairs = merged.get("_categories_labels") or []
    if label_pairs:
        for fid, lbl in label_pairs:
            if not lbl or lbl == "NOT AVAILABLE LANGUAGE":
                continue
            link = s.scalar(
                select(ExhibitorCategory).where(
                    ExhibitorCategory.exhibitor_id == exh.id,
                    ExhibitorCategory.category_finderr_id == fid,
                )
            )
            if link:
                if settings.finderr_language == "en":
                    link.label_en = lbl
                else:
                    link.label_fr = lbl

    exh.last_enriched_at = datetime.utcnow()
    sources = exh.field_sources or {}
    confidences = exh.field_confidence or {}
    detail_url = (
        f"https://eurosatory.finderr.cloud/api/v3/catalog/get_exhibitor/{exh.finderr_guid}/{settings.finderr_language}"
    )
    for field in (
        "address1", "address2", "city", "zip_code", "country_name", "phone",
        "contact_email", "linkedin_url", "twitter_url", "facebook_url", "presentation",
    ):
        if getattr(exh, field):
            sources.setdefault(field, detail_url)
            confidences.setdefault(field, "high")
    exh.field_sources = sources
    exh.field_confidence = confidences

    s.add(
        ExhibitorEnrichmentSource(
            exhibitor_id=exh.id,
            source_type="finderr_detail",
            source_url=detail_url,
            http_status=200,
            fields_extracted=[
                f for f in
                ("address1", "city", "zip_code", "country_name", "phone",
                 "contact_email", "linkedin_url", "presentation")
                if getattr(exh, f)
            ],
        )
    )


async def run_enrich_finderr(limit: Optional[int] = None) -> dict:
    summary = {"requested": 0, "enriched": 0}
    with session_scope() as s:
        q = select(Exhibitor.id, Exhibitor.finderr_guid).where(
            Exhibitor.last_enriched_at.is_(None)
        )
        if limit:
            q = q.limit(limit)
        rows = list(s.execute(q).all())

    summary["requested"] = len(rows)
    if not rows:
        logger.info("nothing to enrich")
        return summary

    guids = [r.finderr_guid for r in rows]
    details = await _gather(guids)

    with session_scope() as s:
        run = ScrapingRun(kind="eurosatory_details", status="running")
        s.add(run)
        s.flush()
        for r in rows:
            d = details.get(r.finderr_guid)
            if not d:
                continue
            exh = s.get(Exhibitor, r.id)
            if exh is None:
                continue
            _apply_detail(s, exh, d)
            summary["enriched"] += 1
        run.finished_at = datetime.utcnow()
        run.status = "ok"
        run.items_processed = summary["requested"]
        run.items_succeeded = summary["enriched"]
        run.summary = summary

    logger.info("finderr-detail enrichment: {}", summary)
    return summary


def main() -> None:
    asyncio.run(run_enrich_finderr())


if __name__ == "__main__":  # pragma: no cover
    main()
