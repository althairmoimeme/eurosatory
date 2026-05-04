"""Deep website crawler — collects up to N HTML pages and a few public PDFs per
exhibitor, focused on commercially relevant paths (products, solutions, services,
capabilities, industries, defense, aerospace, security, technology, about,
news, brochures, downloads).

Design choices
--------------
* Same-host only: never follows links off the company's own domain.
* BFS with two priority queues: priority paths first, "other" paths last.
* Hard caps: ``max_pages`` HTML + ``max_pdfs`` PDF + ``max_depth`` link depth.
* Per-host rate limit reuses ``HostThrottle`` from ``http_client``.
* Robots.txt: we honour the host's ``Disallow`` rules with a permissive UA
  string, falling back to "allow" when robots is unreachable.
* Body cap: skip pages over ``max_page_bytes`` to dodge gigantic JS bundles or
  multi-MB CMS dumps.

Output is a ``CrawlResult`` containing ``pages`` (kind, url, html, status) and
``pdfs`` (url, bytes, status).  Downstream extractors decide what to do with it.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from loguru import logger
from selectolax.parser import HTMLParser

from app.config import settings
from app.scrapers.http_client import HttpClient

# ---------------------------------------------------------------------------
# Path priorities — drive BFS ordering
# ---------------------------------------------------------------------------

PRIORITY_PATTERNS: list[tuple[str, str]] = [
    # (kind, regex matched against the URL path, lower-cased)
    ("product", r"/(products?|product[\-_]?list|catalogue?s?|portfolio)(/|$|\.)"),
    ("product", r"/solutions?(/|$|\.)"),
    ("product", r"/capabilit(y|ies)(/|$|\.)"),
    ("product", r"/offerings?(/|$|\.)"),
    ("product", r"/(systems?|technologies?|technology)(/|$|\.)"),
    ("service", r"/(services?|maintenance|support|integration|engineering)(/|$|\.)"),
    ("industry", r"/(industries?|markets?|sectors?|customers?|clients?)(/|$|\.)"),
    ("defense", r"/(defen[cs]e|defen[cs]e[\-_]?security|military|homeland|aerospace|naval|land|air[\-_]?force)(/|$|\.)"),
    ("about", r"/(about|company|who[\-_]?we[\-_]?are|qui[\-_]?sommes[\-_]?nous|notre[\-_]?soci[ée]t[ée])(/|$|\.)"),
    ("news", r"/(news|press|media|insights?|blog|presse)(/|$|\.)"),
    ("download", r"/(downloads?|documentation|brochures?|datasheets?|whitepapers?|resources?)(/|$|\.)"),
    ("contact", r"/(contact|reach[\-_]?us|get[\-_]?in[\-_]?touch)(/|$|\.)"),
]

# regex compiled once
_COMPILED_PRIORITY = [(kind, re.compile(pat, re.IGNORECASE)) for kind, pat in PRIORITY_PATTERNS]

# Skip obvious junk
SKIP_REGEX = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|woff2?|ttf|eot|mp4|mp3|zip|gz|tar|7z|exe|dmg)(\?|$)",
    re.IGNORECASE,
)
# Skip locale duplicates / utility pages we won't extract from
LOCALE_LANG_REGEX = re.compile(r"/(?:cookies?|privacy|legal|terms|mentions[\-_]?legales|cookie[\-_]?policy)(/|$)", re.IGNORECASE)

PDF_REGEX = re.compile(r"\.pdf(\?|$)", re.IGNORECASE)


@dataclass
class CrawlPage:
    url: str
    kind: str
    status: int
    html: Optional[str] = None
    bytes_: Optional[int] = None
    error: Optional[str] = None


@dataclass
class CrawlPdf:
    url: str
    status: int
    content: Optional[bytes] = None
    bytes_: Optional[int] = None
    error: Optional[str] = None


@dataclass
class CrawlResult:
    homepage: str
    host: str
    pages: list[CrawlPage] = field(default_factory=list)
    pdfs: list[CrawlPdf] = field(default_factory=list)
    error: Optional[str] = None

    def texts(self) -> list[tuple[str, str, str]]:
        """Return ``(kind, url, plain_text)`` for every successfully fetched HTML page."""
        out = []
        for p in self.pages:
            if p.html:
                try:
                    body = HTMLParser(p.html).body
                    text = body.text(separator=" ") if body else p.html
                except Exception:  # noqa: BLE001
                    text = p.html
                out.append((p.kind, p.url, text))
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_url(url: str) -> Optional[str]:
    path = urlsplit(url).path or "/"
    path_l = path.lower()
    for kind, pat in _COMPILED_PRIORITY:
        if pat.search(path_l):
            return kind
    return None


def _normalise_url(url: str) -> str:
    parts = urlsplit(url)
    # drop fragment, lower-case host
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path or '/'}" + (
        f"?{parts.query}" if parts.query else ""
    )


def _same_host(host: str, url: str) -> bool:
    h = (urlsplit(url).hostname or "").lower()
    if not h:
        return False
    return h == host or h.endswith("." + host) or host.endswith("." + h)


async def _load_robots(client: HttpClient, host: str) -> Optional[RobotFileParser]:
    rp = RobotFileParser()
    rp.set_url(f"https://{host}/robots.txt")
    try:
        r = await client.get(f"https://{host}/robots.txt")
        if r.status_code >= 400:
            return None
        rp.parse(r.text.splitlines())
        return rp
    except Exception:  # noqa: BLE001
        return None


def _allowed(rp: Optional[RobotFileParser], ua: str, url: str) -> bool:
    if rp is None:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:  # noqa: BLE001
        return True


def _extract_links(html: str, base_url: str) -> list[str]:
    parser = HTMLParser(html)
    out = []
    for a in parser.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        full = urljoin(base_url, href)
        if SKIP_REGEX.search(full) or LOCALE_LANG_REGEX.search(full):
            continue
        out.append(_normalise_url(full))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def crawl_site(
    homepage: str,
    *,
    max_pages: Optional[int] = None,
    max_pdfs: Optional[int] = None,
    max_depth: Optional[int] = None,
    client: Optional[HttpClient] = None,
) -> CrawlResult:
    """Crawl ``homepage`` and return the relevant pages + PDFs."""
    max_pages = max_pages or settings.deep_crawl_max_pages
    max_pdfs = max_pdfs or settings.deep_crawl_max_pdfs
    max_depth = max_depth if max_depth is not None else settings.deep_crawl_max_depth

    parts = urlsplit(homepage)
    host = (parts.hostname or "").lower()
    if not host:
        return CrawlResult(homepage=homepage, host="", error="invalid_url")

    owns_client = client is None
    if owns_client:
        client = HttpClient(
            per_host_delay=settings.deep_crawl_per_host_delay,
            timeout=settings.deep_crawl_timeout,
            concurrency=settings.deep_crawl_concurrency,
        )
        await client.__aenter__()

    result = CrawlResult(homepage=homepage, host=host)
    rp = await _load_robots(client, host)

    visited: set[str] = set()
    pdf_urls: list[tuple[int, str]] = []  # (depth, url)
    queue: list[tuple[int, str, Optional[str]]] = [(0, _normalise_url(homepage), "homepage")]
    pages_done = 0
    pdfs_done = 0

    try:
        while queue and pages_done < max_pages:
            queue.sort(key=lambda t: (0 if t[2] else 1, t[0]))  # priority pages first, then depth
            depth, url, kind_hint = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            if not _same_host(host, url):
                continue
            if not _allowed(rp, settings.http_user_agent, url):
                logger.debug("robots disallow {}", url)
                continue

            kind = kind_hint or _classify_url(url) or "other"
            try:
                r = await client.get(url)
            except Exception as e:  # noqa: BLE001
                result.pages.append(CrawlPage(url=url, kind=kind, status=0, error=str(e)[:200]))
                continue

            content_type = r.headers.get("content-type", "").lower()
            content_length = int(r.headers.get("content-length", 0) or 0)
            if "html" not in content_type and "xhtml" not in content_type:
                # if the homepage redirected to a PDF or anything else, skip
                if PDF_REGEX.search(url):
                    pdf_urls.append((depth, url))
                continue
            if content_length and content_length > settings.deep_crawl_max_page_bytes:
                continue

            html = r.text
            if len(html) > settings.deep_crawl_max_page_bytes:
                html = html[: settings.deep_crawl_max_page_bytes]

            page = CrawlPage(
                url=url, kind=kind, status=r.status_code, html=html, bytes_=len(html.encode("utf-8", "ignore"))
            )
            result.pages.append(page)
            pages_done += 1

            if depth >= max_depth:
                continue

            for link in _extract_links(html, url):
                if link in visited:
                    continue
                if not _same_host(host, link):
                    continue
                if PDF_REGEX.search(link):
                    pdf_urls.append((depth + 1, link))
                    continue
                kind_l = _classify_url(link)
                queue.append((depth + 1, link, kind_l))

        # de-dup PDF urls and bound count
        seen_pdf: set[str] = set()
        unique_pdfs = []
        for d, u in sorted(pdf_urls, key=lambda t: t[0]):
            if u in seen_pdf:
                continue
            seen_pdf.add(u)
            unique_pdfs.append(u)
            if len(unique_pdfs) >= max_pdfs:
                break

        for url in unique_pdfs:
            if not _allowed(rp, settings.http_user_agent, url):
                continue
            try:
                r = await client.get(url)
            except Exception as e:  # noqa: BLE001
                result.pdfs.append(CrawlPdf(url=url, status=0, error=str(e)[:200]))
                continue
            if r.status_code >= 400:
                result.pdfs.append(CrawlPdf(url=url, status=r.status_code))
                continue
            data = r.content
            if len(data) > 8_000_000:  # cap PDF at 8 MB
                continue
            result.pdfs.append(
                CrawlPdf(url=url, status=r.status_code, content=data, bytes_=len(data))
            )
            pdfs_done += 1

        return result
    finally:
        if owns_client:
            await client.__aexit__(None, None, None)


async def crawl_many(
    pairs: Iterable[tuple[int, str]],
    *,
    max_pages: Optional[int] = None,
    max_pdfs: Optional[int] = None,
    max_depth: Optional[int] = None,
    concurrency: Optional[int] = None,
) -> dict[int, CrawlResult]:
    sem = asyncio.Semaphore(concurrency or settings.deep_crawl_concurrency)
    results: dict[int, CrawlResult] = {}

    async def _one(eid: int, url: str) -> None:
        async with sem:
            try:
                results[eid] = await crawl_site(
                    url, max_pages=max_pages, max_pdfs=max_pdfs, max_depth=max_depth
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("crawl {} failed: {}", url, e)
                results[eid] = CrawlResult(homepage=url, host="", error=str(e)[:200])

    await asyncio.gather(*(_one(eid, url) for eid, url in pairs))
    return results
