"""Documented OSINT search queries for collecting public attendance signals.

These are *templates the operator runs manually* in their browser / a search
engine.  Nothing in this module performs scraping — we only document the
queries so the team uses a stable, auditable set, and so the search query
that produced each signal can be recorded on every imported row.

Legal posture
-------------
- Only public, indexed material.
- No paywall bypass, no LinkedIn login scraping.
- LinkedIn / X queries return *what Google indexed publicly* — operators must
  copy the URL and visible snippet that the search engine showed, not log
  into the source platform.
- No personal data outside professional context (no personal phones / emails).

Year coverage: 2024, 2025, 2026 — earlier editions are covered by the
``eurosatory_exhibitors_history`` table, not by this attendance signal table.
"""
from __future__ import annotations

from typing import Iterable

# General queries (per-year placeholder ``{year}``)
GENERAL_QUERIES: list[str] = [
    '"Eurosatory {year}" "will attend"',
    '"Eurosatory {year}" "attending"',
    '"Eurosatory {year}" "meet us"',
    '"Eurosatory {year}" "visit us"',
    '"Eurosatory {year}" "booth"',
    '"Eurosatory {year}" "stand"',
    '"Eurosatory {year}" "delegation"',
    '"Eurosatory {year}" "Ministry of Defence"',
    '"Eurosatory {year}" "our team"',
    '"Eurosatory {year}" "thank you for visiting"',
    '"Eurosatory {year}" "great week"',
]

HASHTAG_QUERIES: list[str] = [
    '"#Eurosatory{year}"',
    '"#Eurosatory"',
]

# LinkedIn / X queries — only what's publicly indexed (we follow the URL the
# search engine returns; we do not log in).
SOCIAL_QUERIES: list[str] = [
    'site:linkedin.com/posts "Eurosatory {year}" "attending"',
    'site:linkedin.com/posts "Eurosatory {year}" "will be attending"',
    'site:linkedin.com/posts "Eurosatory {year}" "come meet us"',
    'site:linkedin.com/posts "Eurosatory {year}" "visit us"',
    'site:linkedin.com/posts "Eurosatory {year}" "booth"',
    'site:linkedin.com/posts "Eurosatory {year}" "stand"',
    'site:linkedin.com/posts "#Eurosatory{year}"',
    'site:x.com "Eurosatory {year}" "attending"',
    'site:x.com "#Eurosatory{year}" "booth"',
]

# Source priorities — used by the operator to triage which results to record
SOURCE_PRIORITY = [
    "corporate_site",
    "press_release",
    "event_page",
    "national_pavilion",
    "media_article",
    "wayback",
    "social_post",
    "other",
]


def queries_for_year(year: int, *, include_social: bool = True) -> list[str]:
    """Return the full list of queries the operator should run for a year."""
    out: list[str] = []
    for q in GENERAL_QUERIES + HASHTAG_QUERIES:
        out.append(q.format(year=year))
    if include_social:
        for q in SOCIAL_QUERIES:
            out.append(q.format(year=year))
    return out


def queries_for_years(years: Iterable[int], *, include_social: bool = True) -> list[str]:
    out: list[str] = []
    for y in years:
        out.extend(queries_for_year(y, include_social=include_social))
    return out
