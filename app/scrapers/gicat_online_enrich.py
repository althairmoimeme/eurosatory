"""Enrich the catalog with the GICAT online directory detail pages.

The hubj2c SPA loads a per-company detail HTML when the user clicks a
card. The endpoint :

    POST https://new-api.hubj2c.com/form/gicat/main/getView/<idSociete>/-/fr/0

returns ~10-15 kB of HTML containing :
- official email (``mailto:...``)
- website URL
- **LinkedIn company URL** (``<a class="fe-social" href="https://www.linkedin.com/company/...">``)
- Twitter / X / YouTube social links (when set)
- Full address (street + postal + city)
- Executives list with names + roles (and sometimes per-person LinkedIn)
- Products (titles + descriptions)

Previously we only had the basic getSettings dump (no LinkedIn, no
detailed contacts). This module fetches the detail per company in
parallel, parses the HTML, and writes the enrichment back to the
``Exhibitor`` rows + creates fresh ``AttendanceSignal`` entries for
the dirigeants discovered (with their LinkedIn URLs when available).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger
from selectolax.parser import HTMLParser
from sqlalchemy import select

from app.scrapers.http_client import HttpClient


GICAT_GETSETTINGS_URL = (
    "https://new-liste-exposants.hubj2c.com/gicat/main/fr/getSettings"
)
GICAT_GETVIEW_URL = (
    "https://new-api.hubj2c.com/form/gicat/main/getView/{id}/-/fr/0"
)


@dataclass
class GicatPersonOnline:
    name: str
    role: Optional[str] = None
    linkedin_url: Optional[str] = None


@dataclass
class GicatDetail:
    id_societe: str
    name: str
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    youtube_url: Optional[str] = None
    facebook_url: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: str = "France"
    persons: list[GicatPersonOnline] = field(default_factory=list)
    products: list[str] = field(default_factory=list)


_PHONE_RX = re.compile(r"\+\d[\d\s().-]{8,}\d")
_POSTAL_FR_RX = re.compile(r"\b(\d{5})\s+([A-ZÉÈÊÀÂÔÛÇ][A-ZÉÈÊÀÂÔÛÇ\s\-]+)")


# ---------------------------------------------------------------------------
# Step 1 : list all companies (id + name) from the cached getSettings.
# ---------------------------------------------------------------------------


async def _fetch_company_index(client: HttpClient) -> list[tuple[str, str]]:
    """Return a list of ``(idSociete, company_name)`` tuples (~512 rows)."""
    r = await client.request("POST", GICAT_GETSETTINGS_URL)
    if r.status_code != 200:
        return []
    try:
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        logger.error(f"GICAT getSettings JSON parse failed: {e!r}")
        return []
    out: list[tuple[str, str]] = []
    for row in payload.get("list") or []:
        sid = str(row.get("idSociete") or "")
        name = (row.get("exposant") or "").strip()
        if sid and name:
            out.append((sid, name))
    return out


# ---------------------------------------------------------------------------
# Step 2 : per-company detail fetch + HTML parse.
# ---------------------------------------------------------------------------


def _parse_detail(id_societe: str, name: str, html: str) -> GicatDetail:
    tree = HTMLParser(html)
    detail = GicatDetail(id_societe=id_societe, name=name)

    # All anchors carry the contact info.
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if not href:
            continue
        href_lower = href.lower()
        if href_lower.startswith("mailto:") and not detail.email:
            detail.email = href[7:].strip()
        elif href_lower.startswith("tel:") and not detail.phone:
            detail.phone = href[4:].strip()
        elif "linkedin.com/" in href_lower and not detail.linkedin_url:
            detail.linkedin_url = href
        elif (
            ("twitter.com/" in href_lower or "://x.com/" in href_lower)
            and not detail.twitter_url
        ):
            detail.twitter_url = href
        elif "youtube.com/" in href_lower and not detail.youtube_url:
            detail.youtube_url = href
        elif "facebook.com/" in href_lower and not detail.facebook_url:
            detail.facebook_url = href
        elif (
            href_lower.startswith(("http://", "https://"))
            and not detail.website
            and not any(
                domain in href_lower for domain in (
                    "linkedin.com", "twitter.com", "x.com",
                    "youtube.com", "facebook.com", "hubj2c.com",
                )
            )
        ):
            detail.website = href

    # Address — usually inside a card with title "Adresse" or similar. The
    # full text of the page contains the postal code easily.
    page_text = re.sub(r"\s+", " ", tree.text(separator=" ") or "")
    pm = _POSTAL_FR_RX.search(page_text)
    if pm:
        detail.postal_code = pm.group(1)
        detail.city = pm.group(2).strip().title()

    # Phone — fallback if no tel: anchor.
    if not detail.phone:
        for ph in _PHONE_RX.finditer(page_text):
            detail.phone = ph.group(0).strip()
            break

    # Address block — pick the line right before the postal code.
    if pm:
        idx = page_text.find(pm.group(0))
        if idx > 0:
            chunk = page_text[max(0, idx - 200): idx].strip()
            # Keep last line-ish (split by 2+ spaces) as the street.
            parts = re.split(r"\s{2,}", chunk)
            if parts:
                detail.address = parts[-1].strip()

    # Persons — parse EVERY .fe-bloc-contacts block (the page typically
    # has two : "CORRESPONDANT GICAT" + "PRINCIPAUX DIRIGEANTS"). Strip
    # honorifics (Mme / M. / Dr.) so the name matches the canonical form
    # we already have in DB.
    seen_names: set[str] = set()
    for block in tree.css(".fe-bloc-contacts"):
        for entry in block.css("div.fe-contact-infos"):
            name_el = entry.css_first(".fe-gras")
            if not name_el:
                continue
            raw_name = re.sub(
                r"\s+", " ", (name_el.text() or "")
            ).strip(" .,;:\xa0")
            # Strip leading civility prefixes that hubj2c sometimes
            # injects (Mme / M. / Mr. / Mrs. / Dr.).
            person_name = re.sub(
                r"^(?:Mme|M\.|M|Mr\.?|Mrs\.?|Dr\.?|Pr\.?)\s+",
                "",
                raw_name,
            ).strip()
            if not person_name or person_name in seen_names:
                continue
            seen_names.add(person_name)
            role: Optional[str] = None
            for sib in entry.css("div"):
                txt = re.sub(r"\s+", " ", (sib.text() or "")).strip()
                if not txt or person_name in txt or raw_name in txt:
                    continue
                # Skip lines that look like an email or phone number.
                if "@" in txt or _PHONE_RX.search(txt):
                    continue
                role = txt[:120]
                break
            li_url = None
            for a in entry.css("a"):
                href = a.attributes.get("href") or ""
                if "linkedin.com/" in href.lower():
                    li_url = href
                    break
            detail.persons.append(
                GicatPersonOnline(name=person_name, role=role,
                                    linkedin_url=li_url)
            )

    # Products — the page's right column.
    for p in tree.css(".fe-titre-produit"):
        title = (p.text() or "").strip()
        if title:
            detail.products.append(title)

    return detail


async def _fetch_one_detail(
    client: HttpClient, id_societe: str, name: str,
) -> Optional[GicatDetail]:
    url = GICAT_GETVIEW_URL.format(id=id_societe)
    try:
        r = await client.request("POST", url)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"GICAT getView {id_societe} failed: {e!r}")
        return None
    if r.status_code != 200 or not r.text:
        return None
    try:
        return _parse_detail(id_societe, name, r.text)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"GICAT parse {id_societe} failed: {e!r}")
        return None


async def _crawl_async(
    limit: Optional[int] = None,
    progress_cb=None,
) -> list[GicatDetail]:
    headers = {
        "Accept": "text/html, */*; q=0.01",
        "Origin": "https://new-liste-exposants.hubj2c.com",
        "Referer": "https://new-liste-exposants.hubj2c.com/",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with HttpClient(
        extra_headers=headers, per_host_delay=0.05, concurrency=12,
        timeout=15, max_retries=1,
    ) as c:
        index = await _fetch_company_index(c)
        if limit:
            index = index[:limit]
        total = len(index)
        results: list[GicatDetail] = []
        scanned = 0

        async def _one(sid: str, nm: str) -> None:
            nonlocal scanned
            d = await _fetch_one_detail(c, sid, nm)
            scanned += 1
            if d is not None:
                results.append(d)
            if progress_cb:
                progress_cb(scanned, total, len(results))

        # Batch in groups of 32 to avoid spawning 512 tasks at once.
        batch = 32
        for i in range(0, len(index), batch):
            await asyncio.gather(
                *[_one(sid, nm) for sid, nm in index[i: i + batch]]
            )
        return results


def crawl_gicat_details(
    limit: Optional[int] = None, progress_cb=None,
) -> list[GicatDetail]:
    """Sync entrypoint — fetches all (or ``limit``) GICAT detail pages."""
    return asyncio.run(_crawl_async(limit=limit, progress_cb=progress_cb))


# ---------------------------------------------------------------------------
# Step 3 : write enrichment back to Exhibitor + AttendanceSignal.
# ---------------------------------------------------------------------------


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def write_enrichment(
    details: list[GicatDetail], *, dry_run: bool = True,
) -> dict:
    """Update existing Exhibitor rows with the online-found fields, and
    upsert AttendanceSignal rows for any new dirigeants (preferring
    LinkedIn URL as ``source_url`` when available).
    """
    from app.attendance.seed import RawSignal, upsert_signal
    from app.database import Exhibitor, SessionLocal

    res = {
        "details": len(details),
        "exhibitors_updated": 0,
        "fields_set": 0,
        "linkedin_added": 0,
        "signals_new": 0,
        "signals_skipped": 0,
    }

    s = SessionLocal()
    try:
        catalog: dict[str, Exhibitor] = {}
        for ex in s.execute(select(Exhibitor)).scalars():
            catalog[_norm_name(ex.company_name)] = ex

        for d in details:
            ex = catalog.get(_norm_name(d.name))
            if ex is None:
                # Substring fallback
                for k, e in catalog.items():
                    if len(k) >= 4 and (k in _norm_name(d.name)
                                          or _norm_name(d.name) in k):
                        ex = e
                        break
            if ex is None:
                continue

            updated = False
            for src, dst, was_set_attr in (
                (d.website, "website_url", "website_url"),
                (d.email, "contact_email", "contact_email"),
                (d.phone, "phone", "phone"),
                (d.city, "city", "city"),
                (d.postal_code, "zip_code", "zip_code"),
                (d.linkedin_url, "linkedin_url", "linkedin_url"),
                (d.twitter_url, "twitter_url", "twitter_url"),
                (d.youtube_url, "youtube_url", "youtube_url"),
                (d.facebook_url, "facebook_url", "facebook_url"),
            ):
                if src and not getattr(ex, was_set_attr, None):
                    if not dry_run:
                        setattr(ex, dst, src)
                    res["fields_set"] += 1
                    if dst == "linkedin_url":
                        res["linkedin_added"] += 1
                    updated = True
            if updated:
                res["exhibitors_updated"] += 1

        if not dry_run:
            s.commit()
    finally:
        s.close()

    # Upsert dirigeants signals — using LinkedIn URL as source_url when
    # we have it (so the URL is clickable in the page), otherwise a
    # stable URN per (company, person).
    for d in details:
        for p in d.persons:
            if dry_run:
                res["signals_new"] += 1
                continue
            try:
                # Prefer the person's LinkedIn URL when available — that's
                # the most useful "source" link. Otherwise, deep-link to
                # the GICAT online directory at this exact company so the
                # operator can verify the entry.
                source_url = (
                    p.linkedin_url
                    if p.linkedin_url else
                    f"https://new-liste-exposants.hubj2c.com/gicat/main/fr"
                    f"?openItem={d.id_societe}"
                )
                raw = RawSignal(
                    edition_year=2026,
                    entity_type="person",
                    source_url=source_url,
                    person_name=p.name,
                    person_role=p.role,
                    company_name=d.name,
                    country=d.country,
                    country_iso2="FR",
                    source_platform=(
                        "linkedin" if p.linkedin_url else "gicat_directory"
                    ),
                    source_title=f"GICAT online directory: {p.name}",
                    source_snippet=(
                        f"{p.name}"
                        + (f", {p.role}" if p.role else "")
                        + f" — {d.name}, GICAT member"
                    ),
                    search_query_used="gicat_online_enrich",
                    signal_type=(
                        "personal_linkedin_post" if p.linkedin_url
                        else "company_announcement"
                    ),
                    signal_text=(
                        f"GICAT online directory entry for {d.name}. "
                        f"Person: {p.name}"
                        + (f", {p.role}" if p.role else "")
                        + (f". LinkedIn: {p.linkedin_url}"
                           if p.linkedin_url else "")
                    ),
                    is_company_post=False,
                    is_personal_post=bool(p.linkedin_url),
                    notes=(
                        f"From GICAT online directory (idSociete="
                        f"{d.id_societe}). "
                        + (f"LinkedIn: {p.linkedin_url}. "
                           if p.linkedin_url else "")
                    ),
                )
                _, is_new = upsert_signal(raw)
                if is_new:
                    res["signals_new"] += 1
                else:
                    res["signals_skipped"] += 1
            except Exception as e:  # noqa: BLE001
                res["signals_skipped"] += 1
                logger.debug(f"upsert {p.name} failed: {e!r}")

    return res
