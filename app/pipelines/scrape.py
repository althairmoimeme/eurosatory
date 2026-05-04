"""Pipeline: fetch the Eurosatory catalog and upsert exhibitors into the DB."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.database import (
    Category,
    Country,
    Exhibitor,
    ExhibitorCategory,
    ExhibitorEnrichmentSource,
    ScrapingRun,
    init_db,
    session_scope,
)
from app.processors.normalizer import normalize_finderr_search_row
from app.scrapers.eurosatory_scraper import (
    fetch_all_exhibitors,
    fetch_categories,
    fetch_countries,
)


def _upsert_country(s, row: dict) -> None:
    code = (row.get("CodeISO2") or "").strip().upper()[:2]
    if not code:
        return
    obj = s.scalar(select(Country).where(Country.code_iso2 == code))
    if obj is None:
        obj = Country(code_iso2=code)
        s.add(obj)
    obj.finderr_id = row.get("CountryID")
    obj.label_en = (row.get("LabelEN") or "").strip() or None
    obj.label_fr = (row.get("LabelFR") or "").strip() or None


def _upsert_category(s, row: dict) -> None:
    fid = row.get("CategoryID")
    if not fid:
        return
    obj = s.scalar(select(Category).where(Category.finderr_id == fid))
    if obj is None:
        obj = Category(finderr_id=fid)
        s.add(obj)
    obj.code = row.get("Code")
    obj.category_type = row.get("CategoryType")
    obj.label_en = row.get("LabelEN")
    obj.label_fr = row.get("LabelFR")
    obj.parent_finderr_id = row.get("ParentCategoryID")
    obj.tags = row.get("Tags")


def _upsert_exhibitor(s, search_row: dict, run_id: int) -> Optional[Exhibitor]:
    payload = normalize_finderr_search_row(search_row)
    guid = payload.get("finderr_guid")
    if not guid:
        return None
    obj = s.scalar(select(Exhibitor).where(Exhibitor.finderr_guid == guid))
    if obj is None:
        obj = Exhibitor(finderr_guid=guid)
        s.add(obj)
    for k, v in payload.items():
        if k.startswith("_"):
            continue
        if hasattr(obj, k):
            setattr(obj, k, v)
    obj.last_scraped_at = datetime.utcnow()

    # resolve country_name from countries table
    if obj.country_iso2:
        c = s.scalar(select(Country).where(Country.code_iso2 == obj.country_iso2))
        if c and not obj.country_name:
            obj.country_name = c.label_en or c.label_fr

    # categories link
    s.query(ExhibitorCategory).filter_by(exhibitor_id=obj.id).delete() if obj.id else None
    cat_ids = search_row.get("Categories") or []
    if cat_ids:
        s.flush()  # ensure obj.id
        for cid in cat_ids:
            cat = s.scalar(select(Category).where(Category.finderr_id == cid))
            link = ExhibitorCategory(
                exhibitor_id=obj.id,
                category_finderr_id=cid,
                label_en=cat.label_en if cat else None,
                label_fr=cat.label_fr if cat else None,
            )
            s.add(link)

    s.add(
        ExhibitorEnrichmentSource(
            exhibitor_id=obj.id,
            source_type="finderr_search",
            source_url=(
                "https://eurosatory.finderr.cloud/api/catalog/search_exhibitors"
            ),
            http_status=200,
            fields_extracted=list(payload.keys()),
            notes=f"run={run_id}",
        )
    )
    return obj


async def run_scrape() -> dict:
    init_db()
    summary = {"countries": 0, "categories": 0, "exhibitors": 0}
    countries = await fetch_countries()
    categories = await fetch_categories()
    bulk = await fetch_all_exhibitors()
    rows = bulk.get("ListDetailsExhibitors") or []
    featured_guids = {x["Exhi_Guid"] for x in (bulk.get("ListFeaturedExhibitors") or []) if x.get("Exhi_Guid")}

    with session_scope() as s:
        run = ScrapingRun(kind="eurosatory_search", status="running")
        s.add(run)
        s.flush()

        for c in countries:
            _upsert_country(s, c)
        summary["countries"] = len(countries)

        for c in categories:
            _upsert_category(s, c)
        summary["categories"] = len(categories)
        s.flush()

        for row in rows:
            if row.get("Exhi_Guid") in featured_guids:
                row["IsFeatured"] = True
            obj = _upsert_exhibitor(s, row, run.id)
            if obj is not None:
                summary["exhibitors"] += 1

        run.finished_at = datetime.utcnow()
        run.status = "ok"
        run.items_processed = len(rows)
        run.items_succeeded = summary["exhibitors"]
        run.summary = summary

    logger.info("scrape pipeline complete: {}", summary)
    return summary


def main() -> None:  # convenience entry point
    asyncio.run(run_scrape())


if __name__ == "__main__":  # pragma: no cover
    main()
