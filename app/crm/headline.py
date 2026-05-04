"""Extract a single-line, *self-described* headline from each exhibitor's
homepage — the company's own one-liner, surfacing what they say about
themselves rather than our derived classification.

Sources tried, in order:
1. ``<meta name="description">``
2. ``<meta property="og:description">``
3. ``<title>``
4. First prose sentence of the body (60-220 chars, defense-keyword bonus)
5. (Caller fallback) first sentence of the existing ``activity_summary``

Result is rejected when it looks like keyword stuffing or generic nav text.
"""
from __future__ import annotations

import html
import re
from typing import Optional

from sqlalchemy import select

from app.database import CrawledPage
from app.processors.intelligence import _looks_like_keyword_stuffing

_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']{30,500})["\']',
    re.IGNORECASE,
)
_OG_DESC_RE = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]*content=["\']([^"\']{30,500})["\']',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]{8,200})</title>", re.IGNORECASE)

_NAV_NOISE = re.compile(
    # We avoid the bare token ``navigation`` because legitimate sentences
    # mention "GPS navigation", "UAV navigation", etc.  We only flag the
    # web-UI shapes: "toggle navigation", "skip navigation", "main navigation",
    # "site navigation".
    r"\b(cookie|privacy policy|gdpr|toggle menu|toggle navigation|"
    r"skip navigation|main navigation|site navigation|copyright|"
    r"all rights reserved|sign up to|subscribe to our|newsletter|home page|"
    r"page not found|404|loading\.\.\.)\b",
    re.IGNORECASE,
)
_DEFENSE_HINT = re.compile(
    r"\b(defen[sc]e|defense|military|tactical|surveillance|reconnaissance|"
    r"combat|missile|radar|drone|UAV|cyber|protection|intelligence|"
    r"simulation|aerospace|naval|land force|homeland|security|sensor|"
    r"optronic|optical|night vision|ammunition|armor|armour|ballistic|"
    r"weapon|MRO|integrate|integration|manufacture|design)\b",
    re.IGNORECASE,
)
_TITLE_BAD_TAILS = re.compile(
    r"\s*[\|\-–—]\s*(home|homepage|accueil|welcome|official site|official "
    r"website|site officiel)\s*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    # Decode HTML entities (&amp;, &#39;, …) and collapse whitespace.
    return " ".join(html.unescape(text or "").split())


def _looks_usable(text: str) -> bool:
    if not text or len(text) < 24:
        return False
    if _NAV_NOISE.search(text):
        return False
    if _looks_like_keyword_stuffing(text):
        return False
    return True


def _extract_first_prose(plain_text: str) -> Optional[str]:
    """Pick the first 60-220-char sentence with a defense hint, fallback to
    any clean sentence in that length range."""
    if not plain_text:
        return None
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"\u201C\u00AB])", plain_text)[:30]
    candidates: list[tuple[int, str]] = []
    for s in sentences:
        s = _clean(s)
        if not (60 <= len(s) <= 220):
            continue
        if _NAV_NOISE.search(s):
            continue
        score = 5
        if _DEFENSE_HINT.search(s):
            score -= 2
        candidates.append((score, s))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def extract_headline_from_text(homepage_text: Optional[str]) -> Optional[str]:
    """Run the extraction on the stored ``text_excerpt`` of the homepage.

    The excerpt may be raw HTML (older crawls) or pre-cleaned plain text
    (newer crawls).  We try meta tags first since they survive in both cases
    (they sit in the head, which is always within the first 4-6 KB).
    """
    if not homepage_text:
        return None

    for regex in (_META_DESC_RE, _OG_DESC_RE):
        m = regex.search(homepage_text)
        if not m:
            continue
        text = _clean(m.group(1))
        if _looks_usable(text):
            return text[:300]

    m = _TITLE_RE.search(homepage_text)
    if m:
        title = _TITLE_BAD_TAILS.sub("", _clean(m.group(1)))
        if _looks_usable(title) and len(title) >= 30:
            return title[:300]

    # Fall back to first prose sentence (works when text_excerpt is plain text)
    prose = _extract_first_prose(homepage_text)
    if prose and _looks_usable(prose):
        return prose[:300]
    return None


def headline_from_summary(summary: Optional[str]) -> Optional[str]:
    """Last-resort fallback: take the first prose sentence of the existing
    activity_summary.  This guarantees that any exhibitor with usable summary
    text gets a headline, even when their site's meta tags are useless.
    """
    if not summary:
        return None
    s = _clean(summary)
    if _looks_like_keyword_stuffing(s):
        return None
    # Pick the first complete sentence
    m = re.search(r"^(.{40,260}?[.!?])(?=\s|$)", s)
    pick = (m.group(1) if m else s)[:260]
    pick = pick.strip()
    if _looks_usable(pick):
        return pick
    return None


def extract_headline(session, exhibitor_id: int, *, fallback_summary: Optional[str] = None) -> Optional[str]:
    """Read the homepage CrawledPage row for ``exhibitor_id`` and extract a
    headline.  Falls back to the longest available crawled text on the
    exhibitor when no homepage row exists, then to ``fallback_summary``.
    """
    homepage = session.scalar(
        select(CrawledPage).where(
            CrawledPage.exhibitor_id == exhibitor_id,
            CrawledPage.kind == "homepage",
        )
    )
    if homepage is not None and homepage.text_excerpt:
        h = extract_headline_from_text(homepage.text_excerpt)
        if h:
            return h

    pages = list(
        session.execute(
            select(CrawledPage).where(
                CrawledPage.exhibitor_id == exhibitor_id,
                CrawledPage.kind != "pdf",
                CrawledPage.text_excerpt.is_not(None),
            )
        ).scalars()
    )
    pages.sort(key=lambda p: len(p.text_excerpt or ""), reverse=True)
    for p in pages[:3]:
        h = extract_headline_from_text(p.text_excerpt)
        if h:
            return h

    return headline_from_summary(fallback_summary)
