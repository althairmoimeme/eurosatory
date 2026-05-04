"""Find each exhibitor's LinkedIn company URL by scraping their website
homepage footer.

Defense industry sites almost universally link to their LinkedIn page
from the homepage footer (or an Apple-style social-icons row in the
header). We fetch the homepage, look for any ``<a href>`` whose URL
matches ``linkedin.com/company/<slug>``, and save it back to
``Exhibitor.linkedin_url``.

Stays within the existing legal posture (corporate sites are public),
no LinkedIn login required — we just extract the public link they
themselves placed on their homepage.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from loguru import logger
from selectolax.parser import HTMLParser
from sqlalchemy import select

from app.database import Exhibitor, SessionLocal
from app.scrapers.http_client import HttpClient


_LINKEDIN_RX = re.compile(
    r"https?://(?:[a-z]{2,4}\.)?linkedin\.com/(?:company|school|in)/[^\"'\s>]+",
    re.IGNORECASE,
)


def _find_linkedin_url(html: str) -> Optional[str]:
    """Return the most likely LinkedIn COMPANY URL from the page HTML."""
    if not html:
        return None
    candidates: list[str] = []
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        # Fall back to regex on raw HTML if parsing fails.
        m = _LINKEDIN_RX.search(html)
        return m.group(0) if m else None
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if "linkedin.com/" in href.lower():
            candidates.append(href)
    if not candidates:
        m = _LINKEDIN_RX.search(html)
        return m.group(0) if m else None
    # Prefer /company/ links over /in/ (personal profile) and /school/.
    for c in candidates:
        if "/company/" in c.lower():
            return c.split("?")[0].rstrip(")/")
    return candidates[0].split("?")[0].rstrip(")/")


async def _scrape_one(
    client: HttpClient, exh_id: int, website: str,
) -> Optional[str]:
    if not website or not website.startswith(("http://", "https://")):
        return None
    try:
        r = await client.request("GET", website)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"GET {website} failed: {e!r}")
        return None
    if r.status_code >= 400 or not r.text:
        return None
    return _find_linkedin_url(r.text)


async def _scrape_async(
    limit: Optional[int] = None,
    progress_cb=None,
) -> dict:
    s = SessionLocal()
    try:
        rows = s.execute(
            select(
                Exhibitor.id, Exhibitor.company_name, Exhibitor.website_url,
            ).where(
                Exhibitor.website_url.is_not(None),
                Exhibitor.linkedin_url.is_(None),
            )
        ).all()
    finally:
        s.close()
    if limit:
        rows = rows[:limit]
    total = len(rows)
    scanned = 0
    found = 0
    errors = 0

    async with HttpClient(
        per_host_delay=0.05, concurrency=24, timeout=8, max_retries=1,
    ) as c:
        async def _one(eid: int, name: str, website: str) -> None:
            nonlocal scanned, found, errors
            scanned += 1
            try:
                url = await _scrape_one(c, eid, website)
            except Exception as e:  # noqa: BLE001
                errors += 1
                logger.debug(f"scrape {name} failed: {e!r}")
                if progress_cb:
                    progress_cb(scanned, total, found)
                return
            if url:
                # Persist immediately
                s2 = SessionLocal()
                try:
                    ex = s2.get(Exhibitor, eid)
                    if ex and not ex.linkedin_url:
                        ex.linkedin_url = url
                        s2.commit()
                        found += 1
                finally:
                    s2.close()
            if progress_cb:
                progress_cb(scanned, total, found)

        batch = 64
        for i in range(0, len(rows), batch):
            await asyncio.gather(*[
                _one(int(r[0]), r[1], r[2]) for r in rows[i: i + batch]
            ])

    return {
        "scanned": scanned,
        "linkedin_found": found,
        "errors": errors,
        "total_candidates": total,
    }


def scrape_linkedin_footers(
    limit: Optional[int] = None, progress_cb=None,
) -> dict:
    """Sync entrypoint."""
    return asyncio.run(
        _scrape_async(limit=limit, progress_cb=progress_cb)
    )
