"""Targeted enrichment : scan only exhibitors with ZERO attendance signal.

This is the "fill the gap" pass — the regular auto_collect scans every
exhibitor, which wastes bandwidth re-scanning the 583 already-covered ones.
This variant pulls the explicit list of exhibitors with no signals at all
and probes their corporate sites for ``Eurosatory <year>`` mentions.

Usage :
    python -m scripts.enrich_zero_signal_exhibitors                 # default year 2026
    python -m scripts.enrich_zero_signal_exhibitors --year 2026     # explicit
    python -m scripts.enrich_zero_signal_exhibitors --limit 100     # dry-run-ish

Output :
    Final JSON report : {scanned, hits, new, updated, errors, total_candidates}.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from urllib.parse import urlsplit

from sqlalchemy import select, func

from app.config import settings  # noqa: F401  — ensure config loads
from app.database import AttendanceSignal, Exhibitor, SessionLocal
from app.attendance.seed import upsert_signal
from app.scrapers.http_client import HttpClient
from app.attendance.auto_collect import _scan_one_exhibitor


logger = logging.getLogger("enrich_zero_signal")


def _load_zero_signal_exhibitors() -> list[Exhibitor]:
    """Return exhibitors with no AttendanceSignal row (matched on
    case-insensitive trimmed company_name == canonical_company_name or
    company_name)."""
    s = SessionLocal()
    try:
        # Compute the set of company names that DO have signals
        from sqlalchemy import or_
        signal_names_q = select(
            func.lower(func.trim(
                func.coalesce(
                    AttendanceSignal.canonical_company_name,
                    AttendanceSignal.company_name,
                )
            ))
        ).where(
            or_(
                AttendanceSignal.canonical_company_name.is_not(None),
                AttendanceSignal.company_name.is_not(None),
            )
        ).distinct()
        signal_names: set[str] = set()
        for (n,) in s.execute(signal_names_q):
            if n:
                signal_names.add(n)

        # Pull all exhibitors with a usable website
        all_exhib = list(
            s.execute(
                select(Exhibitor).where(Exhibitor.website_url.is_not(None))
            ).scalars()
        )
    finally:
        s.close()

    zero_signal: list[Exhibitor] = []
    for exh in all_exhib:
        k = (exh.company_name or "").strip().lower()
        if not k:
            continue
        if k in signal_names:
            continue
        site = (exh.website_url or "").strip()
        if not site.startswith(("http://", "https://")):
            continue
        zero_signal.append(exh)
    return zero_signal


async def _run(target_year: int, limit: int | None) -> dict:
    rows = _load_zero_signal_exhibitors()
    if limit:
        rows = rows[:limit]

    total = len(rows)
    print(f"[enrich-zero] candidates : {total} exhibitors "
          f"with website + zero signals", flush=True)
    if total == 0:
        return {"scanned": 0, "hits": 0, "new": 0, "updated": 0,
                "errors": 0, "total_candidates": 0,
                "target_year": target_year}

    scanned = 0
    hits = 0
    new = 0
    updated = 0
    errors = 0
    t0 = time.time()

    async with HttpClient(
        per_host_delay=0.05, concurrency=24, timeout=8, max_retries=1,
    ) as c:
        dead_hosts: set[str] = set()

        async def _one(exh: Exhibitor) -> None:
            nonlocal scanned, hits, new, updated, errors
            scanned += 1
            site = (exh.website_url or "").strip()
            host = urlsplit(site).netloc
            if host in dead_hosts:
                return
            try:
                signals = await _scan_one_exhibitor(
                    c, exh.id, exh.company_name,
                    exh.country_name, site,
                    target_year=target_year,
                    dead_hosts=dead_hosts,
                )
            except Exception as e:  # noqa: BLE001
                errors += 1
                logger.debug(f"scan failed for {exh.company_name!r}: {e!r}")
                return
            for sig in signals:
                hits += 1
                try:
                    _, is_new = upsert_signal(sig)
                    if is_new:
                        new += 1
                    else:
                        updated += 1
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    logger.debug(
                        f"upsert failed for {sig.source_url}: {e!r}"
                    )

        batch = 64
        for i in range(0, len(rows), batch):
            await asyncio.gather(*[_one(e) for e in rows[i:i + batch]])
            elapsed = int(time.time() - t0)
            print(
                f"  [{elapsed:>4}s] scanned {scanned}/{total} · "
                f"hits={hits} · new={new} · dead_hosts={len(dead_hosts)}",
                flush=True,
            )

    return {
        "scanned": scanned,
        "hits": hits,
        "new": new,
        "updated": updated,
        "errors": errors,
        "total_candidates": total,
        "target_year": target_year,
        "elapsed_seconds": int(time.time() - t0),
        "dead_hosts": len(dead_hosts),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026,
                    help="Edition cible (default : 2026)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Max exhibitors to scan (default : all)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    res = asyncio.run(_run(args.year, args.limit))
    print()
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
