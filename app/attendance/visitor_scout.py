"""Scout for *visitor*-oriented attendance signals (NOT exhibitors).

Strategy
--------
Unlike the auto-scrape on the exhibitor catalog (which finds "we will be
exhibiting at Eurosatory" — useful but redundant with the Companies tab),
this module looks for **potential VISITORS** — defense ministries,
delegations, procurement officers, journalists, primes scouting the show.

Sources
-------
1. **Defense press**: ``defensenews.com``, ``shephardmedia.com``,
   ``breakingdefense.com``, ``janes.com``. They publish "who's going to
   Eurosatory" / delegation announcements.
2. **Government / military sites**: ``defense.gouv.fr``, ``army.mil``,
   ``bundeswehr.de``, ``gov.uk/MOD`` — official delegation pages.

For each seed site we fetch the homepage + a ``site:`` style search
proxy (Google / DuckDuckGo redirector) limited to that domain, and
crawl the matching pages — extracting visitor-style signals via the
existing patterns. Each upserted signal carries
``signal_type=media_article`` (or ``official_delegation`` when the host
is a ``.gov`` / ministry) so the operator can tell visitors apart from
the exhibitor announcements.

Legal posture
-------------
- Public, indexed pages only.
- No login, no paywall bypass.
- Article snippets are stored as ``source_snippet`` (≤300 chars) — fair
  use under the press analysis exception of the InfoSoc directive.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import urljoin, urlsplit

from loguru import logger
from selectolax.parser import HTMLParser

from app.attendance.auto_collect import (
    _EUROSATORY_RX, _page_title, _scan_text_for_year, _visible_text,
)
from app.attendance.seed import RawSignal, upsert_signal
from app.scrapers.http_client import HttpClient


# ---------------------------------------------------------------------------
# Seed sources — each entry: (homepage_url, kind)
# kind ∈ {"press", "gov"}.  Used to default-tag the signal_type.
# ---------------------------------------------------------------------------


VISITOR_SEEDS: list[tuple[str, str]] = [
    # Defense press — search / tag pages where Eurosatory coverage lives.
    # Homepage scans miss them (Eurosatory news isn't on the front page
    # outside of the show week).
    ("https://www.defensenews.com/search/?q=eurosatory", "press"),
    ("https://www.shephardmedia.com/?s=eurosatory", "press"),
    ("https://breakingdefense.com/tag/eurosatory/", "press"),
    ("https://breakingdefense.com/?s=eurosatory", "press"),
    ("https://www.janes.com/?s=eurosatory", "press"),
    ("https://www.armada.ch/?s=eurosatory", "press"),
    # French defense press
    ("https://www.opex360.com/?s=eurosatory", "press"),
    ("https://www.meta-defense.fr/?s=eurosatory", "press"),
    ("https://www.air-cosmos.com/?s=eurosatory", "press"),
    ("https://www.forcesoperations.com/?s=eurosatory", "press"),
    # Eurosatory's own visitor / press / delegation pages
    ("https://www.eurosatory.com/en/visit", "press"),
    ("https://www.eurosatory.com/visit/professional-visitors/", "press"),
    ("https://www.eurosatory.com/en/press/press-releases", "press"),
    # Government / ministry search pages
    ("https://www.defense.gouv.fr/search?keys=eurosatory", "gov"),
    ("https://www.gov.uk/search/news-and-communications?keywords=eurosatory&"
     "organisations[]=ministry-of-defence", "gov"),
]


# Eurosatory link patterns — anchors on a homepage / index that point to
# Eurosatory-related articles.
_LINK_PATTERNS = re.compile(r"eurosatory", re.IGNORECASE)

# Visitor-oriented patterns we expect in the article body (any match).
_VISITOR_PATTERNS = re.compile(
    r"\b("
    r"delegation|delegations|"
    r"will\s+attend|will\s+visit|"
    r"visiting|visit\s+to|visited"
    r"|attending|attendees?"
    r"|chef\s+de\s+l[ae]\s+d[ée]l[ée]gation"
    r"|vient\s+visiter|vient\s+\u00e0\s+Eurosatory"
    r"|procurement\s+officer"
    r"|defense\s+attach[ée]"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


async def _fetch_html(client: HttpClient, url: str) -> Optional[str]:
    try:
        r = await client.request("GET", url)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"GET {url} failed: {e!r}")
        return None
    if r.status_code >= 400:
        return None
    if "html" not in (r.headers.get("content-type") or "").lower():
        return None
    return r.text or None


def _find_eurosatory_links(html: str, base_url: str) -> list[str]:
    """Return absolute URLs whose anchor text or href contains 'eurosatory'.

    Limited to the same registered domain as ``base_url`` — we don't follow
    cross-site links from a press homepage.
    """
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return []
    base_host = _domain(base_url)
    out: list[str] = []
    seen: set[str] = set()
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        text = a.text() or ""
        if not href:
            continue
        if not _LINK_PATTERNS.search(href + " " + text):
            continue
        full = urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            continue
        if _domain(full) != base_host:
            continue
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out[:25]  # cap per seed


def _extract_visitor_signal(
    html: str, url: str, kind: str, target_year: int,
) -> Optional[RawSignal]:
    """Try to extract a visitor-oriented RawSignal from one article.

    Press / gov sites *covering* Eurosatory are inherently visitor-side
    (they're not exhibitor self-promotion), so any Eurosatory mention is
    enough to upsert a signal. The visitor-pattern check stays as a
    *boost* (signal_text trimmed around the visitor verb when found),
    not a hard filter.
    """
    text = _visible_text(html)
    if not text:
        return None
    hit = _scan_text_for_year(text, target_year)
    if hit is None:
        return None
    # Prefer the snippet around a visitor verb (delegation / attending /
    # …) when present — gives a more meaningful preview than the bare
    # year mention.
    vm = _VISITOR_PATTERNS.search(text)
    snippet = hit.snippet
    if vm:
        start = max(0, vm.start() - 120)
        end = min(len(text), vm.end() + 120)
        snippet = text[start:end].strip()
    title = _page_title(html)
    sig_type = (
        "official_delegation" if kind == "gov" else "media_article"
    )
    platform = "press" if kind == "press" else "official_site"
    return RawSignal(
        edition_year=hit.year,
        entity_type=("delegation" if kind == "gov" else "media"),
        source_url=url,
        company_name=None,  # extracted later via enrichment
        country=None,
        source_platform=platform,
        source_title=title,
        source_snippet=snippet,
        search_query_used="visitor_scout:" + kind,
        signal_type=sig_type,
        signal_text=snippet,
        is_official_delegation=(kind == "gov"),
        notes=f"Auto-scouted from {kind} site ({_domain(url)})",
    )


# ---------------------------------------------------------------------------
# Crawl one seed
# ---------------------------------------------------------------------------


async def _scout_seed(
    client: HttpClient, seed_url: str, kind: str, target_year: int,
) -> tuple[int, int, int]:
    """Fetch homepage, find article links, scrape each, upsert signals.

    Returns ``(pages_visited, hits, new)``.
    """
    pages_visited = 0
    hits = 0
    new = 0
    home_html = await _fetch_html(client, seed_url)
    if not home_html:
        return 0, 0, 0
    pages_visited = 1
    # Even the homepage may already contain Eurosatory mentions.
    sig = _extract_visitor_signal(home_html, seed_url, kind, target_year)
    if sig is not None:
        try:
            _, is_new = upsert_signal(sig)
            hits += 1
            if is_new:
                new += 1
        except Exception as e:  # noqa: BLE001
            logger.debug(f"upsert failed for {seed_url}: {e!r}")

    article_urls = _find_eurosatory_links(home_html, seed_url)
    if not article_urls:
        return pages_visited, hits, new

    async def _one(u: str) -> tuple[int, int]:
        nonlocal pages_visited
        html = await _fetch_html(client, u)
        pages_visited += 1
        if not html:
            return 0, 0
        sig = _extract_visitor_signal(html, u, kind, target_year)
        if sig is None:
            return 0, 0
        try:
            _, is_new = upsert_signal(sig)
            return 1, (1 if is_new else 0)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"upsert failed for {u}: {e!r}")
            return 0, 0

    chunks = await asyncio.gather(*[_one(u) for u in article_urls])
    for h, n in chunks:
        hits += h
        new += n
    return pages_visited, hits, new


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def _scout_async(
    target_year: int,
    seeds: Optional[list[tuple[str, str]]] = None,
    progress_cb=None,
) -> dict:
    sources = seeds or VISITOR_SEEDS
    async with HttpClient(
        per_host_delay=0.1, concurrency=10, timeout=10, max_retries=1,
    ) as c:
        pages_visited = 0
        hits = 0
        new = 0
        seeds_done = 0
        for seed_url, kind in sources:
            try:
                pv, h, n = await _scout_seed(c, seed_url, kind, target_year)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"scout {seed_url} failed: {e!r}")
                pv, h, n = 0, 0, 0
            pages_visited += pv
            hits += h
            new += n
            seeds_done += 1
            if progress_cb is not None:
                progress_cb(seeds_done, len(sources), hits)
    return {
        "seeds_total": len(sources),
        "pages_visited": pages_visited,
        "hits": hits,
        "new": new,
        "target_year": target_year,
    }


def scout_visitors(
    target_year: int = 2026,
    seeds: Optional[list[tuple[str, str]]] = None,
    progress_cb=None,
) -> dict:
    """Sync entrypoint — returns a counts dict."""
    return asyncio.run(
        _scout_async(target_year=target_year, seeds=seeds,
                      progress_cb=progress_cb)
    )
