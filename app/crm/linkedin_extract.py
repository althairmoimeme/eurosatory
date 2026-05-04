"""Backfill ``Exhibitor.linkedin_url`` from the company's own homepage HTML.

Most defense corporate sites link to their LinkedIn company page in the
footer. We already crawled the homepage and stored the text/HTML — we just
sweep it with a regex.

Only matches *company* pages (``linkedin.com/company/...`` and friends), not
personal profiles.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select

from app.database import CrawledPage

_LINKEDIN_COMPANY_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|school|showcase)/"
    r"([A-Za-z0-9_\-\.%]+)/?",
    re.IGNORECASE,
)


def find_linkedin_company(text: str) -> Optional[str]:
    if not text:
        return None
    m = _LINKEDIN_COMPANY_RE.search(text)
    if not m:
        return None
    url = m.group(0).rstrip("/")
    # Strip any trailing query / fragment
    return url.split("?")[0].split("#")[0]


def extract_linkedin(session, exhibitor_id: int) -> Optional[str]:
    """Return the first LinkedIn company URL found across crawled pages, or None."""
    pages = list(
        session.execute(
            select(CrawledPage).where(
                CrawledPage.exhibitor_id == exhibitor_id,
                CrawledPage.kind != "pdf",
                CrawledPage.text_excerpt.is_not(None),
            )
        ).scalars()
    )
    pages.sort(key=lambda p: (
        0 if p.kind == "homepage" else
        1 if p.kind == "about" else
        2 if p.kind == "contact" else
        3,
    ))
    for p in pages:
        url = find_linkedin_company(p.text_excerpt or "")
        if url:
            return url
    return None
