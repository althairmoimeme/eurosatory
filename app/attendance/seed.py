"""Ingest a raw OSINT signal into ``AttendanceSignal``.

A "raw" signal is the minimal information an operator collects from a public
source: who (person / company), where (URL), what year, what platform, what
the post said, and which search query produced it.  Everything else
(score, role category, dedup, GDPR risk, next action, recommended angle) is
computed deterministically from those inputs.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger
from sqlalchemy import select

from app.attendance.dedup import (
    canonical_company_name,
    canonical_person_name,
    dedupe_key,
)
from app.attendance.scorer import (
    UNKNOWN_ROLE,
    commercial_relevance,
    compute_presence_score,
    gdpr_risk_level,
    initial_validation_status,
    meeting_potential,
    next_best_action,
    presence_confidence,
    reason_to_contact,
    recommended_angle,
    role_category,
    sales_priority,
)
from app.database import AttendanceSignal, session_scope


VALID_ENTITY_TYPES = {"person", "company", "delegation", "institution", "media", "unknown"}
VALID_SIGNAL_TYPES = {
    "company_announcement", "personal_linkedin_post", "official_delegation",
    "national_pavilion", "press_release", "event_page", "social_post",
    "media_article",
}


@dataclass
class RawSignal:
    edition_year: int
    entity_type: str
    source_url: str

    person_name: Optional[str] = None
    person_role: Optional[str] = None
    company_name: Optional[str] = None
    country: Optional[str] = None
    country_iso2: Optional[str] = None
    source_platform: Optional[str] = None
    source_title: Optional[str] = None
    source_snippet: Optional[str] = None
    search_query_used: Optional[str] = None
    signal_type: Optional[str] = None
    signal_text: Optional[str] = None
    is_company_post: bool = False
    is_personal_post: bool = False
    is_official_delegation: bool = False
    is_exhibitor_employee: bool = False
    notes: Optional[str] = None


def _normalise(raw: RawSignal) -> RawSignal:
    """Coerce types + normalise enums.  Raises on invalid required fields."""
    if raw.entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"entity_type={raw.entity_type!r} not in {sorted(VALID_ENTITY_TYPES)}"
        )
    if raw.signal_type and raw.signal_type not in VALID_SIGNAL_TYPES:
        raise ValueError(
            f"signal_type={raw.signal_type!r} not in {sorted(VALID_SIGNAL_TYPES)}"
        )
    if not raw.source_url or not raw.source_url.strip():
        raise ValueError("source_url is required")
    if raw.entity_type == "person" and not (raw.person_name and raw.person_name.strip()):
        # OK — operator may know a person attended but not their name; we
        # still keep the row but force entity_type=unknown for clarity.
        raw.entity_type = "unknown"
    return raw


def _elect_canonical(s, key: str) -> Optional[AttendanceSignal]:
    """Among all rows sharing ``dedupe_key``, return the one that should be
    canonical (highest presence_score, oldest id as tiebreaker).
    """
    rows = list(
        s.execute(
            select(AttendanceSignal)
            .where(AttendanceSignal.dedupe_key == key)
        ).scalars()
    )
    if not rows:
        return None
    rows.sort(key=lambda r: (-(r.presence_score or 0), r.id))
    return rows[0]


def upsert_signal(raw: RawSignal) -> tuple[AttendanceSignal, bool]:
    """Insert (or update if same source_url already in DB) one signal.

    Returns ``(row, is_new)``.  Re-elects canonical if needed.
    """
    raw = _normalise(raw)
    canon_company = canonical_company_name(raw.company_name)
    canon_person = canonical_person_name(raw.person_name)
    key = dedupe_key(
        canonical_person=canon_person,
        canonical_company=canon_company,
        edition_year=raw.edition_year,
    )

    role_cat = role_category(raw.person_role)
    presence = compute_presence_score(
        signal_text=raw.signal_text,
        signal_type=raw.signal_type,
        person_name=raw.person_name,
        is_official_delegation=raw.is_official_delegation,
    )
    confidence = presence_confidence(presence.score)
    relevance = commercial_relevance(role_cat, raw.entity_type)
    priority = sales_priority(confidence, relevance)
    nba = next_best_action(confidence, relevance, raw.entity_type)
    gdpr = gdpr_risk_level(raw.entity_type, presence.score)
    validation = initial_validation_status(gdpr)

    with session_scope() as s:
        existing = s.scalar(
            select(AttendanceSignal).where(
                AttendanceSignal.source_url == raw.source_url,
                AttendanceSignal.edition_year == raw.edition_year,
            )
        )
        if existing is None:
            obj = AttendanceSignal(source_url=raw.source_url)
            s.add(obj)
            is_new = True
        else:
            obj = existing
            is_new = False

        obj.edition_year = raw.edition_year
        obj.entity_type = raw.entity_type
        obj.person_name = raw.person_name
        obj.person_role = raw.person_role
        obj.role_category = role_cat
        obj.company_name = raw.company_name
        obj.country = raw.country
        obj.country_iso2 = (raw.country_iso2 or "").upper()[:2] or None
        obj.source_platform = raw.source_platform
        obj.source_title = raw.source_title
        obj.source_snippet = raw.source_snippet
        obj.search_query_used = raw.search_query_used
        obj.signal_type = raw.signal_type
        obj.signal_text = raw.signal_text
        obj.signal_strength_reason = presence.reason
        obj.is_company_post = bool(raw.is_company_post)
        obj.is_personal_post = bool(raw.is_personal_post)
        obj.is_official_delegation = bool(raw.is_official_delegation)
        obj.is_exhibitor_employee = bool(raw.is_exhibitor_employee)
        obj.presence_score = presence.score
        obj.presence_confidence = confidence
        obj.commercial_relevance = relevance
        obj.sales_priority = priority
        obj.meeting_potential = meeting_potential(priority, role_cat)
        obj.next_best_action = nba
        obj.canonical_company_name = canon_company
        obj.canonical_person_name = canon_person
        obj.dedupe_key = key
        obj.gdpr_risk_level = gdpr
        if not obj.manual_validation_status or obj.manual_validation_status == "Pending":
            obj.manual_validation_status = validation
        obj.notes = raw.notes
        obj.reason_to_contact = reason_to_contact(
            role_cat=role_cat, company_name=raw.company_name,
            confidence=confidence, relevance=relevance,
            signal_type=raw.signal_type,
        )
        obj.recommended_angle = recommended_angle(role_cat, relevance)

        s.flush()  # ensure obj.id

        # Re-elect canonical for this dedupe_key
        canonical = _elect_canonical(s, key)
        if canonical is not None:
            for r in s.execute(
                select(AttendanceSignal).where(AttendanceSignal.dedupe_key == key)
            ).scalars():
                r.is_duplicate = (r.id != canonical.id)
                r.duplicate_group_id = canonical.id

        return obj, is_new


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------

# Required headers in the CSV (exact match, case-insensitive)
CSV_REQUIRED = ["edition_year", "entity_type", "source_url"]
CSV_OPTIONAL = [
    "person_name", "person_role", "company_name", "country", "country_iso2",
    "source_platform", "source_title", "source_snippet", "search_query_used",
    "signal_type", "signal_text",
    "is_company_post", "is_personal_post", "is_official_delegation",
    "is_exhibitor_employee", "notes",
]


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y", "vrai"}


def import_csv(path: Path) -> dict:
    """Bulk-import a CSV file of raw signals.

    Returns a summary dict with counts.  Skips rows missing required fields
    and logs a warning per skip.
    """
    summary = {"read": 0, "inserted": 0, "updated": 0, "skipped": 0}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        # normalise headers
        if not reader.fieldnames:
            raise ValueError(f"CSV {path} has no header row")
        for row in reader:
            summary["read"] += 1
            try:
                raw = RawSignal(
                    edition_year=int(row["edition_year"]),
                    entity_type=(row.get("entity_type") or "unknown").strip().lower(),
                    source_url=row["source_url"].strip(),
                    person_name=(row.get("person_name") or "").strip() or None,
                    person_role=(row.get("person_role") or "").strip() or None,
                    company_name=(row.get("company_name") or "").strip() or None,
                    country=(row.get("country") or "").strip() or None,
                    country_iso2=(row.get("country_iso2") or "").strip() or None,
                    source_platform=(row.get("source_platform") or "").strip() or None,
                    source_title=(row.get("source_title") or "").strip() or None,
                    source_snippet=(row.get("source_snippet") or "").strip() or None,
                    search_query_used=(row.get("search_query_used") or "").strip() or None,
                    signal_type=(row.get("signal_type") or "").strip() or None,
                    signal_text=(row.get("signal_text") or "").strip() or None,
                    is_company_post=_coerce_bool(row.get("is_company_post")),
                    is_personal_post=_coerce_bool(row.get("is_personal_post")),
                    is_official_delegation=_coerce_bool(row.get("is_official_delegation")),
                    is_exhibitor_employee=_coerce_bool(row.get("is_exhibitor_employee")),
                    notes=(row.get("notes") or "").strip() or None,
                )
                _, is_new = upsert_signal(raw)
                if is_new:
                    summary["inserted"] += 1
                else:
                    summary["updated"] += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("skipping row {}: {}", summary["read"], e)
                summary["skipped"] += 1
    return summary


def write_template(path: Path) -> Path:
    """Write a CSV template with the expected headers + 2 sample rows."""
    headers = CSV_REQUIRED + CSV_OPTIONAL
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        w.writerow({
            "edition_year": "2026", "entity_type": "person",
            "source_url": "https://example.com/post/123",
            "person_name": "Jane Smith", "person_role": "VP Sales",
            "company_name": "Sample Defense Ltd", "country": "United Kingdom",
            "country_iso2": "GB",
            "source_platform": "linkedin",
            "source_title": "Eurosatory 2026 — see you at booth K221",
            "source_snippet": "We will attend Eurosatory 2026, meet us at booth K221!",
            "search_query_used": '"Eurosatory 2026" "will attend"',
            "signal_type": "personal_linkedin_post",
            "signal_text": "We will attend Eurosatory 2026, meet us at booth K221!",
            "is_personal_post": "true",
        })
        w.writerow({
            "edition_year": "2024", "entity_type": "delegation",
            "source_url": "https://example-ministry.gov/news/eurosatory-2024",
            "company_name": "Ministry of Defence — UK delegation",
            "country": "United Kingdom", "country_iso2": "GB",
            "source_platform": "press",
            "source_title": "UK official delegation at Eurosatory 2024",
            "source_snippet": "An official UK delegation will attend Eurosatory 2024…",
            "search_query_used": '"Eurosatory 2024" "Ministry of Defence"',
            "signal_type": "official_delegation",
            "signal_text": "Official UK delegation visiting Eurosatory 2024.",
            "is_official_delegation": "true",
        })
    return path
