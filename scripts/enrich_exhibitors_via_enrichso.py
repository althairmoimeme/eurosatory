"""Enrich exhibitors that have NO email yet — via Enrich.so MCP.

Flow per exhibitor
──────────────────
  1. employee_finder(company_linkedin_url, job_level=[C-Level,VP,Director])
     → returns up to ``--per-company`` employees with name + LinkedIn
  2. find_email(first, last, domain_from_website)
     → returns the prospect email if Enrich.so finds it
  3. Insert as ExhibitorContact with confidence='high' + source 'enrich-so'

Idempotent : skips any exhibitor that ALREADY has at least 1 email row.

Usage :
    python -m scripts.enrich_exhibitors_via_enrichso --limit 5 --per-company 1
        → pilot (5 calls, ~5 credits)
    python -m scripts.enrich_exhibitors_via_enrichso --per-company 1
        → full run (900 candidates × ~2 credits each = ~1800 credits)
    python -m scripts.enrich_exhibitors_via_enrichso --dry-run
        → no API calls, just list the targets
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

# Bootstrap
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from app.config import settings  # noqa: F401
from app.database import Exhibitor, ExhibitorContact, SessionLocal

# Re-use the existing MCP client (ADS-employees script)
sys.path.insert(0, str(ROOT / "scripts"))
from enrich_ads_employees import EnrichMCP, ENRICH_KEY_ENV  # noqa: E402


logger = logging.getLogger("enrich-via-enrichso")


# ─── HELPERS ──────────────────────────────────────────────────────────

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


def _is_linkedin_company_url(url: str | None) -> bool:
    if not url:
        return False
    u = url.lower()
    return "linkedin.com/company/" in u or "linkedin.com/school/" in u


def _is_junk_or_generic_email(e: str | None) -> bool:
    """Reject generic mailboxes — same filter as scrape_contact_pages."""
    if not e or "@" not in e:
        return True
    local = e.split("@")[0].lower()
    generics = {
        "info", "contact", "sales", "support", "hello", "office",
        "team", "service", "marketing", "press", "presse", "kontakt",
        "commercial", "admin", "webmaster", "noreply", "no-reply",
        "communication", "legal", "secretariat",
    }
    if local in generics:
        return True
    for prefix in ("info-", "info.", "contact-", "contact.",
                   "sales-", "support-"):
        if local.startswith(prefix):
            return True
    return False


# ─── DB I/O ───────────────────────────────────────────────────────────

def load_targets(s, limit: int | None) -> list[Exhibitor]:
    """Exhibitors with NO email yet + a LinkedIn company URL + a website."""
    # Build the set of exhibitors that have at least 1 email anywhere.
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

    rows = []
    for exh in s.execute(
        select(Exhibitor).where(Exhibitor.linkedin_url.is_not(None))
    ).scalars():
        if exh.id in has_email:
            continue
        if not _is_linkedin_company_url(exh.linkedin_url):
            continue
        if not _domain_from_url(exh.website_url):
            continue
        rows.append(exh)
    if limit:
        rows = rows[:limit]
    return rows


def insert_contact(s, exhibitor_id: int, first: str, last: str,
                   role: str | None, email: str,
                   linkedin: str | None) -> None:
    s.add(ExhibitorContact(
        exhibitor_id=exhibitor_id,
        full_name=f"{first} {last}".strip() or None,
        function=role or None,
        email=email,
        phone=None,
        linkedin=linkedin or None,
        is_generic=False,
        confidence="high",
        source_url="enrich-so:employee_finder+find_email",
    ))


# ─── MAIN ─────────────────────────────────────────────────────────────

async def _process_one(
    cli: EnrichMCP,
    s,
    exh: Exhibitor,
    per_company: int,
    stats: dict,
) -> None:
    domain = _domain_from_url(exh.website_url)
    if not domain:
        return

    # Call employee_finder DIRECTLY (the existing wrapper hardcodes
    # country=["United Kingdom"] for the ADS use case — we want all
    # countries here).
    out = await cli.call("employee_finder", {
        "company_linkedin_url": exh.linkedin_url,
        "job_level": ["C-Level", "VP", "Director", "Manager"],
        "max_results": per_company * 3,
    })
    stats["employee_finder_calls"] += 1
    employees = []
    if out and out.get("success"):
        employees = (out.get("data") or {}).get("results", []) or []
    if not employees:
        stats["no_employees"] += 1
        return
    stats["employees_returned"] += len(employees)

    inserted = 0
    for emp in employees:
        if inserted >= per_company:
            break
        first = (emp.get("first_name") or "").strip()
        last = (emp.get("last_name") or "").strip()
        if not first or not last:
            continue

        email = await cli.find_email(first, last, domain)
        stats["find_email_calls"] += 1
        if not email:
            stats["email_not_found"] += 1
            continue
        if _is_junk_or_generic_email(email):
            stats["email_rejected_as_generic"] += 1
            continue

        role = (emp.get("job_title") or emp.get("title") or "").strip()
        linkedin = emp.get("linkedin_url")
        insert_contact(s, exh.id, first, last, role, email, linkedin)
        s.commit()
        inserted += 1
        stats["emails_inserted"] += 1
        if stats["emails_inserted"] % 5 == 0:
            print(f"   ✓ {stats['emails_inserted']} emails inserted so far",
                  flush=True)


async def _run(targets: list[Exhibitor], per_company: int,
               concurrency: int = 3) -> dict:
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
        "emails_inserted": 0,
    }
    s = SessionLocal()
    t0 = time.time()
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(exh: Exhibitor) -> None:
        async with sem:
            try:
                await _process_one(cli, s, exh, per_company, stats)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"  ! {exh.company_name}: {e!r}")
            stats["exhibitors_processed"] += 1

    try:
        await cli.initialize()
        # Launch in waves so we can log progress.
        wave = 30
        for start in range(0, len(targets), wave):
            chunk = targets[start:start + wave]
            await asyncio.gather(*(_bounded(e) for e in chunk))
            elapsed = int(time.time() - t0)
            done = stats["exhibitors_processed"]
            print(
                f"  [{elapsed:>4}s] processed {done}/{len(targets)} · "
                f"emails_inserted={stats['emails_inserted']} · "
                f"calls={stats['employee_finder_calls']}+{stats['find_email_calls']}",
                flush=True,
            )
    finally:
        await cli.close()
        s.close()
    stats["elapsed_seconds"] = int(time.time() - t0)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Max exhibitors to process")
    ap.add_argument("--per-company", type=int, default=1,
                    help="Max emails to fetch per exhibitor (default: 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="No API calls — just list targets")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    s = SessionLocal()
    targets = load_targets(s, limit=args.limit)
    s.close()

    print(f"[targets] {len(targets)} exhibitors with no email + LinkedIn + website")
    if args.dry_run:
        for exh in targets[:10]:
            print(f"  - #{exh.id:5d} {exh.company_name} ({exh.country_iso2})  "
                  f"{exh.linkedin_url}")
        if len(targets) > 10:
            print(f"  ... ({len(targets) - 10} more)")
        return 0

    stats = asyncio.run(_run(targets, args.per_company))
    print()
    print("─── stats ───")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
