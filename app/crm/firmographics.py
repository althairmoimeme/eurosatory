"""Best-effort firmographic enrichment from crawled pages.

We extract three signals when present in the company's own text:
- ``founding_year``        — from "founded in 1998", "established in 2007", etc.
- ``employee_count_hint``  — from "100 employees", "team of 50", "200+ staff".
- ``employee_range``       — bucket the hint into the canonical range.

Never invents.  Returns ``None`` for any signal not found in plain prose.
Keyword-stuffed pages are skipped to avoid SEO false positives.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.database import CrawledPage
from app.processors.intelligence import _looks_like_keyword_stuffing

# Very conservative: only accept years 1900-2026 inside an "established/founded/since" phrase.
_FOUNDED_RE = re.compile(
    r"\b(?:founded|established|since|created|formed|incorporated|set up|"
    r"fond[ée]e?|cr[ée]+e?|cr[ée]ation en)\s*(?:in|en)?\s*(?:on\s+)?"
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)?\s*(\d{1,2}\s*,\s*)?(\d{4})\b",
    re.IGNORECASE,
)
_FOUNDED_RE_SHORT = re.compile(
    r"\b(?:in|en)\s+(\d{4})\b[^.]{0,80}\b(?:founded|established|created|formed|fond[ée]e?)\b",
    re.IGNORECASE,
)
_YEAR_RANGE_OK = range(1900, 2027)

_EMP_RE = re.compile(
    r"(?:more than\s+|over\s+|approximately\s+|about\s+|around\s+|nearly\s+|"
    r"plus de\s+|environ\s+|près de\s+)?"
    r"(\d{1,5})\s*\+?\s*(?:employees|professionals|staff|specialists|"
    r"experts|engineers|people|team members|salari[ée]s?|collaborateurs?)",
    re.IGNORECASE,
)
_EMP_TEAM_OF_RE = re.compile(
    r"\bteam of\s+(\d{1,5})\b", re.IGNORECASE,
)


@dataclass
class Firmographics:
    founding_year: Optional[int] = None
    employee_count_hint: Optional[int] = None
    employee_range: Optional[str] = None
    sources: dict[str, str] | None = None


_BANDS = [
    (10, "1-10"), (50, "11-50"), (200, "51-200"),
    (500, "201-500"), (1000, "501-1000"), (10**9, "1000+"),
]


def _to_band(n: int) -> str:
    for cap, label in _BANDS:
        if n <= cap:
            return label
    return "1000+"


def _extract_year_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    for regex in (_FOUNDED_RE, _FOUNDED_RE_SHORT):
        for m in regex.finditer(text):
            year_str = next((g for g in m.groups() if g and g.isdigit() and len(g) == 4), None)
            if not year_str:
                continue
            year = int(year_str)
            if year in _YEAR_RANGE_OK:
                return year
    return None


def _extract_employees_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    candidates: list[int] = []
    for m in _EMP_RE.finditer(text):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if 2 <= n <= 200_000:
            candidates.append(n)
    for m in _EMP_TEAM_OF_RE.finditer(text):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if 2 <= n <= 50_000:
            candidates.append(n)
    if not candidates:
        return None
    # Pick the largest plausible — companies advertise their max headcount.
    return max(candidates)


def extract_firmographics(session, exhibitor_id: int) -> Firmographics:
    """Scan stored CrawledPage rows (about / homepage / contact) for firmographic
    signals.  Returns a ``Firmographics`` with whatever we found.
    """
    # Prefer 'about' and 'company' pages, then homepage, then any other
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
        0 if p.kind == "about" else
        1 if p.kind == "homepage" else
        2 if p.kind in {"news", "industry"} else
        3,
        -len(p.text_excerpt or ""),
    ))

    out = Firmographics(sources={})
    for p in pages:
        text = p.text_excerpt or ""
        if _looks_like_keyword_stuffing(text):
            continue
        if out.founding_year is None:
            year = _extract_year_from_text(text)
            if year:
                out.founding_year = year
                out.sources["founding_year"] = p.url
        if out.employee_count_hint is None:
            emp = _extract_employees_from_text(text)
            if emp:
                out.employee_count_hint = emp
                out.employee_range = _to_band(emp)
                out.sources["employee_count_hint"] = p.url
        if out.founding_year and out.employee_count_hint:
            break
    return out
