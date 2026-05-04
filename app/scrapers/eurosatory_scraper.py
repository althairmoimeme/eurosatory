"""Eurosatory 2026 scraper — Finderr Cloud public catalog API.

Discovery notes
---------------
The Eurosatory catalog page (https://www.eurosatory.com/en/catalogue/) embeds
an iframe pointing to https://eurosatory.finderr.cloud/standalone/catalog/company-list,
which is an Angular SPA backed by the Finderr Cloud REST API. The relevant
endpoints found in the public JS bundle are:

* POST {api}/catalog/search_exhibitors
* GET  {api}/v3/catalog/get_exhibitor/{guid}/{lang}
* GET  {api}/catalog/get-all-categories?CurrentLanguage={lang}
* GET  {api}/catalog/get-all-countries?CurrentLanguage={lang}

Authentication uses an X-API-KEY header, but the value is the public
"deployedApiKey" embedded in the front-end bundle and required by every visitor
loading the catalog. We are therefore using publicly accessible data only.

We respect rate limits (per-host delay + concurrency cap) and identify ourselves
with a transparent User-Agent including a contact email.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from loguru import logger

from app.config import RAW_DIR, settings
from app.scrapers.http_client import HttpClient


SEARCH_PATH = "catalog/search_exhibitors"
DETAIL_PATH = "v3/catalog/get_exhibitor/{guid}/{lang}"
CATEGORIES_PATH = "catalog/get-all-categories"
COUNTRIES_PATH = "catalog/get-all-countries"


def _client() -> HttpClient:
    return HttpClient(
        base_url=settings.finderr_base_url,
        extra_headers={
            "X-API-KEY": settings.finderr_api_key,
            "Origin": "https://eurosatory.finderr.cloud",
            "Referer": "https://eurosatory.finderr.cloud/standalone/catalog/company-list",
            "Content-Type": "application/json",
        },
    )


def _save_raw(name: str, payload: Any) -> Path:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = RAW_DIR / f"{ts}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return path


async def fetch_categories() -> list[dict]:
    async with _client() as c:
        r = await c.get(f"{CATEGORIES_PATH}?CurrentLanguage={settings.finderr_language}")
        r.raise_for_status()
        data = r.json()
        _save_raw("categories", data)
        logger.info("Fetched {} categories", len(data))
        return data


async def fetch_countries() -> list[dict]:
    async with _client() as c:
        r = await c.get(f"{COUNTRIES_PATH}?CurrentLanguage={settings.finderr_language}")
        r.raise_for_status()
        data = r.json()
        _save_raw("countries", data)
        logger.info("Fetched {} countries", len(data))
        return data


async def fetch_all_exhibitors(page_size: int = 5000) -> dict:
    """Fetch the catalog. The API supports a single bulk call with a large page size.

    Returns the raw response: {NbExhibitors, ListDetailsExhibitors, ListFeaturedExhibitors, ...}
    """
    body = {
        "GetAll": True,
        "CurrentLanguage": settings.finderr_language,
        "NbElementsPerPage": page_size,
        "PageIndex": 0,
    }
    async with _client() as c:
        r = await c.post(SEARCH_PATH, json=body)
        r.raise_for_status()
        data = r.json()
        path = _save_raw("search_exhibitors", data)
        logger.info(
            "Fetched {} exhibitors (raw saved to {})",
            data.get("NbExhibitors"),
            path,
        )
        return data


async def fetch_exhibitor_detail(client: HttpClient, guid: str) -> Optional[dict]:
    path = DETAIL_PATH.format(guid=guid, lang=settings.finderr_language)
    try:
        r = await client.get(path)
        if r.status_code == 200:
            return r.json()
        logger.warning("detail {} -> HTTP {}", guid, r.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("detail {} failed: {}", guid, e)
    return None


async def fetch_exhibitor_details(
    guids: Iterable[str], concurrency: Optional[int] = None
) -> dict[str, dict]:
    """Fetch detail payload for each GUID concurrently."""
    sem = asyncio.Semaphore(concurrency or settings.scrape_concurrency)
    results: dict[str, dict] = {}

    async with _client() as c:

        async def _one(g: str) -> None:
            async with sem:
                d = await fetch_exhibitor_detail(c, g)
                if d:
                    results[g] = d

        await asyncio.gather(*(_one(g) for g in guids))

    logger.info("Fetched details for {} / {} exhibitors", len(results), len(list(guids)))
    return results
