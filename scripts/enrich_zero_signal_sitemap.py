"""Second-pass enrichment via sitemap.xml.

Strategy : for each remaining zero-signal exhibitor, fetch ``/sitemap.xml``
(and a few common variants), extract every <loc>, keep only the ones whose
path contains the word "eurosatory", then fetch + scan those pages.

This catches dedicated event pages buried in non-standard paths like :
    /events/exhibitions/eurosatory-2026
    /fr/actualites/eurosatory-2026
    /de/messen/eurosatory
    /press-room/news/eurosatory-2024
that the corporate-site probe (which only tests 7 fixed paths) misses.

Usage :
    python -m scripts.enrich_zero_signal_sitemap                   # default 2026
    python -m scripts.enrich_zero_signal_sitemap --limit 50        # quick sample
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlsplit

from app.config import settings  # noqa: F401
from app.database import AttendanceSignal, Exhibitor, SessionLocal
from app.attendance.seed import RawSignal, upsert_signal
from app.scrapers.http_client import HttpClient
from app.attendance.auto_collect import (
    _visible_text, _page_title, _scan_text_for_year,
)
from sqlalchemy import select, func, or_


logger = logging.getLogger("enrich_sitemap")


_SITEMAP_CANDIDATES = [
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemaps.xml",
    "/sitemap/sitemap.xml",
]

# Quick & dirty <loc> extractor — robust enough for both sitemap and
# sitemap index XML.
_LOC_RX = re.compile(r"<loc[^>]*>\s*([^<]+?)\s*</loc>", re.IGNORECASE)

_EUROSATORY_IN_URL_RX = re.compile(r"eurosatory", re.IGNORECASE)


def _load_zero_signal_exhibitors() -> list[Exhibitor]:
    s = SessionLocal()
    try:
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
        all_exhib = list(
            s.execute(
                select(Exhibitor).where(Exhibitor.website_url.is_not(None))
            ).scalars()
        )
    finally:
        s.close()

    rows: list[Exhibitor] = []
    for exh in all_exhib:
        k = (exh.company_name or "").strip().lower()
        if not k:
            continue
        if k in signal_names:
            continue
        site = (exh.website_url or "").strip()
        if not site.startswith(("http://", "https://")):
            continue
        rows.append(exh)
    return rows


async def _fetch_text(c: HttpClient, url: str) -> Optional[str]:
    try:
        r = await c.request("GET", url)
    except Exception:  # noqa: BLE001
        return None
    if r.status_code >= 400:
        return None
    return r.text or None


async def _gather_sitemap_urls(
    c: HttpClient, site: str, max_depth: int = 2,
) -> list[str]:
    """Return all <loc> URLs found in any sitemap of the site, recursing
    one level into sitemap-index nesting (depth=2)."""
    seen: set[str] = set()
    out: list[str] = []

    async def _walk(url: str, depth: int) -> None:
        if depth > max_depth or url in seen:
            return
        seen.add(url)
        body = await _fetch_text(c, url)
        if not body:
            return
        for m in _LOC_RX.finditer(body):
            loc = m.group(1).strip()
            if not loc:
                continue
            if loc.endswith(".xml") and depth < max_depth:
                await _walk(loc, depth + 1)
            else:
                out.append(loc)

    for cand in _SITEMAP_CANDIDATES:
        await _walk(urljoin(site, cand), 0)
        if out:
            break  # one hit is enough — most sites only have one sitemap
    return out


async def _scan_via_sitemap(
    c: HttpClient,
    exhibitor: Exhibitor,
    target_year: int,
    dead_hosts: set[str],
    matches_per_company_cap: int = 2,
) -> list[RawSignal]:
    site = (exhibitor.website_url or "").strip()
    host = urlsplit(site).netloc
    if host in dead_hosts:
        return []

    urls = await _gather_sitemap_urls(c, site)
    if not urls:
        return []

    # Filter URLs by 'eurosatory' keyword in the path.
    interesting = [u for u in urls if _EUROSATORY_IN_URL_RX.search(u)]
    if not interesting:
        return []

    # Dedupe + cap.
    seen: set[str] = set()
    todo: list[str] = []
    for u in interesting:
        if u not in seen:
            seen.add(u)
            todo.append(u)
        if len(todo) >= matches_per_company_cap * 3:  # we filter again after fetch
            break

    found: list[RawSignal] = []
    for url in todo:
        if len(found) >= matches_per_company_cap:
            break
        body = await _fetch_text(c, url)
        if not body:
            continue
        text = _visible_text(body)
        hit = _scan_text_for_year(text, target_year)
        if hit is None:
            continue
        title = _page_title(body)
        sig = RawSignal(
            edition_year=hit.year,
            entity_type="company",
            source_url=url,
            company_name=exhibitor.company_name,
            country=exhibitor.country_name,
            source_platform="corporate_site",
            source_title=title,
            source_snippet=hit.snippet,
            search_query_used="enrich_sitemap:eurosatory_in_url",
            signal_type="company_announcement",
            signal_text=hit.snippet,
            is_company_post=True,
            notes=(
                f"Sitemap-discovered for exhibitor #{exhibitor.id} "
                f"({exhibitor.company_name})"
            ),
        )
        found.append(sig)
    return found


async def _run(target_year: int, limit: Optional[int]) -> dict:
    rows = _load_zero_signal_exhibitors()
    if limit:
        rows = rows[:limit]
    total = len(rows)
    print(f"[enrich-sitemap] candidates : {total} exhibitors "
          f"with website + zero signals", flush=True)
    if total == 0:
        return {"scanned": 0, "hits": 0, "new": 0,
                "target_year": target_year}

    scanned = 0
    sitemap_ok = 0
    hits = 0
    new = 0
    updated = 0
    errors = 0
    t0 = time.time()

    async with HttpClient(
        per_host_delay=0.05, concurrency=20, timeout=8, max_retries=1,
    ) as c:
        dead_hosts: set[str] = set()

        async def _one(exh: Exhibitor) -> None:
            nonlocal scanned, sitemap_ok, hits, new, updated, errors
            scanned += 1
            try:
                signals = await _scan_via_sitemap(
                    c, exh, target_year, dead_hosts,
                )
            except Exception as e:  # noqa: BLE001
                errors += 1
                logger.debug(
                    f"sitemap scan failed for "
                    f"{exh.company_name!r}: {e!r}"
                )
                return
            if signals:
                sitemap_ok += 1
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

        batch = 48
        for i in range(0, len(rows), batch):
            await asyncio.gather(*[_one(e) for e in rows[i:i + batch]])
            elapsed = int(time.time() - t0)
            print(
                f"  [{elapsed:>4}s] scanned {scanned}/{total} · "
                f"sitemap_hits={sitemap_ok} · new_signals={new}",
                flush=True,
            )

    return {
        "scanned": scanned,
        "exhibitors_with_eurosatory_in_sitemap": sitemap_ok,
        "hits": hits,
        "new": new,
        "updated": updated,
        "errors": errors,
        "total_candidates": total,
        "target_year": target_year,
        "elapsed_seconds": int(time.time() - t0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    res = asyncio.run(_run(args.year, args.limit))
    print()
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
