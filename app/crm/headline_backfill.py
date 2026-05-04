"""Backfill the ``ExhibitorIntelligence.headline`` field for exhibitors
that don't have one yet.

Most existing rows were filled by the full deep-crawler pipeline. The
GICAT-only imports (and a handful of legacy rows) bypass that and end
up with no headline. This module just fetches each candidate's homepage
once and runs the existing ``extract_headline_from_text`` parser on it
— no full crawl, no LLM, ~5-10s per company.

A clean ``<meta name="description">`` / ``<meta property="og:description">``
/ ``<title>`` is preferred ; otherwise we fall back to the first prose
paragraph that ``app.crm.headline`` can identify.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger
from selectolax.parser import HTMLParser
from sqlalchemy import select

from app.crm.headline import (
    _clean as _hd_clean,
    _looks_usable as _hd_looks_usable,
    extract_headline_from_text,
)
from app.database import (
    Exhibitor, ExhibitorIntelligence, SessionLocal,
)
from app.scrapers.http_client import HttpClient


@dataclass
class HeadlineBackfillResult:
    scanned: int = 0
    homepages_fetched: int = 0
    headlines_filled: int = 0
    intelligence_rows_created: int = 0
    errors: int = 0

    def to_dict(self) -> dict:
        return self.__dict__


# Common meta-tag selectors, in priority order. The first one that
# yields a usable string wins.
_META_SELECTORS: list[tuple[str, str]] = [
    ('meta[property="og:description"]', "content"),
    ('meta[name="description"]', "content"),
    ('meta[property="twitter:description"]', "content"),
    ('meta[name="twitter:description"]', "content"),
    ('meta[property="og:title"]', "content"),
    ("title", None),  # innerText
]


def _normalise_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    return u


# Patterns that indicate the page returned a cookie banner / JS-required
# placeholder / e-commerce error instead of real corporate content.
# Matched case-insensitively as substrings.
_BAD_HEADLINE_FRAGMENTS = (
    "cookies are disabled", "cookies are required", "enable cookies",
    "accept all cookies", "we use cookies", "this site uses cookies",
    "please enable javascript", "javascript is disabled",
    "the store will not work correctly",
    "loading…", "loading...", "please wait",
    "404 not found", "page not found", "access denied",
    "checking your browser", "verify you are human",
    "just a moment", "cloudflare",
    "forbidden", "service unavailable",
    "your browser is not supported",
)


def _is_bad_headline(s: str) -> bool:
    low = s.lower()
    return any(frag in low for frag in _BAD_HEADLINE_FRAGMENTS)


def _meta_headline(html: str) -> Optional[str]:
    """Extract the headline from the page's meta tags (og / description /
    title). Returns the first usable hit or None.
    """
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return None
    for selector, attr in _META_SELECTORS:
        el = tree.css_first(selector)
        if el is None:
            continue
        if attr:
            value = el.attributes.get(attr) or ""
        else:
            value = el.text() or ""
        cleaned = _hd_clean(value)
        if not cleaned:
            continue
        if _is_bad_headline(cleaned):
            continue
        if not _hd_looks_usable(cleaned):
            continue
        return cleaned[:300]
    return None


def _visible_text(html: str) -> str:
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return ""
    for sel in ("script", "style", "noscript"):
        for n in tree.css(sel):
            n.decompose()
    return re.sub(r"\s+", " ", tree.text(separator=" ") or "").strip()


async def _fetch_one(
    client: HttpClient, exh_id: int, name: str, website: str,
) -> Optional[str]:
    url = _normalise_url(website)
    if not url:
        return None
    try:
        r = await client.request("GET", url)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"GET {url} failed: {e!r}")
        return None
    if r.status_code >= 400 or not r.text:
        return None
    html = r.text
    # Meta tags first (cleanest, intentional source).
    headline = _meta_headline(html)
    if headline:
        return headline
    # Fallback : run the existing prose-extractor on the visible body.
    text = _visible_text(html)[:8000]
    if text:
        h = extract_headline_from_text(text)
        if h:
            return h
    return None


async def _backfill_async(
    limit: Optional[int] = None,
    progress_cb=None,
) -> HeadlineBackfillResult:
    res = HeadlineBackfillResult()
    s = SessionLocal()
    try:
        # Need exhibitors with a website AND missing headline (or no
        # intelligence row at all).
        rows = s.execute(
            select(
                Exhibitor.id, Exhibitor.company_name,
                Exhibitor.website_url,
                ExhibitorIntelligence.headline,
            )
            .outerjoin(
                ExhibitorIntelligence,
                ExhibitorIntelligence.exhibitor_id == Exhibitor.id,
            )
            .where(
                Exhibitor.website_url.is_not(None),
                (
                    ExhibitorIntelligence.headline.is_(None)
                    | (ExhibitorIntelligence.headline == "")
                ),
            )
        ).all()
    finally:
        s.close()
    if limit:
        rows = rows[:limit]
    total = len(rows)

    headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
    }
    async with HttpClient(
        extra_headers=headers, per_host_delay=0.05, concurrency=24,
        timeout=10, max_retries=1,
    ) as c:
        async def _one(eid: int, name: str, website: str) -> None:
            res.scanned += 1
            try:
                h = await _fetch_one(c, eid, name, website)
            except Exception as e:  # noqa: BLE001
                res.errors += 1
                logger.debug(f"backfill {name} failed: {e!r}")
                return
            if not h:
                return
            res.homepages_fetched += 1
            # Persist
            s2 = SessionLocal()
            try:
                intel = s2.scalar(
                    select(ExhibitorIntelligence).where(
                        ExhibitorIntelligence.exhibitor_id == eid,
                    )
                )
                if intel is None:
                    intel = ExhibitorIntelligence(exhibitor_id=eid)
                    s2.add(intel)
                    res.intelligence_rows_created += 1
                if not (intel.headline or "").strip():
                    intel.headline = h
                    res.headlines_filled += 1
                s2.commit()
            except Exception as e:  # noqa: BLE001
                res.errors += 1
                logger.debug(f"persist {name} failed: {e!r}")
            finally:
                s2.close()
            if progress_cb:
                progress_cb(
                    res.scanned, total, res.headlines_filled,
                )

        batch = 64
        for i in range(0, len(rows), batch):
            await asyncio.gather(*[
                _one(int(r[0]), r[1], r[2]) for r in rows[i: i + batch]
            ])
    return res


def backfill_headlines(
    limit: Optional[int] = None, progress_cb=None,
) -> HeadlineBackfillResult:
    """Sync entrypoint."""
    return asyncio.run(_backfill_async(limit=limit, progress_cb=progress_cb))
