"""FullEnrich.com API client — find emails + phones for our contacts.

API
---
- Bulk enrichment   : POST  https://app.fullenrich.com/api/v2/contact/enrich/bulk
- Get results       : GET   https://app.fullenrich.com/api/v2/contact/enrich/bulk/{id}
- Account credits   : GET   https://app.fullenrich.com/api/v2/account/credits

Auth: ``Authorization: Bearer <API_KEY>``.

Pricing (credits):
- Work email     : 1
- Personal email : 3
- Mobile phone   : 10

The bulk endpoint is async — you POST a batch, get an ``enrichment_id``,
then poll the GET endpoint until ``status == "FINISHED"``.

Workflow used here :

1. Build a batch of contacts (max 100 per call to stay polite) from
   ``AttendanceSignal`` rows missing email.
2. POST → get ``enrichment_id``.
3. Poll the GET endpoint every 8 s until FINISHED.
4. Iterate the returned contacts ; for each one, write
   ``most_probable_work_email`` / ``most_probable_phone`` back into the
   matching signal's ``notes`` field (so the existing
   ``derived_email`` / ``derived_phone`` regex pickup surfaces them in
   the UI).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.attendance.dedup import canonical_company_name, canonical_person_name
from app.config import settings
from app.scrapers.http_client import HttpClient


_BASE = "https://app.fullenrich.com/api/v2"


@dataclass
class FullEnrichResult:
    candidates_sent: int = 0
    enrichment_id: Optional[str] = None
    finished_in_seconds: float = 0.0
    contacts_returned: int = 0
    emails_found: int = 0
    phones_found: int = 0
    signals_updated: int = 0
    credits_used: int = 0

    def to_dict(self) -> dict:
        return self.__dict__


def _split_first_last(full_name: str) -> tuple[Optional[str], Optional[str]]:
    """Best-effort split — most names in our DB are "Firstname LASTNAME"."""
    name = (full_name or "").strip()
    if not name:
        return None, None
    parts = name.split()
    if len(parts) == 1:
        return None, parts[0]
    # Last name is typically the all-caps token (or the LAST chunk).
    upper_tokens = [p for p in parts if p.isupper() and len(p) >= 2]
    if upper_tokens:
        last = " ".join(upper_tokens)
        first = " ".join(p for p in parts if p not in upper_tokens) or None
        return first, last
    return parts[0], " ".join(parts[1:])


_SOCIAL_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "tiktok.com",
    "telegram.org", "wa.me", "whatsapp.com",
}


def _domain_from_url(url: Optional[str]) -> Optional[str]:
    """Return the bare host of ``url`` (without ``www.``).

    Tolerates schemeless URLs (``www.example.com``, ``example.com/path``)
    by prepending ``https://`` before parsing — many of the catalog
    websites are stored without a scheme. Returns None for social-media
    domains.
    """
    if not url:
        return None
    from urllib.parse import urlsplit
    s = url.strip()
    if not s.lower().startswith(("http://", "https://")):
        s = "https://" + s
    host = urlsplit(s).netloc.lower().removeprefix("www.")
    if not host:
        return None
    parts = host.split(".")
    if len(parts) >= 2:
        registered = ".".join(parts[-2:])
        if registered in _SOCIAL_DOMAINS:
            return None
    return host


# ---------------------------------------------------------------------------
# Low-level API calls
# ---------------------------------------------------------------------------


async def _check_credits(client: HttpClient, api_key: str) -> Optional[int]:
    headers = {"Authorization": f"Bearer {api_key}"}
    r = await client.request(
        "GET", f"{_BASE}/account/credits", headers=headers,
    )
    if r.status_code != 200:
        logger.warning(
            f"FullEnrich credits check returned {r.status_code}: "
            f"{r.text[:200]}"
        )
        return None
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return None
    # The API may return either a flat int or {"credits": N}.
    if isinstance(data, dict):
        return int(data.get("credits", data.get("balance", 0)) or 0)
    if isinstance(data, int):
        return int(data)
    return None


async def _post_bulk_enrich(
    client: HttpClient, api_key: str, batch_name: str,
    contacts: list[dict],
) -> Optional[str]:
    """Submit a bulk enrichment job. Returns the ``enrichment_id``."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "name": batch_name,
        "data": contacts,
    }
    r = await client.request(
        "POST", f"{_BASE}/contact/enrich/bulk",
        headers=headers, content=json.dumps(payload),
    )
    if r.status_code not in (200, 201, 202):
        logger.error(
            f"FullEnrich bulk POST {r.status_code}: {r.text[:400]}"
        )
        return None
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return None
    return data.get("enrichment_id") or data.get("id")


async def _get_bulk_result(
    client: HttpClient, api_key: str, enrichment_id: str,
) -> Optional[dict]:
    headers = {"Authorization": f"Bearer {api_key}"}
    r = await client.request(
        "GET", f"{_BASE}/contact/enrich/bulk/{enrichment_id}",
        headers=headers,
    )
    if r.status_code != 200:
        logger.warning(
            f"FullEnrich GET {r.status_code}: {r.text[:200]}"
        )
        return None
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Main flow — pick candidates, submit, poll, write back
# ---------------------------------------------------------------------------


def _select_candidates(
    limit: int, target_signal_ids: Optional[list[int]] = None,
) -> list[dict]:
    """Build the list of contacts to enrich from the DB.

    Each entry includes a stable ``signal_id`` in ``custom`` so we can
    match the response back to the source signal.
    """
    from app.database import (
        AttendanceSignal, Exhibitor, SessionLocal,
    )
    from sqlalchemy import select

    out: list[dict] = []
    s = SessionLocal()
    try:
        # Build catalog : canonical_company_name → website domain.
        # SKIP entries whose canonical name is empty / None ; otherwise
        # they all collide on the same key and shadow each other.
        catalog: dict[str, str] = {}
        for cn, web in s.execute(
            select(Exhibitor.company_name, Exhibitor.website_url)
        ).all():
            if not cn:
                continue
            canon = canonical_company_name(cn)
            if not canon:
                continue
            domain = _domain_from_url(web)
            if domain:
                catalog[canon] = domain

        q = select(AttendanceSignal).where(
            AttendanceSignal.entity_type == "person",
            AttendanceSignal.person_name.is_not(None),
            # Need a real company too — without one, FullEnrich can't
            # narrow down the email/phone search.
            AttendanceSignal.company_name.is_not(None),
        )
        if target_signal_ids:
            q = q.where(AttendanceSignal.id.in_(target_signal_ids))
        else:
            # Skip rows that already have an email in notes OR have
            # already been tried (and failed) by FullEnrich.
            q = q.where(
                ~AttendanceSignal.notes.like("%@%"),
                ~AttendanceSignal.notes.like(
                    f"%{_FULLENRICH_TRIED_MARKER}%"
                ),
            )
        rows = s.execute(q).scalars().all()

        # Dedup by (canonical_person, canonical_company) so we don't pay
        # twice for the same person at the same company.
        seen_dedup: set[tuple[str, str]] = set()
        for sig in rows:
            if len(out) >= limit:
                break
            first, last = _split_first_last(sig.person_name or "")
            if not last:
                continue
            company = (sig.company_name or "").strip() or None
            if not company:
                continue
            canon = canonical_company_name(company) or ""
            canon_person = canonical_person_name(sig.person_name or "") or ""
            if not canon or not canon_person:
                continue
            dedup_key = (canon_person, canon)
            if dedup_key in seen_dedup:
                continue
            seen_dedup.add(dedup_key)
            domain = catalog.get(canon)
            # Request emails only by default (1 credit each).  Mobile
            # phones are 10 credits each — too expensive for a default.
            # The CLI flag ``--phones`` opts in.
            entry: dict = {
                "first_name": first or "",
                "last_name": last,
                "enrich_fields": ["contact.work_emails"],
                "custom": {"signal_id": str(sig.id)},
            }
            if domain:
                entry["domain"] = domain
            else:
                entry["company_name"] = company
            # If we already have a LinkedIn URL on the signal, send it
            # (much higher accuracy on FullEnrich's waterfall).
            if sig.source_url and "linkedin.com/in/" in sig.source_url.lower():
                entry["linkedin_url"] = sig.source_url
            out.append(entry)
    finally:
        s.close()
    return out


_FULLENRICH_TRIED_MARKER = "[FullEnrich:tried]"


def _apply_results_to_signals(payload: dict) -> tuple[int, int, int]:
    """Walk the bulk-result payload and write emails / phones to the
    matching ``AttendanceSignal.notes`` so the UI surfaces them. Also
    stamps a ``[FullEnrich:tried]`` marker on rows that came back empty,
    so subsequent runs skip them and avoid wasting credits.

    Returns ``(emails_found, phones_found, signals_updated)``.
    """
    from app.database import AttendanceSignal, SessionLocal
    contacts = payload.get("data") or []
    emails_found = 0
    phones_found = 0
    updated = 0
    s = SessionLocal()
    try:
        for c in contacts:
            custom = c.get("custom") or {}
            sig_id = custom.get("signal_id")
            if not sig_id:
                continue
            try:
                sid = int(sig_id)
            except (ValueError, TypeError):
                continue
            sig = s.get(AttendanceSignal, sid)
            if sig is None:
                continue
            info = c.get("contact_info") or {}
            email_obj = info.get("most_probable_work_email") or {}
            email = email_obj.get("email")
            phone_obj = info.get("most_probable_phone") or {}
            phone = phone_obj.get("number") if phone_obj else None
            if not email and (info.get("work_emails") or []):
                email = (info["work_emails"][0] or {}).get("email")
            if not phone and (info.get("phones") or []):
                phone = (info["phones"][0] or {}).get("number")

            new_bits: list[str] = []
            if email:
                emails_found += 1
                new_bits.append(f"FullEnrich email: {email}")
            if phone:
                phones_found += 1
                new_bits.append(f"FullEnrich phone: {phone}")

            existing = (sig.notes or "").rstrip()
            if not new_bits:
                # Stamp a "tried" marker so we never retry this contact
                # (saves credits on next runs). Skip if already stamped.
                if _FULLENRICH_TRIED_MARKER in existing:
                    continue
                sep = "\n" if existing else ""
                sig.notes = f"{existing}{sep}{_FULLENRICH_TRIED_MARKER}"
                continue
            sep = "\n" if existing else ""
            sig.notes = f"{existing}{sep}" + ". ".join(new_bits) + "."
            updated += 1
        s.commit()
    finally:
        s.close()
    return emails_found, phones_found, updated


async def _run_enrich(
    api_key: str,
    limit: int = 50,
    poll_interval: float = 8.0,
    poll_timeout: float = 600.0,
    target_signal_ids: Optional[list[int]] = None,
    progress_cb=None,
) -> FullEnrichResult:
    res = FullEnrichResult()
    candidates = _select_candidates(limit, target_signal_ids)
    res.candidates_sent = len(candidates)
    if not candidates:
        return res

    headers = {"Accept": "application/json"}
    async with HttpClient(
        extra_headers=headers, per_host_delay=0.2, concurrency=2,
        timeout=30, max_retries=2,
    ) as c:
        # Optional credit check.
        credits = await _check_credits(c, api_key)
        if credits is not None and progress_cb:
            progress_cb(f"FullEnrich credits available: {credits}")

        from datetime import datetime as _dt
        batch_name = f"Eurosatory contacts {_dt.utcnow():%Y-%m-%d %H:%M}"
        enrichment_id = await _post_bulk_enrich(
            c, api_key, batch_name, candidates,
        )
        if not enrichment_id:
            return res
        res.enrichment_id = enrichment_id
        if progress_cb:
            progress_cb(f"Job submitted: id={enrichment_id}")

        # Poll until finished.
        import time
        t0 = time.monotonic()
        last_status = None
        while time.monotonic() - t0 < poll_timeout:
            await asyncio.sleep(poll_interval)
            payload = await _get_bulk_result(c, api_key, enrichment_id)
            if payload is None:
                continue
            status = payload.get("status")
            if status != last_status:
                if progress_cb:
                    progress_cb(
                        f"  status={status} after "
                        f"{time.monotonic() - t0:.0f}s"
                    )
                last_status = status
            if status == "FINISHED":
                res.finished_in_seconds = time.monotonic() - t0
                res.contacts_returned = len(payload.get("data") or [])
                cost = (payload.get("cost") or {}).get("credits")
                res.credits_used = int(cost or 0)
                em, ph, up = _apply_results_to_signals(payload)
                res.emails_found = em
                res.phones_found = ph
                res.signals_updated = up
                return res
            if status in ("CANCELED", "RATE_LIMIT", "CREDITS_INSUFFICIENT"):
                if progress_cb:
                    progress_cb(f"Job ended with status={status}; aborting")
                return res
        # Timeout
        logger.warning("FullEnrich poll timeout — job still running.")
    return res


def enrich_signals_via_fullenrich(
    limit: int = 50,
    api_key: Optional[str] = None,
    target_signal_ids: Optional[list[int]] = None,
    progress_cb=None,
) -> FullEnrichResult:
    """Sync entrypoint."""
    key = api_key or settings.fullenrich_api_key
    if not key:
        raise RuntimeError(
            "FULLENRICH_API_KEY not configured. Set it in .env or pass "
            "api_key explicitly."
        )
    return asyncio.run(
        _run_enrich(
            api_key=key, limit=limit,
            target_signal_ids=target_signal_ids,
            progress_cb=progress_cb,
        )
    )
