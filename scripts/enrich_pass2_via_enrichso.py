"""Second pass — Enrich.so on the exhibitors the first pass missed.

Two improvements over the original pass
───────────────────────────────────────
  1. EXTEND TARGETS — also process exhibitors WITHOUT a LinkedIn URL,
     by generating 2-3 LinkedIn slug candidates from the company name
     (e.g. "2iC Limited" → ["2ic-limited", "2ic"]). Stops at the first
     slug that returns employees.

  2. PATTERN FALLBACK — when find_email returns None for an employee
     we just discovered, build ``firstname.lastname@<domain>`` and
     MX-validate it. Insert with confidence='medium' instead of 'high'.

Idempotency
───────────
  Skips any exhibitor that already has any email row. A "tried but
  empty" marker file (``data/.enrich_pass2_processed``) is appended
  with the exhibitor_id after each attempt so re-runs don't waste
  credits on the same dead-ends.

Usage
─────
    python -m scripts.enrich_pass2_via_enrichso --limit 20             # pilot
    python -m scripts.enrich_pass2_via_enrichso                        # full
    python -m scripts.enrich_pass2_via_enrichso --include-linkedin     # also re-try those with linkedin_url
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from app.config import settings  # noqa: F401
from app.database import Exhibitor, ExhibitorContact, SessionLocal

# Re-use the existing MCP client + slug helper
sys.path.insert(0, str(ROOT / "scripts"))
from enrich_ads_employees import (  # noqa: E402
    EnrichMCP, ENRICH_KEY_ENV, linkedin_slug_candidates,
)

try:
    import dns.resolver
except ImportError:
    print("Install : pip install dnspython", file=sys.stderr)
    raise

logger = logging.getLogger("enrich-pass2")
MARKER_FILE = ROOT / "data" / ".enrich_pass2_processed"


# ─── HELPERS (same filters as the other email scripts) ───────────────

_GENERIC_LOCALS = {
    "info", "infos", "contact", "contacts", "sales", "support",
    "hello", "office", "team", "service", "services", "marketing",
    "presse", "press", "kontakt", "secretariat", "commercial",
    "admin", "webmaster", "noreply", "no-reply",
}


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        p = urlparse(url if "://" in url else "http://" + url)
        d = (p.netloc or p.path).lower()
        d = re.sub(r"^www\.", "", d).split("/")[0].split(":")[0]
        return d if "." in d else None
    except Exception:  # noqa: BLE001
        return None


def _is_generic_local(local: str) -> bool:
    low = local.lower()
    if low in _GENERIC_LOCALS:
        return True
    for prefix in ("info-", "info.", "contact-", "contact.",
                   "sales-", "sales.", "support-"):
        if low.startswith(prefix):
            return True
    return False


def _normalize(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z]", "", s)
    return s.lower()


def _domain_has_mx(domain: str, cache: dict, timeout: float = 3.0) -> bool:
    if domain in cache:
        return cache[domain]
    try:
        r = dns.resolver.Resolver()
        r.lifetime = timeout
        r.timeout = timeout
        ans = r.resolve(domain, "MX")
        ok = len(ans) > 0
    except Exception:  # noqa: BLE001
        ok = False
    cache[domain] = ok
    return ok


# ─── MARKER FILE — track processed exhibitors ────────────────────────

def _load_processed() -> set[int]:
    if not MARKER_FILE.exists():
        return set()
    try:
        return {int(line.strip())
                for line in MARKER_FILE.read_text().splitlines()
                if line.strip().isdigit()}
    except Exception:  # noqa: BLE001
        return set()


def _mark_processed(exhibitor_id: int) -> None:
    MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with MARKER_FILE.open("a") as f:
        f.write(f"{exhibitor_id}\n")


# ─── DB I/O ───────────────────────────────────────────────────────────

def load_targets(s, include_linkedin: bool,
                 limit: int | None) -> list[Exhibitor]:
    has_email: set[int] = set()
    for (eid,) in s.execute(
        select(ExhibitorContact.exhibitor_id).where(
            ExhibitorContact.email.is_not(None),
            ExhibitorContact.email != "",
        )
    ).all():
        has_email.add(eid)
    for eid, ce in s.execute(
        select(Exhibitor.id, Exhibitor.contact_email)
    ).all():
        if ce and ce.strip():
            has_email.add(eid)

    processed = _load_processed()
    targets = []
    for exh in s.execute(
        select(Exhibitor).where(Exhibitor.website_url.is_not(None))
    ).scalars():
        if exh.id in has_email:
            continue
        if exh.id in processed:
            continue
        site = (exh.website_url or "").strip()
        if not site.startswith(("http://", "https://")):
            continue
        if not _domain_from_url(site):
            continue
        # By default, only target those WITHOUT linkedin_url (pass 1
        # already tried those). --include-linkedin re-tries those too,
        # using the new pattern fallback.
        has_li = bool(exh.linkedin_url and exh.linkedin_url.strip())
        if has_li and not include_linkedin:
            continue
        targets.append(exh)
    if limit:
        targets = targets[:limit]
    return targets


def insert_contact(s, exhibitor_id: int, first: str, last: str,
                   role: str | None, email: str, source_tag: str,
                   confidence: str, linkedin: str | None) -> None:
    s.add(ExhibitorContact(
        exhibitor_id=exhibitor_id,
        full_name=f"{first} {last}".strip() or None,
        function=role or None,
        email=email,
        phone=None,
        linkedin=linkedin or None,
        is_generic=False,
        confidence=confidence,
        source_url=f"enrich-so:{source_tag}",
    ))


# ─── EMPLOYEE FINDER (with slug fallback) ────────────────────────────

async def _employees_for_exhibitor(
    cli: EnrichMCP, exh: Exhibitor, max_results: int,
) -> tuple[list[dict], str]:
    """Returns (employees_list, used_linkedin_url). Tries the explicit
    linkedin_url first, then falls back to slug candidates derived from
    the company name."""

    candidate_urls: list[str] = []
    if exh.linkedin_url and "linkedin.com/company/" in exh.linkedin_url.lower():
        candidate_urls.append(exh.linkedin_url.strip())
    for slug in linkedin_slug_candidates(exh.company_name or ""):
        candidate_urls.append(f"https://www.linkedin.com/company/{slug}")

    for url in candidate_urls:
        out = await cli.call("employee_finder", {
            "company_linkedin_url": url,
            "job_level": ["C-Level", "VP", "Director", "Manager"],
            "max_results": max_results,
        })
        if not out or not out.get("success"):
            continue
        employees = (out.get("data") or {}).get("results", []) or []
        if employees:
            return employees, url
    return [], ""


async def _process_one(cli, s, exh, mx_cache, stats) -> None:
    domain = _domain_from_url(exh.website_url)
    if not domain:
        return

    employees, used_url = await _employees_for_exhibitor(
        cli, exh, max_results=3,
    )
    stats["employee_finder_calls"] += 1
    if not employees:
        stats["no_employees"] += 1
        _mark_processed(exh.id)
        return
    stats["employees_returned"] += len(employees)

    # Pre-verify MX once per domain (cheap, cached)
    has_mx = _domain_has_mx(domain, mx_cache)

    inserted_any = False
    for emp in employees:
        first = (emp.get("first_name") or "").strip()
        last = (emp.get("last_name") or "").strip()
        if not first or not last:
            continue

        # 1. real find_email
        email = await cli.find_email(first, last, domain)
        stats["find_email_calls"] += 1
        confidence = "high"
        source_tag = "employee_finder+find_email"

        # 2. fallback : pattern firstname.lastname@domain + MX
        if not email and has_mx:
            f = _normalize(first)
            l = _normalize(last)
            if f and l and len(f) >= 2 and len(l) >= 2:
                email = f"{f}.{l}@{domain}"
                confidence = "medium"
                source_tag = "employee_finder+pattern"
                stats["pattern_fallback_used"] += 1

        if not email:
            stats["email_not_found"] += 1
            continue
        if "@" not in email:
            continue
        local = email.split("@")[0]
        if _is_generic_local(local):
            stats["email_rejected_as_generic"] += 1
            continue

        role = (emp.get("job_title") or emp.get("title") or "").strip()
        linkedin = emp.get("linkedin_url")
        insert_contact(s, exh.id, first, last, role, email,
                       source_tag, confidence, linkedin)
        s.commit()
        stats["emails_inserted"] += 1
        inserted_any = True
        break  # cap : 1 email per exhibitor

    _mark_processed(exh.id)


async def _run(targets, concurrency: int = 3) -> dict:
    key = os.environ.get(ENRICH_KEY_ENV)
    if not key:
        raise RuntimeError(f"{ENRICH_KEY_ENV} missing in env")
    cli = EnrichMCP(key)
    stats = {
        "exhibitors_processed": 0,
        "employee_finder_calls": 0,
        "find_email_calls": 0,
        "employees_returned": 0,
        "no_employees": 0,
        "email_not_found": 0,
        "email_rejected_as_generic": 0,
        "pattern_fallback_used": 0,
        "emails_inserted": 0,
    }
    s = SessionLocal()
    t0 = time.time()
    sem = asyncio.Semaphore(concurrency)
    mx_cache: dict[str, bool] = {}

    async def _bounded(exh) -> None:
        async with sem:
            try:
                await _process_one(cli, s, exh, mx_cache, stats)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"  ! {exh.company_name}: {e!r}")
            stats["exhibitors_processed"] += 1

    try:
        await cli.initialize()
        wave = 30
        for start in range(0, len(targets), wave):
            chunk = targets[start:start + wave]
            await asyncio.gather(*(_bounded(e) for e in chunk))
            elapsed = int(time.time() - t0)
            print(
                f"  [{elapsed:>4}s] processed {stats['exhibitors_processed']}/"
                f"{len(targets)} · emails_inserted={stats['emails_inserted']} · "
                f"pattern_fallback={stats['pattern_fallback_used']}",
                flush=True,
            )
    finally:
        await cli.close()
        s.close()
    stats["elapsed_seconds"] = int(time.time() - t0)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--include-linkedin", action="store_true",
                    help="Also re-try exhibitors that have a linkedin_url "
                    "(default : skip them, pass 1 already covered)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    s = SessionLocal()
    targets = load_targets(s, include_linkedin=args.include_linkedin,
                           limit=args.limit)
    s.close()
    print(f"[targets] {len(targets)} exhibitors to process "
          f"(include_linkedin={args.include_linkedin})")
    if args.dry_run:
        for exh in targets[:5]:
            print(f"  - #{exh.id:5d} {exh.company_name} ({exh.country_iso2}) "
                  f"linkedin={'yes' if exh.linkedin_url else 'NO'}")
        return 0

    import json
    stats = asyncio.run(_run(targets))
    print()
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
