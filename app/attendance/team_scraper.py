"""Scrape exhibitor websites for `/team` `/about` `/direction` pages and
extract executive contacts.

For each ``Exhibitor`` with a website URL that has no person-signal yet,
we probe a few well-known leadership-page paths, run spaCy NER on the
visible text, and create ``AttendanceSignal`` entries for every PERSON
that has a role keyword nearby.

Stays within the existing legal posture (corporate sites are public),
no LinkedIn login, no paywall bypass.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlsplit

from loguru import logger
from selectolax.parser import HTMLParser
from sqlalchemy import select

from app.attendance.ner_extract import extract_persons_with_context
from app.attendance.seed import RawSignal, upsert_signal
from app.database import AttendanceSignal, Exhibitor, SessionLocal
from app.scrapers.http_client import HttpClient


# Common URL paths where companies expose their leadership.
_TEAM_PATHS: list[str] = [
    "/team",
    "/about",
    "/about-us",
    "/leadership",
    "/management",
    "/executives",
    "/equipe",
    "/notre-equipe",
    "/direction",
    "/management-team",
    "/qui-sommes-nous",
    "/our-team",
    "/people",
    "/governance",
    "/comite-de-direction",
]


@dataclass
class TeamScrapeResult:
    exhibitors_scanned: int = 0
    exhibitors_with_team_page: int = 0
    persons_created: int = 0
    persons_skipped: int = 0
    errors: int = 0

    def to_dict(self) -> dict:
        return self.__dict__


def _visible_text(html: str) -> str:
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return ""
    for sel in ("script", "style", "noscript"):
        for n in tree.css(sel):
            n.decompose()
    return re.sub(r"\s+", " ", tree.text(separator=" ") or "").strip()


async def _fetch_team_text(
    client: HttpClient, website: str,
) -> tuple[Optional[str], Optional[str]]:
    """Try the common team paths in order. Return ``(text, url)`` of the
    first page that yielded ≥ 500 chars of visible text, otherwise
    ``(None, None)``.
    """
    if not website or not website.startswith(("http://", "https://")):
        return None, None
    base = website.rstrip("/")
    for path in _TEAM_PATHS:
        url = base + path
        try:
            r = await client.request("GET", url)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"GET {url} failed: {e!r}")
            continue
        if r.status_code >= 400:
            continue
        ct = (r.headers.get("content-type") or "").lower()
        if "html" not in ct:
            continue
        text = _visible_text(r.text or "")
        if len(text) >= 500:
            return text, str(r.url)
    return None, None


async def _scrape_one(
    client: HttpClient, exh_id: int, name: str, website: str,
    country: Optional[str], country_iso2: Optional[str],
) -> tuple[int, int]:
    """Fetch + parse one exhibitor's leadership page. Returns
    ``(persons_created, persons_skipped)``.
    """
    text, url = await _fetch_team_text(client, website)
    if not text:
        return 0, 0
    persons = extract_persons_with_context(text, max_persons=15)
    created = 0
    skipped = 0
    for p in persons:
        # We require BOTH role and company-context to keep the noise
        # low (NER on team pages is otherwise very permissive).
        if not p["role"]:
            skipped += 1
            continue
        try:
            raw = RawSignal(
                edition_year=2026,
                entity_type="person",
                source_url=url,
                person_name=p["name"],
                person_role=p["role"],
                company_name=name,
                country=country,
                country_iso2=country_iso2,
                source_platform="corporate_site",
                source_title=f"{name} — leadership page",
                source_snippet=(
                    f"{p['name']}, {p['role']} — extracted from "
                    f"{name}'s {url} (NER)"
                ),
                search_query_used="team_scraper",
                signal_type="company_announcement",
                signal_text=f"{p['name']}, {p['role']} at {name}",
                is_company_post=True,
                notes=(
                    f"Extracted from corporate leadership page {url} "
                    f"for exhibitor #{exh_id} via NER."
                ),
            )
            _, is_new = upsert_signal(raw)
            if is_new:
                created += 1
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001
            skipped += 1
            logger.debug(f"upsert failed for {p['name']}: {e!r}")
    return created, skipped


async def _scrape_async(
    limit: Optional[int] = None,
    only_unfilled: bool = True,
    progress_cb=None,
) -> TeamScrapeResult:
    """Iterate exhibitors with a website, fetch their team pages, extract
    persons. ``only_unfilled`` skips exhibitors that already have ≥ 2
    person-signals attached (to avoid wasting requests).
    """
    res = TeamScrapeResult()
    s = SessionLocal()
    try:
        rows = s.execute(
            select(
                Exhibitor.id, Exhibitor.company_name,
                Exhibitor.website_url, Exhibitor.country_name,
                Exhibitor.country_iso2,
            ).where(Exhibitor.website_url.is_not(None))
        ).all()
        # Build a per-exhibitor count of existing person signals
        person_counts: dict[str, int] = {}
        for cn, ct in s.execute(
            select(
                AttendanceSignal.canonical_company_name,
                AttendanceSignal.entity_type,
            )
        ).all():
            if ct != "person" or not cn:
                continue
            person_counts[cn] = person_counts.get(cn, 0) + 1
    finally:
        s.close()

    # Filter to exhibitors with few/no existing person signals.
    if only_unfilled:
        from app.attendance.dedup import canonical_company_name
        rows = [
            r for r in rows
            if person_counts.get(canonical_company_name(r[1]) or "", 0) < 2
        ]
    if limit:
        rows = rows[:limit]
    total = len(rows)

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "*/*;q=0.8",
    }
    async with HttpClient(
        extra_headers=headers, per_host_delay=0.05, concurrency=16,
        timeout=10, max_retries=1,
    ) as c:
        async def _one(eid: int, name: str, website: str,
                       country: Optional[str],
                       iso2: Optional[str]) -> None:
            res.exhibitors_scanned += 1
            try:
                created, skipped = await _scrape_one(
                    c, eid, name, website, country, iso2,
                )
                if created > 0:
                    res.exhibitors_with_team_page += 1
                res.persons_created += created
                res.persons_skipped += skipped
            except Exception as e:  # noqa: BLE001
                res.errors += 1
                logger.debug(f"scrape {name} failed: {e!r}")
            if progress_cb:
                progress_cb(
                    res.exhibitors_scanned, total, res.persons_created,
                )

        batch = 32
        for i in range(0, len(rows), batch):
            await asyncio.gather(*[
                _one(int(r[0]), r[1], r[2], r[3], r[4])
                for r in rows[i: i + batch]
            ])

    return res


def scrape_team_pages(
    limit: Optional[int] = None,
    only_unfilled: bool = True,
    progress_cb=None,
) -> TeamScrapeResult:
    """Sync entrypoint."""
    return asyncio.run(
        _scrape_async(
            limit=limit, only_unfilled=only_unfilled,
            progress_cb=progress_cb,
        )
    )
