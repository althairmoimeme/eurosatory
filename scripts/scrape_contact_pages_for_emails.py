"""Scrape contact pages for NAMED emails on exhibitors with ZERO emails.

NAMED-ONLY strategy — single phase
──────────────────────────────────
  For each exhibitor with a website but no email anywhere, fetch a
  handful of probe paths (/contact, /contact-us, /about, /imprint…),
  grep for ``mailto:`` links + plain-text ``foo@bar.com`` patterns.

  Filters applied at INSERT time :
    - Domain must match the exhibitor's own website host (no third-party)
    - Local part must NOT be generic (info@ / contact@ / sales@ / support@
      / hello@ / kontakt@ / office@ / commercial@ / etc.) — those are
      useless for cold outreach.
    - Domain must have an MX record.

  Confidence:
    - ``high``   when sourced from ``mailto:`` link (the company chose to
                 publish this email)
    - ``medium`` when only found in plain text (regex)

Usage :
    python -m scripts.scrape_contact_pages_for_emails                 # full
    python -m scripts.scrape_contact_pages_for_emails --limit 30 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from typing import Optional
from urllib.parse import urljoin, urlsplit

from sqlalchemy import select

from app.config import settings  # noqa: F401
from app.database import Exhibitor, ExhibitorContact, SessionLocal
from app.scrapers.http_client import HttpClient

try:
    import dns.resolver
except ImportError:
    print("Install : pip install dnspython", file=sys.stderr)
    raise

logger = logging.getLogger("scrape_emails")


# ─── PROBE PATHS ──────────────────────────────────────────────────────

_PROBE_PATHS = [
    "",  # homepage
    "/contact",
    "/contact-us",
    "/contact.html",
    "/contacts",
    "/nous-contacter",
    "/about",
    "/about-us",
    "/imprint",
    "/impressum",
    "/legal",
    "/legal-notice",
    "/mentions-legales",
    "/team",
    "/en/contact",
    "/fr/contact",
    "/de/contact",
    "/de/kontakt",
    "/kontakt",
]


# ─── EMAIL EXTRACTION ─────────────────────────────────────────────────

_EMAIL_RX = re.compile(
    r"\b([A-Za-z0-9](?:[A-Za-z0-9._%+\-]{0,62}[A-Za-z0-9])?@"
    r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,62}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,62}[A-Za-z0-9])?){1,3})\b"
)

_MAILTO_RX = re.compile(r'href=["\']?mailto:([^"\'>\s\?]+)', re.IGNORECASE)

# Strings that look like emails but aren't
_JUNK_LOCALS = {
    "example", "your.email", "youremail", "user", "email", "name",
    "firstname", "firstname.lastname", "test",
}

# Generic mailboxes — the user explicitly does not want these inserted
# (no commercial value for cold outreach). Match by EXACT local part OR
# local part starting with one of these followed by "-" / "." / "_".
_GENERIC_LOCALS_EXACT = {
    "info", "infos", "contact", "contacts", "sales", "support",
    "hello", "hi", "office", "team", "service", "services",
    "marketing", "presse", "press", "media", "communication",
    "communications", "commercial", "admin", "administration",
    "kontakt", "secretariat", "rh", "hr", "jobs", "career",
    "careers", "recrutement", "recruiting", "legal", "compliance",
    "gdpr", "rgpd", "webmaster", "newsletter", "noreply", "no-reply",
    "donotreply", "donot-reply", "do-not-reply", "feedback",
    "general", "general-inquiries", "enquiries", "inquiries",
    "info_fr", "infofr", "contact_fr", "info_en", "info_de",
    "info_es", "info_it", "vente", "ventes", "client",
    "clients", "boutique", "shop",
}
_GENERIC_LOCALS_PREFIXES = (
    "info-", "info.", "info_",
    "contact-", "contact.", "contact_",
    "sales-", "sales.", "sales_",
    "support-", "support.", "support_",
    "press-", "press.", "press_",
    "presse-", "presse.", "presse_",
    "kontakt-", "kontakt.", "kontakt_",
    "service-", "service.", "service_",
    "marketing-", "marketing.", "marketing_",
)


def _is_junk_email(email: str) -> bool:
    local = email.split("@")[0].lower()
    if local in _JUNK_LOCALS:
        return True
    if any(ext in email.lower() for ext in (
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".webp",
    )):
        return True
    if len(local) < 2 or len(email) > 80:
        return True
    return False


def _is_generic_local(local: str) -> bool:
    """Return True when this looks like a shared mailbox (info@, contact@…)."""
    low = local.lower()
    if low in _GENERIC_LOCALS_EXACT:
        return True
    if low.startswith(_GENERIC_LOCALS_PREFIXES):
        return True
    return False


def _extract_named_emails_from_html(
    html: str, host: str,
) -> list[tuple[str, str]]:
    """Return [(email, src: 'mailto'|'plain')] keeping ONLY :
      - emails whose domain matches the exhibitor's host (no 3rd party)
      - emails whose local part is NOT generic (named persons only)
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    host_root = ".".join(host.split(".")[-2:])  # eg. saab.com from www.saab.com

    def _consider(email: str, src: str) -> None:
        e = email.strip().lower()
        if "@" not in e or e in seen:
            return
        seen.add(e)
        if _is_junk_email(e):
            return
        local, _, dom = e.partition("@")
        if not (dom == host or dom.endswith("." + host_root) or dom == host_root):
            return
        if _is_generic_local(local):
            return  # ← key filter : drop info@/contact@/sales@…
        out.append((e, src))

    # 1. mailto: links (highest signal)
    for m in _MAILTO_RX.finditer(html):
        _consider(m.group(1), "mailto")

    # 2. plain-text email patterns
    for m in _EMAIL_RX.finditer(html):
        _consider(m.group(1), "plain")

    return out


# ─── MX VERIFICATION ──────────────────────────────────────────────────

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


def _host_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return None
    if not host:
        return None
    for p in ("www.", "en.", "fr.", "de.", "es.", "it.", "m."):
        if host.startswith(p):
            host = host[len(p):]
            break
    return host or None


# ─── ASYNC SCRAPE ─────────────────────────────────────────────────────

async def _scrape_one(
    c: HttpClient,
    exh: Exhibitor,
    dead_hosts: set[str],
) -> list[tuple[str, str]]:
    site = (exh.website_url or "").strip()
    if not site.startswith(("http://", "https://")):
        return []
    host = _host_from_url(site)
    if not host or host in dead_hosts:
        return []

    found: list[tuple[str, str]] = []
    homepage_failed = False
    for idx, path in enumerate(_PROBE_PATHS):
        if len(found) >= 3:
            break
        url = urljoin(site, path) if path else site
        if homepage_failed and idx > 0:
            break
        try:
            r = await c.request("GET", url)
        except Exception:  # noqa: BLE001
            if idx == 0:
                homepage_failed = True
                dead_hosts.add(host)
            continue
        if r.status_code >= 400:
            if idx == 0:
                homepage_failed = True
            continue
        ct = (r.headers.get("content-type") or "").lower()
        if "html" not in ct:
            continue
        html = r.text or ""
        if not html:
            continue
        emails = _extract_named_emails_from_html(html, host)
        for e, src in emails:
            if e not in {x[0] for x in found}:
                found.append((e, src))
            if len(found) >= 4:
                break
    return found


async def _run_scrape(
    targets: list[Exhibitor],
    mx_cache: dict,
) -> dict[int, list[tuple[str, str]]]:
    results: dict[int, list[tuple[str, str]]] = {}
    dead_hosts: set[str] = set()
    t0 = time.time()
    print(f"[scrape] {len(targets)} exhibitors with website + no email", flush=True)

    async with HttpClient(
        per_host_delay=0.05, concurrency=24, timeout=8, max_retries=1,
    ) as c:
        async def _one(exh: Exhibitor) -> None:
            try:
                emails = await _scrape_one(c, exh, dead_hosts)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"scrape fail {exh.company_name}: {e!r}")
                return
            if emails:
                # MX-verify the domain once.
                host = _host_from_url(exh.website_url)
                if host and not _domain_has_mx(host, mx_cache):
                    return
                results[exh.id] = emails

        batch = 48
        for i in range(0, len(targets), batch):
            await asyncio.gather(*[_one(e) for e in targets[i:i + batch]])
            elapsed = int(time.time() - t0)
            n_hits = len(results)
            n_emails = sum(len(v) for v in results.values())
            print(
                f"  [{elapsed:>4}s] scanned {min(i + batch, len(targets))}/"
                f"{len(targets)} · hits={n_hits} · emails={n_emails} · "
                f"dead_hosts={len(dead_hosts)}",
                flush=True,
            )
    return results


# ─── MAIN ─────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    s = SessionLocal()

    # All exhibitors with any NAMED email already.
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

    targets = []
    for exh in s.execute(
        select(Exhibitor).where(Exhibitor.website_url.is_not(None))
    ).scalars():
        if exh.id in has_email:
            continue
        site = (exh.website_url or "").strip()
        if not site.startswith(("http://", "https://")):
            continue
        targets.append(exh)

    if args.limit:
        targets = targets[:args.limit]

    mx_cache: dict[str, bool] = {}
    scraped = asyncio.run(_run_scrape(targets, mx_cache))

    print()
    print(f"[result] {len(scraped)} exhibitors → "
          f"{sum(len(v) for v in scraped.values())} named emails extracted")

    to_insert: list[ExhibitorContact] = []
    for eid, emails in scraped.items():
        for email, src in emails[:2]:  # max 2 per exhibitor
            confidence = "high" if src == "mailto" else "medium"
            to_insert.append(ExhibitorContact(
                exhibitor_id=eid,
                full_name=None,
                function=None,
                email=email,
                is_generic=False,
                confidence=confidence,
                source_url=f"scraped:{src}",
            ))

    print(f"[plan] would insert : {len(to_insert)} new ExhibitorContact rows")

    if args.dry_run:
        print("[dry-run] no DB writes")
        s.close()
        return 0

    if to_insert:
        s.bulk_save_objects(to_insert)
        s.commit()
    s.close()
    print(f"✅ Inserted {len(to_insert)} new ExhibitorContact rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
