"""Automated collection of ``AttendanceSignal`` rows by scanning the
official websites of catalog exhibitors.

Strategy
--------
For each exhibitor with a ``website_url`` :

1. Fetch the homepage + a few well-known sub-paths (``/news``, ``/press``,
   ``/about``, ``/events``, ``/eurosatory``) over HTTP/2 with retries.
2. Detect a mention of ``Eurosatory <year>`` (case-insensitive) in the
   visible text.  Emit a ``RawSignal`` with ``signal_type =
   company_announcement`` and ``source_platform = corporate_site``.
3. ``upsert_signal`` deduplicates / scores / persists.

This keeps us within the existing legal posture (the same official
websites are already crawled by the main exhibitor pipeline) — we just
extract a new dimension of evidence (presence at the show) from the
already-public material.

We DO NOT scrape paywalled / authenticated sources (LinkedIn, X) here —
the operator still uses the documented Google queries for that and
imports the resulting URLs via CSV.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

from loguru import logger
from selectolax.parser import HTMLParser

from app.attendance.seed import RawSignal, upsert_signal
from app.database import Exhibitor, SessionLocal
from app.scrapers.http_client import HttpClient
from sqlalchemy import select


# Sub-paths we try in addition to the homepage. Most corporate sites surface
# event news on /news, /press, /events ; /eurosatory is sometimes a dedicated
# landing for the show.
_PROBE_PATHS: list[str] = [
    "",  # homepage
    "/news",
    "/press",
    "/news-events",
    "/events",
    "/eurosatory",
    "/eurosatory-2026",
]


# Regex to find "Eurosatory <year>" inside text (allow flexible whitespace
# and mixed-case).  We capture roughly 200 chars around the match for the
# snippet so the operator has context.
_EUROSATORY_RX = re.compile(
    r"(?i)\beurosatory[\s\-]*?(?P<year>20\d{2})\b"
)


@dataclass
class _PageHit:
    url: str
    title: Optional[str]
    snippet: str
    year: int


def _visible_text(html: str) -> str:
    """Extract a single text blob from HTML (strips script/style + collapses
    whitespace).  Pure stdlib + selectolax — fast on large pages.
    """
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return ""
    for sel in ("script", "style", "noscript"):
        for n in tree.css(sel):
            n.decompose()
    txt = tree.text(separator=" ")
    return re.sub(r"\s+", " ", txt or "").strip()


def _page_title(html: str) -> Optional[str]:
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return None
    t = tree.css_first("title")
    if t and t.text():
        return t.text(strip=True)[:300]
    og = tree.css_first('meta[property="og:title"]')
    if og:
        v = og.attributes.get("content")
        if v:
            return v.strip()[:300]
    return None


def _scan_text_for_year(text: str, target_year: int) -> Optional[_PageHit]:
    """If ``text`` mentions Eurosatory <target_year> (or any year), return a
    snippet around the FIRST match — preferring an exact target_year match.
    """
    if not text:
        return None
    matches = list(_EUROSATORY_RX.finditer(text))
    if not matches:
        return None
    # Prefer the target_year match
    target = next(
        (m for m in matches if int(m.group("year")) == target_year),
        None,
    )
    chosen = target or matches[0]
    start = max(0, chosen.start() - 120)
    end = min(len(text), chosen.end() + 120)
    snippet = text[start:end].strip()
    return _PageHit(
        url="",  # filled by caller
        title=None,  # filled by caller
        snippet=snippet,
        year=int(chosen.group("year")),
    )


async def _scan_one_exhibitor(
    client: HttpClient,
    exhibitor_id: int,
    name: str,
    country: Optional[str],
    website: str,
    target_year: int,
    matches_per_company_cap: int = 2,
    dead_hosts: Optional[set[str]] = None,
) -> list[RawSignal]:
    """Fetch the homepage + probe paths for one exhibitor and return the
    ``RawSignal`` rows derived from "Eurosatory <year>" mentions.

    If ``dead_hosts`` is provided and the homepage fails (DNS / unreachable),
    the host is added to the set and the probe paths are skipped — saves
    a lot of time during a full sweep over thousands of exhibitors.
    """
    from urllib.parse import urlsplit
    found: list[RawSignal] = []
    seen_urls: set[str] = set()
    homepage_failed = False
    for idx, path in enumerate(_PROBE_PATHS):
        if len(found) >= matches_per_company_cap:
            break
        url = urljoin(website, path) if path else website
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # If the homepage itself failed (DNS / connect error), don't bother
        # with sub-paths — they're on the same host.
        if homepage_failed and idx > 0:
            break
        try:
            r = await client.request("GET", url)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"GET {url} failed: {e!r}")
            if idx == 0:
                homepage_failed = True
                if dead_hosts is not None:
                    dead_hosts.add(urlsplit(url).netloc)
            continue
        if r.status_code >= 400:
            if idx == 0:
                homepage_failed = True
            continue
        ct = (r.headers.get("content-type") or "").lower()
        if "html" not in ct:
            continue
        html = r.text or ""
        if not html:
            continue
        text = _visible_text(html)
        hit = _scan_text_for_year(text, target_year)
        if hit is None:
            continue
        title = _page_title(html)
        sig = RawSignal(
            edition_year=hit.year,
            entity_type="company",
            source_url=str(r.url),
            company_name=name,
            country=country,
            source_platform="corporate_site",
            source_title=title,
            source_snippet=hit.snippet,
            search_query_used="auto_collect:corporate_site",
            signal_type="company_announcement",
            signal_text=hit.snippet,
            is_company_post=True,
            notes=f"Auto-collected from {url} for exhibitor #{exhibitor_id}",
        )
        found.append(sig)
    return found


async def _collect_async(
    target_year: int,
    limit: Optional[int] = None,
    only_with_website: bool = True,
    progress_cb=None,
) -> dict:
    """Iterate exhibitors and emit signals.  Reports counts."""
    s = SessionLocal()
    try:
        q = select(Exhibitor)
        if only_with_website:
            q = q.where(Exhibitor.website_url.is_not(None))
        rows = list(s.execute(q).scalars())
    finally:
        s.close()
    if limit:
        rows = rows[:limit]

    total = len(rows)
    scanned = 0
    hits = 0
    new = 0
    updated = 0
    errors = 0

    # Aggressive parallelism + short timeout + NO retries — most exhibitor
    # sites respond in <2s; if not, skip. We're not crawling deep, and a
    # dead host shouldn't burn 30s of retries.
    async with HttpClient(
        per_host_delay=0.05, concurrency=24, timeout=8, max_retries=1,
    ) as c:
        # Cache: first failed homepage of a host marks the host dead, all
        # subsequent paths on the same host are skipped immediately.
        dead_hosts: set[str] = set()

        async def _one(exh: Exhibitor) -> None:
            nonlocal scanned, hits, new, updated, errors
            scanned += 1
            site = (exh.website_url or "").strip()
            if not site or not site.startswith(("http://", "https://")):
                return
            from urllib.parse import urlsplit
            host = urlsplit(site).netloc
            if host in dead_hosts:
                return
            try:
                signals = await _scan_one_exhibitor(
                    c, exh.id, exh.company_name,
                    exh.country_name, site,
                    target_year=target_year,
                    dead_hosts=dead_hosts,
                )
            except Exception as e:  # noqa: BLE001
                errors += 1
                logger.debug(f"scan failed for {exh.company_name!r}: {e!r}")
                return
            for sig in signals:
                hits += 1
                try:
                    _, is_new = upsert_signal(sig)
                    if is_new:
                        new += 1
                    else:
                        updated += 1
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    logger.debug(f"upsert failed for {sig.source_url}: {e!r}")
            if progress_cb is not None:
                progress_cb(scanned, total, hits)

        # Larger batches for higher throughput on the async client.
        batch = 64
        for i in range(0, len(rows), batch):
            await asyncio.gather(*[_one(e) for e in rows[i : i + batch]])

    return {
        "scanned": scanned,
        "hits": hits,
        "new": new,
        "updated": updated,
        "errors": errors,
        "total_candidates": total,
        "target_year": target_year,
    }


def collect_corporate_signals(
    target_year: int = 2026,
    limit: Optional[int] = None,
    only_with_website: bool = True,
    progress_cb=None,
) -> dict:
    """Sync entrypoint — runs the async crawl and returns counts.

    ``progress_cb(scanned, total, hits)`` is called after each exhibitor.
    """
    return asyncio.run(
        _collect_async(
            target_year=target_year, limit=limit,
            only_with_website=only_with_website,
            progress_cb=progress_cb,
        )
    )


# ---------------------------------------------------------------------------
# Bulk URL paste — operator drops 1..N URLs (e.g. from a Google search) and
# we fetch + scan + upsert them all in one go.  Much faster than scanning
# every exhibitor's site, and totally legal-posture-compliant (the operator
# already saw the URL in a search engine — we just save the trip).
# ---------------------------------------------------------------------------


_PLATFORM_HINTS: list[tuple[str, str]] = [
    ("linkedin.com", "linkedin"),
    ("x.com", "x"),
    ("twitter.com", "x"),
    ("web.archive.org", "wayback"),
]


def _platform_for_url(url: str) -> str:
    low = url.lower()
    for needle, label in _PLATFORM_HINTS:
        if needle in low:
            return label
    return "corporate_site"


def _signal_type_for_platform(platform: str) -> str:
    if platform == "linkedin":
        return "personal_linkedin_post"
    if platform == "x":
        return "social_post"
    if platform == "wayback":
        return "press_release"
    return "company_announcement"


async def _process_one_url(
    client: HttpClient, url: str, target_year: int,
    fallback_year: Optional[int] = None,
) -> Optional[RawSignal]:
    """Fetch ``url`` and build a RawSignal if we can extract enough info.

    For LinkedIn personal profile URLs (``/in/<slug>``) we short-circuit
    and derive the name from the slug — LinkedIn returns a login wall to
    anonymous fetches, so the slug is the only public-derivable info we
    can use without authentication.
    """
    # 1.bis LinkedIn /posts/<slug>_... — the slug is either a company
    # handle (e.g. ``patria``, ``actia-aerospace``) or a personal handle
    # (e.g. ``charles-beaudouin-996789189``). LinkedIn returns a login
    # wall to anonymous fetches, so we derive what we can from the URL
    # itself : extract the slug, classify person vs company by shape,
    # and build a signal accordingly.
    posts_match = re.search(
        r"linkedin\.com/posts/([^/_?#]+)_", url, re.IGNORECASE,
    )
    if posts_match:
        slug = posts_match.group(1)
        raw_parts = [p for p in slug.split("-") if p]
        # Track whether the slug ends with a digit/hex ID — this is the
        # SIGNATURE of a personal LinkedIn profile (LinkedIn appends a
        # unique 6-12 digit number to disambiguate). Companies almost
        # never have one. We use this as the primary person/company
        # discriminator (much more reliable than a token blacklist).
        had_id_suffix = False
        parts = list(raw_parts)
        while parts and (
            parts[-1].isdigit()
            or re.fullmatch(r"[a-f0-9]{6,}", parts[-1])
        ):
            parts.pop()
            had_id_suffix = True
        # Person heuristic : the slug had a numeric/hex suffix AND the
        # remaining tokens are 2-3 alpha words. Otherwise → company.
        is_person = (
            had_id_suffix
            and 2 <= len(parts) <= 3
            and all(p.isalpha() and len(p) >= 2 for p in parts)
        )
        if is_person:
            from app.attendance.enrich import _clean_name
            person_name = _clean_name(
                " ".join(p.capitalize() for p in parts[:3])
            )
            if person_name:
                # Build the personal-profile URL too, so the operator
                # can navigate from the post to the profile.
                profile_url = f"https://www.linkedin.com/in/{slug}/"
                return RawSignal(
                    edition_year=target_year,
                    entity_type="person",
                    source_url=url,  # keep the post URL as the source
                    person_name=person_name,
                    source_platform="linkedin",
                    source_title=f"LinkedIn post: {person_name}",
                    source_snippet=(
                        f"LinkedIn post by {person_name} "
                        f"(slug-derived). Profile: {profile_url}"
                    ),
                    search_query_used="bulk_paste:linkedin_post_person",
                    signal_type="personal_linkedin_post",
                    signal_text=(
                        f"LinkedIn post by {person_name} mentioning "
                        f"Eurosatory. Profile: {profile_url}"
                    ),
                    is_personal_post=True,
                    notes=(
                        "Derived from LinkedIn /posts/<slug>_ URL "
                        "(no authenticated fetch). "
                        f"LinkedIn: {profile_url}"
                    ),
                )
        # Otherwise treat as company post.
        company_name = " ".join(
            p.capitalize() for p in parts
        ).strip() or slug.replace("-", " ").title()
        company_page_url = (
            f"https://www.linkedin.com/company/{slug}/"
        )
        return RawSignal(
            edition_year=target_year,
            entity_type="company",
            source_url=url,
            company_name=company_name,
            source_platform="linkedin",
            source_title=f"LinkedIn post: {company_name}",
            source_snippet=(
                f"Company LinkedIn post by {company_name} mentioning "
                f"Eurosatory. Page: {company_page_url}"
            ),
            search_query_used="bulk_paste:linkedin_post_company",
            signal_type="company_announcement",
            signal_text=(
                f"Company LinkedIn post by {company_name} announcing "
                f"presence at Eurosatory. LinkedIn page: "
                f"{company_page_url}"
            ),
            is_company_post=True,
            notes=(
                "Derived from LinkedIn /posts/<slug>_ URL (no "
                "authenticated fetch). "
                f"LinkedIn page: {company_page_url}"
            ),
        )

    # 1. LinkedIn /in/<slug> short-circuit — derive name from slug.
    if "linkedin.com/in/" in url.lower():
        from app.attendance.enrich import (
            _LINKEDIN_TRAILING_TOKENS, _slug_to_name,
        )
        name = _slug_to_name(url)
        if name:
            # Try to recover the role from the trailing slug tokens (CEO,
            # General, Procurement Officer …).
            slug_match = re.search(
                r"linkedin\.com/in/([^/?#]+)", url, re.IGNORECASE,
            )
            role: Optional[str] = None
            # Acronym roles that should stay all-uppercase rather than
            # capitalised ("Ceo" → "CEO").
            _ACRONYMS = {"ceo", "cto", "coo", "cfo", "cio", "ciso", "vp",
                         "phd", "dr"}

            def _format_role_token(t: str) -> str:
                low = t.lower()
                if low in _ACRONYMS:
                    return low.upper()
                return t.capitalize()

            if slug_match:
                tail_tokens: list[str] = []
                for tok in reversed(slug_match.group(1).split("-")):
                    low = tok.lower()
                    if low in _LINKEDIN_TRAILING_TOKENS:
                        tail_tokens.insert(0, _format_role_token(tok))
                    elif tail_tokens:
                        break
                    else:
                        if not low.isdigit() and not re.fullmatch(
                            r"[a-f0-9]{6,}", low
                        ):
                            break
                if tail_tokens:
                    role = " ".join(tail_tokens)
            return RawSignal(
                edition_year=target_year,
                entity_type="person",
                source_url=url,
                person_name=name,
                person_role=role,
                source_platform="linkedin",
                source_title=f"LinkedIn profile: {name}",
                source_snippet="LinkedIn personal profile (slug-derived).",
                search_query_used="bulk_paste:linkedin_slug",
                signal_type="personal_linkedin_post",
                signal_text=f"Public LinkedIn profile of {name}"
                + (f", {role}" if role else ""),
                is_personal_post=True,
                notes="Derived from LinkedIn /in/ slug "
                "(no authenticated fetch).",
            )
        # If slug parsing failed (admin / abc / digits-only), drop through
        # to the generic fetch path so the URL can at least be recorded.

    # 2. Generic path — fetch + parse.
    try:
        r = await client.request("GET", url)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"GET {url} failed: {e!r}")
        return None
    if r.status_code >= 400:
        return None
    html = r.text or ""
    if not html:
        return None
    text = _visible_text(html)
    title = _page_title(html)
    hit = _scan_text_for_year(text, target_year)
    year = (hit.year if hit else (fallback_year or target_year))
    snippet = (
        hit.snippet if hit else (text[:300] if text else (title or url))
    )
    platform = _platform_for_url(url)
    # Best-effort company name from <meta property="og:site_name"> or hostname.
    try:
        tree = HTMLParser(html)
        og_site = tree.css_first('meta[property="og:site_name"]')
        site_name = og_site.attributes.get("content") if og_site else None
    except Exception:  # noqa: BLE001
        site_name = None
    if not site_name:
        from urllib.parse import urlsplit
        host = urlsplit(url).netloc
        site_name = host.removeprefix("www.").split(".", 1)[0].title()
    # For social platforms (linkedin / x), the host is the platform itself
    # ("LinkedIn", "X") — NOT the actual employer of the person posting.
    # Leave company_name=None so the enricher can derive it later from
    # the post content / og:site_name / profile page.
    is_social = platform in ("linkedin", "x")
    return RawSignal(
        edition_year=year,
        entity_type=("person" if platform == "linkedin" else "company"),
        source_url=str(r.url),
        company_name=None if is_social else site_name,
        source_platform=platform,
        source_title=title,
        source_snippet=snippet,
        search_query_used="bulk_paste",
        signal_type=_signal_type_for_platform(platform),
        signal_text=snippet,
        is_company_post=(platform == "corporate_site"),
        is_personal_post=(platform == "linkedin"),
        notes=f"Imported via bulk URL paste ({platform}).",
    )


async def _bulk_paste_async(
    urls: list[str],
    target_year: int,
    progress_cb=None,
) -> dict:
    scanned = 0
    new = 0
    updated = 0
    skipped = 0
    errors = 0
    results: list[dict] = []

    async with HttpClient(
        per_host_delay=0.1, concurrency=12, timeout=10, max_retries=1,
    ) as c:
        async def _one(url: str) -> None:
            nonlocal scanned, new, updated, skipped, errors
            scanned += 1
            try:
                sig = await _process_one_url(c, url, target_year)
            except Exception as e:  # noqa: BLE001
                errors += 1
                results.append(
                    {"url": url, "status": "error", "detail": str(e)}
                )
                if progress_cb:
                    progress_cb(scanned, len(urls), new + updated)
                return
            if sig is None:
                skipped += 1
                results.append(
                    {"url": url, "status": "skipped",
                     "detail": "no fetchable content"}
                )
                if progress_cb:
                    progress_cb(scanned, len(urls), new + updated)
                return
            try:
                _, is_new = upsert_signal(sig)
                if is_new:
                    new += 1
                    results.append(
                        {"url": url, "status": "new",
                         "detail": sig.source_title or sig.company_name or ""}
                    )
                else:
                    updated += 1
                    results.append(
                        {"url": url, "status": "updated",
                         "detail": sig.source_title or sig.company_name or ""}
                    )
            except Exception as e:  # noqa: BLE001
                errors += 1
                results.append(
                    {"url": url, "status": "error", "detail": str(e)}
                )
            if progress_cb:
                progress_cb(scanned, len(urls), new + updated)

        await asyncio.gather(*[_one(u) for u in urls])

    return {
        "scanned": scanned,
        "new": new,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }


def bulk_paste_signals(
    urls: list[str], target_year: int = 2026, progress_cb=None,
) -> dict:
    """Sync entrypoint — fetch each URL, extract metadata, upsert as a
    signal.  Returns ``{scanned, new, updated, skipped, errors, results}``.
    """
    cleaned = [u.strip() for u in urls if u and u.strip()]
    cleaned = [u for u in cleaned if u.startswith(("http://", "https://"))]
    if not cleaned:
        return {"scanned": 0, "new": 0, "updated": 0, "skipped": 0,
                 "errors": 0, "results": []}
    return asyncio.run(
        _bulk_paste_async(cleaned, target_year, progress_cb=progress_cb)
    )
