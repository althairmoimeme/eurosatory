"""RocketReach API client — lookup persons (name + company) → LinkedIn URL,
confirmed title, employer domain, job history.

API
---
Base URL  : https://api.rocketreach.co/v2/api
Auth      : ``Api-Key: <YOUR_KEY>`` header

Endpoints used :
  - GET /account                — credit balance
  - GET /lookupProfile          — person lookup (async ; ~5-15s to complete)
      params : ``name`` + ``current_employer``  OR  ``linkedin_url``
  - GET /checkStatus            — poll lookup IDs until ``status="complete"``

Credit types (your plan) :
  - ``standard_lookup``  — returns name / LinkedIn / title / employer /
                          job history. **No email or phone.**
  - ``premium_lookup``  — adds work emails (paid tier).
  - ``phone_lookup``    — adds mobile phones (paid tier).

What we save back
-----------------
Per person we update the matching ``AttendanceSignal`` with :
  - ``derived_linkedin``  (via the source_url field on persons whose URL
    is already a LinkedIn /in/ link, or via the notes field)
  - confirmed ``person_role``
  - notes : ``RocketReach LinkedIn: ...``, current_employer_domain, etc.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.attendance.dedup import canonical_person_name
from app.config import settings
from app.scrapers.http_client import HttpClient


_BASE = "https://api.rocketreach.co/v2/api"


@dataclass
class RocketReachResult:
    candidates_sent: int = 0
    lookup_ids: list[int] = None
    contacts_returned: int = 0
    linkedin_added: int = 0
    role_added: int = 0
    notes_updated: int = 0
    errors: int = 0
    credits_remaining_after: Optional[int] = None

    def __post_init__(self) -> None:
        if self.lookup_ids is None:
            self.lookup_ids = []

    def to_dict(self) -> dict:
        return {
            "candidates_sent": self.candidates_sent,
            "contacts_returned": self.contacts_returned,
            "linkedin_added": self.linkedin_added,
            "role_added": self.role_added,
            "notes_updated": self.notes_updated,
            "errors": self.errors,
            "credits_remaining_after": self.credits_remaining_after,
            "lookup_ids": self.lookup_ids,
        }


# ---------------------------------------------------------------------------
# Low-level API calls
# ---------------------------------------------------------------------------


async def get_account(client, api_key: str) -> Optional[dict]:
    # Header is passed when the client is HttpClient ; ignored when the
    # client is _DirectClient (which already injects it from init).
    r = await client.request(
        "GET", f"{_BASE}/account",
        headers={"Api-Key": api_key},
    )
    if r.status_code != 200:
        logger.warning(
            f"RocketReach /account returned {r.status_code}: "
            f"{r.text[:200]}"
        )
        return None
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return None


async def lookup_profile(
    client: HttpClient, api_key: str, *,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    name: Optional[str] = None,
    current_employer: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Optional[dict]:
    """Submit a person lookup. Returns the immediate response (which has
    ``id`` + ``status="progress"``). Use ``check_status`` to poll until
    complete.

    Costs 1 ``standard_lookup`` credit when accepted.
    """
    params: dict[str, str] = {}
    if linkedin_url:
        params["linkedin_url"] = linkedin_url
    elif name:
        params["name"] = name
        if current_employer:
            params["current_employer"] = current_employer
    elif first_name and last_name:
        params["name"] = f"{first_name} {last_name}"
        if current_employer:
            params["current_employer"] = current_employer
    else:
        return None
    r = await client.request(
        "GET", f"{_BASE}/lookupProfile", params=params,
    )
    if r.status_code not in (200, 201, 202):
        logger.warning(
            f"RocketReach lookupProfile {r.status_code}: {r.text[:200]}"
        )
        return None
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return None


async def check_status(
    client: HttpClient, api_key: str, ids: list[int],
) -> list[dict]:
    """Poll the status of pending lookups. Returns a list of profile
    objects ; each has ``status`` ∈ {"progress", "complete", "failed"}.
    """
    if not ids:
        return []
    params = {"ids": ",".join(str(i) for i in ids)}
    r = await client.request(
        "GET", f"{_BASE}/checkStatus", params=params,
    )
    if r.status_code != 200:
        logger.warning(
            f"RocketReach checkStatus {r.status_code}: {r.text[:200]}"
        )
        return []
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, list):
        return data
    return []


# ---------------------------------------------------------------------------
# Candidate selection — high-value persons that don't yet have a LinkedIn URL
# ---------------------------------------------------------------------------


def _split_first_last(full_name: str) -> tuple[Optional[str], Optional[str]]:
    name = (full_name or "").strip()
    if not name:
        return None, None
    parts = name.split()
    if len(parts) == 1:
        return None, parts[0]
    upper_tokens = [p for p in parts if p.isupper() and len(p) >= 2]
    if upper_tokens:
        last = " ".join(upper_tokens)
        first = " ".join(p for p in parts if p not in upper_tokens) or None
        return first, last
    return parts[0], " ".join(parts[1:])


def _select_candidates(limit: int) -> list[dict]:
    """Pick high-value persons from the DB :
    1. has a real person_name + company_name
    2. role looks executive (CEO/CTO/President/Director/General Manager)
    3. notes don't already contain a LinkedIn URL
    4. source_url isn't already a LinkedIn /in/ link

    Returns dicts ready for ``lookup_profile``.
    """
    from app.database import AttendanceSignal, SessionLocal
    from sqlalchemy import select

    s = SessionLocal()
    try:
        rows = s.execute(
            select(AttendanceSignal).where(
                AttendanceSignal.entity_type == "person",
                AttendanceSignal.person_name.is_not(None),
                AttendanceSignal.company_name.is_not(None),
                AttendanceSignal.person_role.is_not(None),
                ~AttendanceSignal.notes.like("%linkedin.com/in/%"),
                ~AttendanceSignal.notes.like("%RocketReach%"),
                ~AttendanceSignal.source_url.like("%linkedin.com/in/%"),
            )
        ).scalars().all()
    finally:
        s.close()

    # Rank by role priority — executive titles first.
    _ROLE_RANK = re.compile(
        r"(CEO|PDG|Pr[ée]sident|Founder|Fondateur|Chairman|CTO|COO|"
        r"CFO|Vice\s*Pr[ée]sident|VP|Director|Directeur|Managing\s+"
        r"Director|General\s+Manager|Directeur\s+G[ée]n[ée]ral)",
        re.IGNORECASE,
    )
    ranked = []
    for sig in rows:
        score = 1 if _ROLE_RANK.search(sig.person_role or "") else 0
        ranked.append((score, sig))
    ranked.sort(key=lambda x: -x[0])

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for _, sig in ranked:
        if len(out) >= limit:
            break
        first, last = _split_first_last(sig.person_name or "")
        if not last:
            continue
        canon_p = canonical_person_name(sig.person_name or "") or ""
        canon_c = (sig.company_name or "").lower().strip()
        key = (canon_p, canon_c)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "signal_id": sig.id,
            "first_name": first or "",
            "last_name": last,
            "name": sig.person_name,
            "company": sig.company_name,
            "role": sig.person_role,
        })
    return out


# ---------------------------------------------------------------------------
# Main flow — submit, poll, persist
# ---------------------------------------------------------------------------


def _persist_results(
    profiles: list[dict], signal_id_by_name: dict[str, int],
) -> tuple[int, int, int]:
    """For each completed profile, write LinkedIn URL + confirmed title
    back to the matching AttendanceSignal's notes.

    Returns ``(linkedin_added, role_added, notes_updated)``.
    """
    from app.database import AttendanceSignal, SessionLocal
    li_added = 0
    role_added = 0
    notes_updated = 0
    s = SessionLocal()
    try:
        for p in profiles:
            if (p.get("status") or "").lower() != "complete":
                continue
            name = p.get("name") or ""
            sig_id = signal_id_by_name.get(name.lower())
            if sig_id is None:
                continue
            sig = s.get(AttendanceSignal, sig_id)
            if sig is None:
                continue
            li = (
                p.get("linkedin_url")
                or (p.get("links") or {}).get("linkedin")
            )
            title = p.get("current_title")
            employer_domain = p.get("current_employer_domain")
            new_bits: list[str] = []
            if li and "linkedin.com" in li.lower():
                if "linkedin.com/in/" not in (sig.notes or ""):
                    new_bits.append(f"RocketReach LinkedIn: {li}")
                    li_added += 1
            if title and not (sig.person_role or "").strip().lower().startswith(title.lower()):
                # Don't overwrite the role we have — record the
                # confirmation as an enrichment note.
                new_bits.append(f"RocketReach title (confirmed): {title}")
                role_added += 1
            if employer_domain:
                new_bits.append(
                    f"RocketReach employer domain: {employer_domain}"
                )
            if not new_bits:
                continue
            existing = (sig.notes or "").rstrip()
            sep = "\n" if existing else ""
            sig.notes = f"{existing}{sep}" + ". ".join(new_bits) + "."
            notes_updated += 1
        s.commit()
    finally:
        s.close()
    return li_added, role_added, notes_updated


class _DirectClient:
    """Tiny httpx wrapper that respects 429 Retry-After.

    The shared ``HttpClient`` wraps every call in a tenacity retry loop
    that converts 429 into a ``TimeoutException`` — too aggressive for
    RocketReach's strict free-tier rate limit (~5-10 req/min). We talk
    to the API directly and back off on 429 manually.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        import httpx as _httpx
        self._cli = _httpx.AsyncClient(
            timeout=20, headers={"Api-Key": api_key},
            follow_redirects=True,
        )

    async def __aenter__(self) -> "_DirectClient":
        return self

    async def __aexit__(self, *a) -> None:
        await self._cli.aclose()

    async def request(
        self, method: str, url: str, **kw,
    ):
        # Merge per-call headers with our defaults.
        merged_headers = dict(self._cli.headers)
        merged_headers.update(kw.pop("headers", {}) or {})
        for attempt in range(5):
            r = await self._cli.request(
                method, url, headers=merged_headers, **kw,
            )
            if r.status_code != 429:
                return r
            wait = float(r.headers.get("retry-after") or (4 + 2 ** attempt))
            wait = min(wait, 60)
            await asyncio.sleep(wait)
        return r


async def _run_enrich(
    api_key: str, limit: int = 4,
    poll_interval: float = 6.0, poll_timeout: float = 120.0,
    progress_cb=None,
) -> RocketReachResult:
    res = RocketReachResult()
    candidates = _select_candidates(limit)
    res.candidates_sent = len(candidates)
    if not candidates:
        return res

    async with _DirectClient(api_key) as c:
        # Credit check.
        acct = await get_account(c, api_key)
        if acct and progress_cb:
            std = next(
                (
                    cu for cu in (acct.get("credit_usage") or [])
                    if cu.get("credit_type") == "standard_lookup"
                ),
                None,
            )
            if std:
                progress_cb(
                    f"  RocketReach standard_lookup credits remaining: "
                    f"{std.get('remaining')}"
                )
        # Submit lookups.
        signal_id_by_name: dict[str, int] = {}
        lookup_ids: list[int] = []
        for cand in candidates:
            payload = await lookup_profile(
                c, api_key,
                name=cand["name"], current_employer=cand["company"],
            )
            if payload is None:
                res.errors += 1
                continue
            lookup_id = payload.get("id")
            returned_name = (payload.get("name") or cand["name"]).lower()
            if lookup_id is not None:
                lookup_ids.append(int(lookup_id))
                signal_id_by_name[returned_name] = cand["signal_id"]
                if progress_cb:
                    progress_cb(
                        f"  Submitted: {cand['name']} @ {cand['company']} "
                        f"→ id={lookup_id}"
                    )
        res.lookup_ids = lookup_ids
        if not lookup_ids:
            return res

        # Poll until all are complete (or timeout).
        import time
        t0 = time.monotonic()
        completed: dict[int, dict] = {}
        pending = set(lookup_ids)
        while pending and (time.monotonic() - t0) < poll_timeout:
            await asyncio.sleep(poll_interval)
            profiles = await check_status(c, api_key, list(pending))
            for p in profiles:
                pid = p.get("id")
                status = (p.get("status") or "").lower()
                if status == "complete":
                    completed[int(pid)] = p
                    pending.discard(int(pid))
                elif status == "failed":
                    pending.discard(int(pid))
                    res.errors += 1
            if progress_cb:
                progress_cb(
                    f"  poll t+{time.monotonic() - t0:.0f}s: "
                    f"complete={len(completed)} pending={len(pending)}"
                )
        res.contacts_returned = len(completed)

        # Persist — write LinkedIn / title back to signal notes.
        li, ro, nu = _persist_results(
            list(completed.values()), signal_id_by_name,
        )
        res.linkedin_added = li
        res.role_added = ro
        res.notes_updated = nu

        # Credit balance after.
        acct_after = await get_account(c, api_key)
        if acct_after:
            std = next(
                (
                    cu for cu in (acct_after.get("credit_usage") or [])
                    if cu.get("credit_type") == "standard_lookup"
                ),
                None,
            )
            if std:
                res.credits_remaining_after = int(std.get("remaining") or 0)
    return res


def enrich_via_rocketreach(
    limit: int = 4, api_key: Optional[str] = None, progress_cb=None,
) -> RocketReachResult:
    """Sync entrypoint."""
    key = api_key or settings.rocketreach_api_key
    if not key:
        raise RuntimeError(
            "ROCKETREACH_API_KEY not configured. Set in .env or pass "
            "api_key explicitly."
        )
    return asyncio.run(
        _run_enrich(api_key=key, limit=limit, progress_cb=progress_cb)
    )
