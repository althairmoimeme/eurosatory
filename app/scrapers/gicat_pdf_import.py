"""Import the parsed GICAT 2025 PDF directory into the database.

Two-target import :
1. **Companies** — match each GICAT company against the existing
   ``Exhibitor`` catalog by normalised name. Update website / phone /
   email / city / postal_code / address on matches ; create new
   ``Exhibitor`` rows for non-matched (these are GICAT-only French
   defense industry players).
2. **People** — every correspondent + executive becomes an
   ``AttendanceSignal`` row : ``entity_type=person``,
   ``signal_type=other`` (or ``personal_linkedin_post`` if we end up
   pasting LinkedIn URLs later), ``source_platform=gicat_directory``,
   ``source_url`` carries a stable ``gicat://<id>/<name>`` urn so the
   same person re-imported doesn't duplicate.

Always supports a ``dry_run=True`` mode so the operator sees the
counts before committing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import select


@dataclass
class ImportResult:
    companies_seen: int
    companies_matched: int
    companies_updated: int
    companies_created: int
    persons_seen: int
    persons_created: int
    persons_skipped: int

    def to_dict(self) -> dict:
        return self.__dict__


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _split_address(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return ``(address1, address2)`` from the joined address string."""
    if not raw:
        return None, None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return None, None
    return parts[0], (", ".join(parts[1:]) or None)


def _build_gicat_slug_to_id() -> dict[str, str]:
    """Fetch the GICAT online directory once to build a name-slug → id
    map so we can issue deep-link URLs in the imported signals.
    Returns ``{}`` on network failure (the import still works, just
    without deep-links).
    """
    import asyncio
    import re as _re
    try:
        from app.scrapers.gicat_online_enrich import _fetch_company_index
        from app.scrapers.http_client import HttpClient

        async def go():
            async with HttpClient(
                extra_headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://new-liste-exposants.hubj2c.com",
                    "Referer":
                        "https://new-liste-exposants.hubj2c.com/gicat/main/fr",
                    "X-Requested-With": "XMLHttpRequest",
                },
                per_host_delay=0.2, concurrency=2, timeout=20, max_retries=1,
            ) as c:
                return await _fetch_company_index(c)

        return {
            _re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-"): sid
            for sid, n in asyncio.run(go())
        }
    except Exception:  # noqa: BLE001
        return {}


_GICAT_DIRECTORY_URL = (
    "https://new-liste-exposants.hubj2c.com/gicat/main/fr"
)


def _gicat_url_for(company_name: str, slug_to_id: dict[str, str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
    sid = slug_to_id.get(slug)
    if not sid:
        for k, v in slug_to_id.items():
            if len(k) >= 4 and (k in slug or slug in k):
                sid = v
                break
    return (
        f"{_GICAT_DIRECTORY_URL}?openItem={sid}" if sid else _GICAT_DIRECTORY_URL
    )


def import_gicat_pdf(
    pdf_path: Path,
    *,
    dry_run: bool = True,
    create_new_companies: bool = True,
    create_signals: bool = True,
) -> ImportResult:
    """Run the full PDF import pipeline.

    ``dry_run=True`` (default) does NOT write to the DB — it just
    reports what *would* be done. Set ``dry_run=False`` to commit.
    """
    from app.attendance.dedup import (
        canonical_company_name as _norm_company,
        canonical_person_name,
    )
    from app.attendance.seed import RawSignal, upsert_signal
    from app.database import Exhibitor, SessionLocal
    from app.scrapers.gicat_pdf import parse_pdf

    parsed = parse_pdf(pdf_path)
    res = ImportResult(
        companies_seen=len(parsed),
        companies_matched=0, companies_updated=0, companies_created=0,
        persons_seen=sum(len(c.persons) for c in parsed),
        persons_created=0, persons_skipped=0,
    )

    # --- Phase 1 : companies -------------------------------------------------
    s = SessionLocal()
    try:
        catalog: dict[str, Exhibitor] = {}
        for ex in s.execute(select(Exhibitor)).scalars():
            catalog[_norm_name(ex.company_name)] = ex
        for c in parsed:
            key = _norm_name(c.name)
            ex = catalog.get(key)
            if ex is None:
                # Loose substring fallback for names that diverge
                for cat_name, cat_ex in catalog.items():
                    if len(cat_name) < 4 or len(key) < 4:
                        continue
                    if cat_name in key or key in cat_name:
                        ex = cat_ex
                        break

            if ex is not None:
                res.companies_matched += 1
                # Fill missing CRM fields from the GICAT data.
                changed = False
                for src, dst in (
                    (c.website, "website_url"),
                    (c.phone, "phone"),
                    (c.email, "contact_email"),
                    (c.city, "city"),
                    (c.postal_code, "zip_code"),
                ):
                    if src and not getattr(ex, dst, None):
                        if not dry_run:
                            setattr(ex, dst, src)
                        changed = True
                if c.address and not ex.address1:
                    a1, a2 = _split_address(c.address)
                    if a1:
                        if not dry_run:
                            ex.address1 = a1
                            if a2:
                                ex.address2 = a2
                        changed = True
                if changed:
                    res.companies_updated += 1
            elif create_new_companies:
                res.companies_created += 1
                if not dry_run:
                    a1, a2 = _split_address(c.address)
                    new_ex = Exhibitor(
                        finderr_guid=f"GICAT-{c.name[:50]}",
                        company_name=c.name,
                        country_iso2="FR",
                        country_name=c.country,
                        city=c.city,
                        zip_code=c.postal_code,
                        address1=a1,
                        address2=a2,
                        website_url=c.website,
                        contact_email=c.email,
                        phone=c.phone,
                        short_presentation=None,  # not in PDF
                        status="new",
                        is_favorite=False,
                    )
                    s.add(new_ex)
                    s.flush()
                    catalog[key] = new_ex  # so subsequent persons can resolve
        if not dry_run:
            s.commit()
    finally:
        s.close()

    # --- Phase 2 : persons → AttendanceSignals -------------------------------
    if create_signals:
        slug_to_id = _build_gicat_slug_to_id()
        for c in parsed:
            for p in c.persons:
                # Real, clickable deep-link to the GICAT directory page
                # for this company (so the operator can verify the
                # source). Falls back to the directory homepage if the
                # company isn't in the live index.
                source_url = _gicat_url_for(c.name, slug_to_id)
                if dry_run:
                    res.persons_created += 1
                    continue
                try:
                    raw = RawSignal(
                        edition_year=2026,
                        entity_type="person",
                        source_url=source_url,
                        person_name=p.name,
                        person_role=p.role,
                        company_name=c.name,
                        country=c.country,
                        country_iso2="FR",
                        source_platform="gicat_directory",
                        source_title=f"GICAT 2025 directory: {p.name}",
                        source_snippet=(
                            f"{p.name}"
                            + (f", {p.role}" if p.role else "")
                            + f" — {c.name}, GICAT member"
                        ),
                        search_query_used="gicat_pdf_import",
                        signal_type="company_announcement",
                        signal_text=(
                            f"GICAT directory entry for {c.name}. "
                            f"Person: {p.name}"
                            + (f", {p.role}" if p.role else "")
                            + (
                                ". Direct email known."
                                if p.email else ""
                            )
                        ),
                        is_company_post=False,
                        is_personal_post=False,
                        notes=(
                            f"Imported from GICAT 2025 PDF directory. "
                            + (
                                f"Direct email: {p.email}. "
                                if p.email else ""
                            )
                            + (f"Phone: {p.phone}. " if p.phone else "")
                            + ("Correspondent GICAT."
                               if p.is_correspondent else "Executive.")
                        ),
                    )
                    _, is_new = upsert_signal(raw)
                    if is_new:
                        res.persons_created += 1
                    else:
                        res.persons_skipped += 1
                except Exception as e:  # noqa: BLE001
                    res.persons_skipped += 1
                    logger.debug(f"upsert person {p.name} failed: {e!r}")

    return res
