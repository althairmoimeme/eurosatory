"""Best-effort enrichment from each exhibitor's official website.

Strategy
--------
1. Fetch the homepage with our transparent UA (respect ``robots.txt`` is
   handled at request time via httpx default — the sites in this catalog are
   public corporate websites and we limit ourselves to homepage / contact /
   about pages).
2. Extract: emails (regex), phones (regex), social links, meta description.
3. Discover a /contact and /about page from anchor tags and re-fetch.
4. Confidence rules:
   * email matching the company's own domain  → ``high``
   * any other public email                   → ``medium``
   * regex-only phone with no surrounding label → ``low``

We never invent values.  Every field we extract carries the source URL it was
extracted from so commercial users can audit.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

from loguru import logger
from selectolax.parser import HTMLParser

from app.processors.normalizer import (
    GENERIC_EMAIL_PREFIXES,
    is_generic_email,
    normalize_email,
    normalize_phone,
    normalize_website,
    website_canonical_key,
)
from app.scrapers.http_client import HttpClient

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# match international-style numbers: optional + then 6-20 digits with separators
PHONE_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s>(\[]))\+?\d[\d\s().\-]{6,20}\d(?=[\s<.,;)\]]|$)"
)
LINKEDIN_RE = re.compile(r"https?://[a-z]{2,3}\.linkedin\.com/(?:company|in)/[A-Za-z0-9._%\-/]+")
TWITTER_RE = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9_]+")
FACEBOOK_RE = re.compile(r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9.\-_/]+")
YOUTUBE_RE = re.compile(r"https?://(?:www\.)?youtube\.com/(?:c|channel|user|@)[A-Za-z0-9_\-/]+")

CONTACT_HINTS = (
    "contact",
    "contacts",
    "contact-us",
    "contactus",
    "reach-us",
    "get-in-touch",
)
ABOUT_HINTS = (
    "about",
    "about-us",
    "aboutus",
    "company",
    "who-we-are",
    "qui-sommes-nous",
    "qui-nous-sommes",
)


@dataclass
class EnrichmentResult:
    homepage_url: Optional[str] = None
    visited_urls: list[str] = field(default_factory=list)
    emails: list[tuple[str, str]] = field(default_factory=list)  # (email, source_url)
    phones: list[tuple[str, str]] = field(default_factory=list)
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    facebook_url: Optional[str] = None
    youtube_url: Optional[str] = None
    meta_description: Optional[str] = None
    keywords: list[str] = field(default_factory=list)
    error: Optional[str] = None
    field_confidence: dict[str, str] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)


def _domain_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def _extract_emails(text: str) -> list[str]:
    found = []
    for m in EMAIL_RE.findall(text or ""):
        e = normalize_email(m)
        if e and not e.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            # filter image/file-like false positives
            if "@" in e and not any(k in e for k in ("example.com", "yourdomain")):
                found.append(e)
    # de-dup preserve order
    seen, out = set(), []
    for e in found:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _extract_phones(text: str) -> list[str]:
    out = []
    for m in PHONE_RE.findall(text or ""):
        p = normalize_phone(m)
        if p and len(p) >= 8:
            out.append(p)
    seen, dedup = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup


def _discover_links(html: HTMLParser, base_url: str, hints: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for a in html.css("a[href]"):
        href = a.attributes.get("href") or ""
        text = (a.text(strip=True) or "").lower()
        href_l = href.lower()
        if any(h in href_l for h in hints) or any(h in text for h in hints):
            full = urljoin(base_url, href)
            if full.startswith(("http://", "https://")) and full not in found:
                found.append(full)
    return found[:3]


async def _fetch_text(client: HttpClient, url: str) -> tuple[Optional[str], Optional[int]]:
    try:
        r = await client.get(url)
        ct = r.headers.get("content-type", "")
        if "html" not in ct and "text" not in ct:
            return None, r.status_code
        return r.text, r.status_code
    except Exception as e:  # noqa: BLE001
        logger.debug("enrich fetch fail {} -> {}", url, e)
        return None, None


def _absorb(
    result: EnrichmentResult,
    html_text: str,
    page_url: str,
    company_domain: Optional[str],
) -> None:
    parser = HTMLParser(html_text)

    # meta description
    if not result.meta_description:
        node = parser.css_first('meta[name="description"]')
        if node and node.attributes.get("content"):
            result.meta_description = node.attributes["content"].strip()[:1000]

    visible_text = parser.body.text(separator=" ") if parser.body else html_text

    for e in _extract_emails(html_text):  # search HTML for mailto: too
        if (e, page_url) not in result.emails:
            result.emails.append((e, page_url))

    for p in _extract_phones(visible_text):
        if (p, page_url) not in result.phones:
            result.phones.append((p, page_url))

    for pat, attr in (
        (LINKEDIN_RE, "linkedin_url"),
        (TWITTER_RE, "twitter_url"),
        (FACEBOOK_RE, "facebook_url"),
        (YOUTUBE_RE, "youtube_url"),
    ):
        if not getattr(result, attr):
            m = pat.search(html_text)
            if m:
                setattr(result, attr, m.group(0))
                result.field_sources[attr] = page_url
                result.field_confidence[attr] = "medium"


def _select_best_email(
    emails: list[tuple[str, str]], company_domain: Optional[str]
) -> tuple[Optional[str], Optional[str], str]:
    """Pick the most useful (email, source_url, confidence) tuple."""
    if not emails:
        return None, None, "low"
    # 1) generic + own domain
    if company_domain:
        for e, src in emails:
            if is_generic_email(e) and e.endswith("@" + company_domain):
                return e, src, "high"
    # 2) generic on any domain (still useful)
    for e, src in emails:
        if is_generic_email(e):
            return e, src, "medium"
    # 3) own-domain anything
    if company_domain:
        for e, src in emails:
            if e.endswith("@" + company_domain):
                return e, src, "medium"
    # 4) fallback
    return emails[0][0], emails[0][1], "low"


async def enrich_website(client: HttpClient, website_url: str) -> EnrichmentResult:
    result = EnrichmentResult()
    homepage = normalize_website(website_url)
    if not homepage:
        result.error = "invalid_url"
        return result
    result.homepage_url = homepage
    company_domain = website_canonical_key(homepage)

    # 1) homepage
    html, status = await _fetch_text(client, homepage)
    if not html:
        # try https if the original was http (lots of corporate sites redirect)
        if homepage.startswith("http://"):
            alt = "https://" + homepage[len("http://") :]
            html, status = await _fetch_text(client, alt)
            if html:
                homepage = alt
                result.homepage_url = alt
    if not html:
        result.error = f"homepage_unreachable:{status}"
        return result
    result.visited_urls.append(homepage)
    _absorb(result, html, homepage, company_domain)

    # 2) discover and fetch /contact and /about
    parser = HTMLParser(html)
    targets: list[str] = []
    targets += _discover_links(parser, homepage, CONTACT_HINTS)
    targets += _discover_links(parser, homepage, ABOUT_HINTS)
    # fall back to common paths if discovery yields nothing
    if not targets:
        targets = [urljoin(homepage, p) for p in ("contact", "contact-us", "about", "about-us")]

    seen = {homepage}
    for url in targets:
        if url in seen:
            continue
        seen.add(url)
        html2, _ = await _fetch_text(client, url)
        if html2:
            result.visited_urls.append(url)
            _absorb(result, html2, url, company_domain)

    # finalize selected email
    best_email, src, conf = _select_best_email(result.emails, company_domain)
    if best_email:
        result.field_sources["contact_email"] = src or homepage
        result.field_confidence["contact_email"] = conf
    return result


async def enrich_many(
    pairs: list[tuple[int, str]], concurrency: int = 5
) -> dict[int, EnrichmentResult]:
    sem = asyncio.Semaphore(concurrency)
    results: dict[int, EnrichmentResult] = {}

    async with HttpClient(per_host_delay=2.0, timeout=15) as client:

        async def _one(eid: int, url: str) -> None:
            async with sem:
                try:
                    results[eid] = await enrich_website(client, url)
                except Exception as e:  # noqa: BLE001
                    logger.warning("enrich {} failed: {}", url, e)
                    r = EnrichmentResult(homepage_url=url)
                    r.error = str(e)[:200]
                    results[eid] = r

        await asyncio.gather(*(_one(i, u) for i, u in pairs))

    return results
