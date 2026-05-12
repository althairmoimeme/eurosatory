"""Enrich attendance signals missing an email via Enrich.so v2 API.

Endpoint
--------
GET https://api.enrich.so/v2/api/linkedin-to-email?linkedin_profile=<url>

  - 200 + ``{"email": "...", "found": true}`` → success, 1 credit burned.
  - 200 + ``{"found": false}``                → no match, 0 credits.
  - 202 + ``{"status": "in_progress"}``        → retry after a few seconds.
  - 401 / 4xx                                  → fatal (key invalid, etc.)
  - 429                                         → rate limited, back off.

Selection
---------
We pick every ``buyer-prospection`` / ``sales-prospection`` signal whose
``notes`` does not already carry a ``Direct email:`` clause and whose
``source_url`` points to a LinkedIn profile URL (always true for the
Lemlist import).

Persistence
-----------
On success we append ``Direct email: <addr>.`` to the signal's notes,
so the existing UI extraction surfaces the address as ``derived_email``
without any other change.

Run
---
    python -m scripts.enrich_missing_emails             # default : all
    python -m scripts.enrich_missing_emails --limit 200 # smoke test
    python -m scripts.enrich_missing_emails --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.database.models import AttendanceSignal  # noqa: E402

BASE_URL = "https://api.enrich.so/v2/api/linkedin-to-email"
_email_rx = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _has_email(notes: Optional[str]) -> bool:
    return bool(notes and _email_rx.search(notes))


def _pick_candidates(limit: Optional[int] = None) -> list[dict]:
    s = SessionLocal()
    try:
        rows = s.execute(
            select(AttendanceSignal).where(
                AttendanceSignal.source_platform.in_(
                    ["buyer-prospection", "sales-prospection"]
                )
            )
        ).scalars().all()
        out: list[dict] = []
        for sig in rows:
            if _has_email(sig.notes):
                continue
            url = (sig.source_url or "").lower()
            if "linkedin.com/" not in url:
                continue
            out.append({
                "id": sig.id,
                "linkedin": sig.source_url,
                "person": sig.person_name,
                "company": sig.company_name,
            })
        if limit:
            out = out[:limit]
        return out
    finally:
        s.close()


async def _enrich_one(
    client: httpx.AsyncClient, key: str, linkedin_url: str,
) -> tuple[Optional[str], int]:
    """Return ``(email_or_None, credits_remaining)``."""
    headers = {"Authorization": f"Bearer {key}"}
    backoff = [4, 8, 16, 32]
    for attempt in range(6):
        try:
            r = await client.get(
                BASE_URL,
                params={"linkedin_profile": linkedin_url},
                headers=headers,
                timeout=30,
            )
        except httpx.HTTPError as e:
            logger.warning(f"HTTP error on {linkedin_url}: {e}")
            await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
            continue

        if r.status_code in (202,):
            # async pending — retry
            await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
            continue
        if r.status_code == 429:
            await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)] * 2)
            continue
        if r.status_code == 401:
            raise RuntimeError(
                f"Enrich.so auth failed (401) : {r.text[:200]}"
            )
        if r.status_code >= 500:
            await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
            continue

        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            return None, -1
        if isinstance(data, dict) and data.get("status") == "in_progress":
            await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
            continue
        email = data.get("email") if isinstance(data, dict) else None
        found = bool(data.get("found")) if isinstance(data, dict) else False
        credits_left = int(data.get("credits_remaining", -1)) \
            if isinstance(data, dict) else -1
        if found and email:
            return email.strip().lower(), credits_left
        return None, credits_left
    return None, -1


async def _run(limit: Optional[int], concurrency: int) -> None:
    key = os.getenv("ENRICH_SO_API_KEY", "")
    if not key:
        for line in Path(ROOT / ".env").read_text().splitlines():
            if line.startswith("ENRICH_SO_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    if not key:
        raise SystemExit("ENRICH_SO_API_KEY missing")

    candidates = _pick_candidates(limit=limit)
    print(f"Candidats à enrichir : {len(candidates)}")
    if not candidates:
        return

    sem = asyncio.Semaphore(concurrency)
    s = SessionLocal()
    n_found = 0
    n_not_found = 0
    n_errors = 0
    credits_remaining = -1
    t0 = time.time()

    async with httpx.AsyncClient(http2=True) as client:
        async def process(cand: dict) -> None:
            nonlocal n_found, n_not_found, n_errors, credits_remaining
            async with sem:
                try:
                    email, credits = await _enrich_one(
                        client, key, cand["linkedin"]
                    )
                    if credits >= 0:
                        credits_remaining = credits
                    if email:
                        n_found += 1
                        sig = s.get(AttendanceSignal, cand["id"])
                        if sig:
                            notes = sig.notes or ""
                            if notes:
                                notes = notes.rstrip(". ") + ". "
                            sig.notes = notes + f"Direct email: {email}."
                    else:
                        n_not_found += 1
                except RuntimeError as e:
                    n_errors += 1
                    logger.error(str(e))
                except Exception as e:  # noqa: BLE001
                    n_errors += 1
                    logger.warning(f"Failed {cand['person']}: {e!r}")

        tasks = [process(c) for c in candidates]
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            await coro
            if i % 25 == 0 or i == len(tasks):
                dt = time.time() - t0
                rate = i / dt if dt > 0 else 0
                print(
                    f"  [{i:>5}/{len(tasks)}]  "
                    f"found={n_found}  miss={n_not_found}  err={n_errors}  "
                    f"({rate:.2f}/s)  credits_left={credits_remaining}"
                )
            if i % 50 == 0:
                s.commit()

    s.commit()
    s.close()
    dt = time.time() - t0
    print(
        f"\nTerminé en {dt:.1f}s — "
        f"found {n_found}  miss {n_not_found}  err {n_errors}  "
        f"credits_left ≈ {credits_remaining}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()
    asyncio.run(_run(args.limit, args.concurrency))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
