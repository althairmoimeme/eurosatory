"""Enrich existing ``AttendanceSignal`` rows that have missing fields.

The scraper / bulk-paste / manual-import paths often produce rows where
``person_name``, ``person_role`` or ``country`` are missing. This module
fills those gaps from cheap-to-acquire data :

1. **Country** — look up the canonical company name in the ``Exhibitor``
   catalog and copy ``country_name`` / ``country_iso2``.
2. **LinkedIn slug → person name** — when the source URL is
   ``linkedin.com/in/john-smith-1234`` we can derive a candidate name from
   the slug.
3. **Person + role from stored text** — scan ``source_snippet`` /
   ``signal_text`` / ``source_title`` for regex patterns like
   ``"<First Last>, <Role>"`` or ``"<Role> at <Company>"``.

Optionally (``include_refetch=True``) the source URL is re-fetched to
parse fresh metadata (``<title>`` / ``<meta property="og:*">`` / visible
text) for signals that are still incomplete.

The module is **read-only on the network unless asked** — by default it
only mines data we already have, so it's instant and safe to run on
demand.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from loguru import logger
from selectolax.parser import HTMLParser
from sqlalchemy import select

from app.attendance.dedup import canonical_person_name
from app.attendance.seed import RawSignal, upsert_signal
from app.database import (
    AttendanceSignal,
    Exhibitor,
    SessionLocal,
)
from app.scrapers.http_client import HttpClient


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------


# Names: "Firstname Lastname" — REQUIRES at least one whitespace-separated
# pair (a single hyphenated token like "Jean-Pol" alone is not a name).
# Each token ≥ 3 lowercase letters. Internal hyphens / apostrophes allowed
# inside a token ("Jean-Pol", "O'Brien").
_NAME_TOKEN = r"[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{2,}(?:[\-'][A-ZÀ-ÖØ-Þ]?[a-zà-öø-ÿ]{2,})?"
_NAME_RX = re.compile(
    r"\b(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){1,2})\b"
)

# Role keywords (FR + EN). Case-insensitive search.
_ROLE_KEYWORDS = (
    "CEO", "CTO", "COO", "CFO", "CIO", "CISO",
    "VP", "Vice President", "President", "Chairman", "Chairwoman",
    "Founder", "Co-Founder", "Cofounder", "Owner",
    "Director", "Directeur", "Directrice",
    "Head of", "Chef de", "Chef du",
    "Manager", "Responsable", "Manageur",
    "Lead", "Engineer", "Ingénieur", "Architect",
    "Sales", "Marketing", "Procurement", "Achats",
    "Program Manager", "Product Manager", "Business Development",
    "General Manager", "Managing Director", "DGA", "DG",
    "Country Manager",
)
_ROLE_RX = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _ROLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


# Pattern A: "John Smith, CEO" or "John Smith — CEO" or "John Smith (CEO)".
_NAME_WITH_ROLE_RX = re.compile(
    r"(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){1,2})"
    r"\s*[,\-—–:]\s*"
    r"(" + "|".join(re.escape(k) for k in _ROLE_KEYWORDS) + r"[a-zA-Z\s\-]*)",
    re.IGNORECASE,
)

# Pattern B: "Minister Florence Parly" / "President Emmanuel Macron" /
# "Colonel John Doe" — title precedes the full name. Strict prefixes
# only (no greedy "of …" tail) — that tail is a magnet for false
# positives where the next words happen to be capitalised but aren't a
# real person name. The role gets normalised to the leading title.
_TITLE_PREFIXES_STRICT = (
    # Multi-word common titles (curated, not greedy regex)
    r"Minister\s+of\s+Defense",
    r"Minister\s+of\s+Defence",
    r"Minister\s+of\s+Armed\s+Forces",
    r"Minister\s+of\s+the\s+Armed\s+Forces",
    r"Secretary\s+of\s+State",
    r"Secretary\s+of\s+Defense",
    r"Secretary\s+of\s+Defence",
    r"Chief\s+of\s+Staff",
    r"Director\s+of\s+Defense",
    r"Director\s+of\s+Defence",
    r"Director\s+of\s+Procurement",
    r"Director\s+of\s+Research",
    r"Director\s+of\s+Operations",
    r"Director\s+of\s+Programs",
    r"Head\s+of\s+Procurement",
    r"Head\s+of\s+Acquisition",
    r"Head\s+of\s+Research",
    r"Ministre\s+de\s+la\s+Défense",
    r"Ministre\s+des\s+Armées",
    r"Chef\s+d'État-Major",
    r"Chef\s+de\s+l'Armée",
    # Single-word titles
    r"Minister",
    r"Secretary",
    r"President",
    r"Vice\s+President",
    r"Chairman", r"Chairwoman",
    r"Lieutenant\s+General", r"Major\s+General", r"Brigadier\s+General",
    r"General", r"Lieutenant\s+Colonel", r"Colonel", r"Major",
    r"Captain", r"Commander", r"Admiral", r"Ambassador",
    # French
    r"Ministre",
    r"Pr[ée]sident(?:e)?",
    r"Directeur", r"Directrice",
    r"G[ée]n[ée]ral", r"Capitaine",
)
_TITLE_BEFORE_NAME_RX = re.compile(
    r"\b(?P<role>" + "|".join(_TITLE_PREFIXES_STRICT) + r")\s+"
    r"(?P<name>" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){1,2})\b"
)

# Pattern C: "CEO of Company" / "CEO at Company" / "Directeur de Company".
_ROLE_AT_COMPANY_RX = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _ROLE_KEYWORDS) + r")"
    r"\s+(?:of|at|chez|de\s+la|du)\s+"
    r"([A-Z][\w\s&'\-]{2,60})",
    re.IGNORECASE,
)


# Common false-positive name candidates (months, days, navigation menu
# items, marketing copy fragments) that we must skip when extracting
# "Firstname Lastname" patterns.
_NAME_BLACKLIST = {
    "Eurosatory", "Paris", "France", "United Kingdom", "United States",
    "Defense Show", "Defense Expo", "Trade Show",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday",
    "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche",
    "Press Release", "Communiqué Presse", "Read More", "Learn More",
    "Visit Us", "Meet Us", "About Us", "Contact Us",
}

# Words that should never appear in a real personal name. If a candidate
# token-set contains any of these, the candidate is rejected.
_NAME_NOISE_WORDS = frozenset(
    w.lower()
    for w in (
        "Reception", "Services", "What", "News", "Event", "Calendar",
        "Contact", "About", "Our", "Their", "Page", "Pages", "Home",
        "Menu", "Login", "Sign", "Register", "Search", "More", "Read",
        "Learn", "Visit", "Meet", "Discover", "Explore", "Welcome",
        "Hello", "Click", "Here", "Cookie", "Cookies", "Privacy",
        "Terms", "Conditions", "Policy", "Subscribe", "Newsletter",
        "Support", "Help", "FAQ", "Blog", "Article", "Articles",
        "Media", "Centre", "Center", "Press", "Release", "Releases",
        "Salon", "Show", "Booth", "Hall", "Stand", "Pavilion",
        "Industry", "Defense", "Defence", "Security", "Land",
        "Mobilisation", "Médias", "Communication",
        "Eurosatory", "Conference", "Programme", "Program", "Schedule",
        "Solutions", "Products", "Services",
        # Titles must NOT leak into the "name" itself — Pattern B handles
        # them, Pattern C must skip strings starting with one of these.
        "President", "Président", "Présidente", "Vice",
        "Minister", "Ministre", "Secretary", "Secrétaire",
        "General", "Général", "Lieutenant", "Major", "Colonel",
        "Captain", "Capitaine", "Commander", "Admiral", "Amiral",
        "Director", "Directeur", "Directrice",
        "Chairman", "Chairwoman", "Chief", "Head",
        "Ambassador", "Ambassadeur",
        "Forces",
        # Common press-page navigation / footer / aside tokens that
        # consistently leak into Pattern A / Pattern B captures.
        "Systems", "System", "Electronic", "Warfare", "Engineer",
        "Engineering", "Lifecycle", "Software", "Hardware",
        "Worldwide", "Certifications", "Opportunity", "Opportunities",
        "Email", "Phone", "Address", "Office", "Location",
        "Data", "Protection", "Regulation", "Compliance",
        "Bertin", "Environics", "Click", "Bond", "Clavister",
        "Condition", "Operationnelle", "Opérationnelle", "Assistance",
        "Achats", "explique", "Explique", "déclare", "annonce",
        "Solutions", "Products", "Services", "Service", "Product",
        "Group", "Corporation", "Limited", "Holdings", "Industries",
        "Defense", "Defence", "Security", "Aviation", "Aerospace",
        "Land", "Naval", "Air", "Cyber", "Marine",
        "International", "National", "European", "American",
        "Lifecycle", "Resources", "Capabilities", "Equipment",
        "Communication", "Communications", "Information",
        "Join", "Lead", "World", "Discover", "Sales", "After",
        "the",  # lowercase but caught when Title-cased "The"
        # NER false-positives we keep seeing on press / event homepages
        "Youtube", "YouTube", "Facebook", "Twitter", "Instagram",
        "TikTok", "WhatsApp", "Telegram", "WeChat",
        "Copyright", "Trademark", "Privacy", "Cookie",
        "Plaza", "Hub", "Studio", "Channel", "Corp",
        "Battlefield", "Abrams", "Warns", "Belarus",  # vehicle/headline tokens
        "Lanka", "Read", "Watch", "Listen", "Browse",
        "Managing", "Editor", "Reporter", "Correspondent",
        "Sponsored", "Featured", "Recommended", "Related", "Trending",
    )
)


def _looks_like_real_name(name: str) -> bool:
    """Reject names containing nav / marketing words."""
    tokens = [t.lower() for t in re.split(r"[\s\-']+", name) if t]
    if len(tokens) < 2 or len(tokens) > 3:
        return False
    if any(t in _NAME_NOISE_WORDS for t in tokens):
        return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_name(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip(" ,.;:-—–\t\n")
    if len(s) < 4 or len(s) > 60:
        return None
    if s in _NAME_BLACKLIST:
        return None
    if any(s.startswith(b) for b in _NAME_BLACKLIST):
        return None
    if not _looks_like_real_name(s):
        return None
    return s


# Trailing slug tokens that LinkedIn users append (their role / company /
# disambiguator) — strip before treating the rest as a name.
_LINKEDIN_TRAILING_TOKENS = frozenset({
    "ceo", "cto", "coo", "cfo", "cio", "ciso", "vp",
    "director", "directeur", "directrice",
    "manager", "lead", "head",
    "general", "colonel", "captain", "commander", "admiral",
    "major", "lieutenant", "sergeant",
    "president", "président", "presidente", "présidente",
    "minister", "ministre",
    "founder", "cofounder", "co-founder", "owner",
    "engineer", "ingenieur", "ingénieur",
    "consultant", "advisor", "conseiller",
    "expert", "specialist", "spécialiste",
    "professor", "professeur", "phd", "dr",
    "official", "officer",
    "ambassador", "ambassadeur",
    "chairman", "chairwoman",
    "secretary", "secrétaire",
    "sales", "marketing", "procurement", "achats",
})


def _slug_to_name(linkedin_url: str) -> Optional[str]:
    """``linkedin.com/in/john-smith-12345/`` → ``"John Smith"``.

    Steps :
      1. Extract the slug between ``/in/`` and the next ``/``.
      2. Drop pure-digit tokens (``12345``) and hex blobs (LinkedIn IDs).
      3. Drop tokens that contain digits (``ab12``) — never first / last
         names.
      4. Drop trailing role / title tokens (``ceo`` / ``general`` / …).
      5. Require at least 2 word tokens, each ≥ 2 chars and pure letters.
    """
    m = re.search(r"linkedin\.com/in/([^/?#]+)", linkedin_url, re.IGNORECASE)
    if not m:
        return None
    slug = m.group(1)
    parts = slug.split("-")
    # 2 — drop pure digits / hex blobs
    parts = [p for p in parts if p and not p.isdigit()]
    parts = [p for p in parts if not re.fullmatch(r"[a-f0-9]{6,}", p)]
    # 3 — drop tokens that contain digits
    parts = [p for p in parts if not any(ch.isdigit() for ch in p)]
    # 4 — drop trailing role / title tokens (one or two consecutive)
    while parts and parts[-1].lower() in _LINKEDIN_TRAILING_TOKENS:
        parts.pop()
    # 5 — final sanity
    if len(parts) < 2:
        return None
    if any(len(p) < 2 for p in parts[:4]):
        return None
    name = " ".join(p.capitalize() for p in parts[:4])
    return _clean_name(name)


def _extract_name_role_from_text(text: str) -> tuple[Optional[str], Optional[str]]:
    """Find the first plausible ``(name, role)`` pair in ``text``.

    Strategy : try the strict "Name, Role" pattern first ; fall back to a
    Role keyword + adjacent name token. Returns ``(None, None)`` when
    nothing solid is found.
    """
    if not text:
        return None, None
    t = re.sub(r"\s+", " ", text)
    m = _NAME_WITH_ROLE_RX.search(t)
    if m:
        name = _clean_name(m.group(1))
        role = re.sub(r"\s+", " ", m.group(2)).strip(" ,.;:-")
        if name:
            return name, role[:120] if role else None
    # Role keyword fallback : pick a Role match and look for the closest
    # capitalised bigram preceding it.
    rm = _ROLE_RX.search(t)
    if rm:
        before = t[max(0, rm.start() - 60): rm.start()]
        nm = list(_NAME_RX.finditer(before))
        if nm:
            name = _clean_name(nm[-1].group(1))
            if name:
                return name, rm.group(1)
    return None, None


def _build_country_lookup() -> dict[str, tuple[str, Optional[str]]]:
    """Map of ``normalised exhibitor name`` → ``(country_name, country_iso2)``.

    Used to fill the country of a signal whose canonical company matches
    one of our catalog exhibitors.
    """
    s = SessionLocal()
    try:
        rows = s.execute(
            select(Exhibitor.company_name, Exhibitor.country_name,
                    Exhibitor.country_iso2)
        ).all()
    finally:
        s.close()
    out: dict[str, tuple[str, Optional[str]]] = {}
    for name, country, iso2 in rows:
        if not name or not country:
            continue
        norm = " ".join(str(name).lower().split())
        out[norm] = (country, iso2)
    return out


# ---------------------------------------------------------------------------
# Phase A : country fill from catalog (instant, no network)
# ---------------------------------------------------------------------------


def _fill_country_from_catalog() -> int:
    """Copy ``country_name`` / ``country_iso2`` from the matching exhibitor
    onto each signal that has a company but no country. Returns number
    of rows updated.
    """
    lookup = _build_country_lookup()
    if not lookup:
        return 0
    s = SessionLocal()
    try:
        candidates = s.execute(
            select(AttendanceSignal).where(
                AttendanceSignal.country.is_(None),
                AttendanceSignal.canonical_company_name.is_not(None),
            )
        ).scalars().all()
        n = 0
        for sig in candidates:
            key = " ".join(
                str(sig.canonical_company_name or "").lower().split()
            )
            hit = lookup.get(key)
            if hit is None and sig.company_name:
                hit = lookup.get(
                    " ".join(str(sig.company_name).lower().split())
                )
            if hit is None:
                # Fuzzy substring fallback: signal_company contains catalog name
                # (or vice versa) — only when one side is at least 4 chars.
                for cat_name, payload in lookup.items():
                    if len(cat_name) < 4:
                        continue
                    if cat_name in key or key in cat_name:
                        hit = payload
                        break
            if hit is None:
                continue
            country, iso2 = hit
            sig.country = country
            if iso2:
                sig.country_iso2 = iso2
            n += 1
        s.commit()
        return n
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Phase B : person + role extraction from already-stored text
# ---------------------------------------------------------------------------


# LinkedIn / X title patterns we can reliably parse :
#  "John Smith - VP Sales chez ACME | LinkedIn"
#  "John Smith on LinkedIn: ..."
#  "John Smith - VP Sales at ACME"
_LINKEDIN_TITLE_RX = re.compile(
    r"^\s*(?P<name>[^\-|]+?)"
    r"\s*[\-–—]\s*(?P<role>[^|]+?)"
    r"(?:\s+(?:chez|at|@)\s+(?P<company>[^|]+?))?"
    r"\s*(?:\||$)",
    re.IGNORECASE,
)


def _extract_linkedin_company(title: Optional[str]) -> Optional[str]:
    """Extract the company from a LinkedIn-formatted page title."""
    if not title:
        return None
    m = _LINKEDIN_TITLE_RX.search(title)
    if not m:
        return None
    company = m.group("company")
    if not company:
        return None
    company = re.sub(r"\s+", " ", company).strip(" -–—|")
    if len(company) < 2 or len(company) > 120:
        return None
    return company


def _fill_company_from_stored_text() -> int:
    """For signals that are missing ``company_name`` (typically cleared
    LinkedIn-as-company rows) try to extract the real employer from the
    stored ``source_title`` / ``source_snippet`` / ``signal_text``.
    """
    s = SessionLocal()
    try:
        candidates = s.execute(
            select(AttendanceSignal).where(
                AttendanceSignal.company_name.is_(None),
            )
        ).scalars().all()
        n = 0
        for sig in candidates:
            company: Optional[str] = None
            # Try LinkedIn title pattern first
            if sig.source_url and "linkedin.com" in sig.source_url.lower():
                company = _extract_linkedin_company(sig.source_title)
            # Generic "<Role> at <Company>" / "<Role> chez <Company>"
            if not company:
                for field in (sig.source_title, sig.signal_text,
                              sig.source_snippet):
                    if not field:
                        continue
                    m = _ROLE_AT_COMPANY_RX.search(field)
                    if m:
                        company = re.sub(
                            r"\s+", " ", m.group(2)
                        ).strip(" .,;:|-")
                        if len(company) < 2 or len(company) > 120:
                            company = None
                        else:
                            break
            if not company:
                continue
            sig.company_name = company
            from app.attendance.dedup import canonical_company_name
            sig.canonical_company_name = canonical_company_name(company)
            n += 1
        s.commit()
        return n
    finally:
        s.close()


def _fill_person_from_stored_text() -> int:
    """For every signal missing ``person_name``, try to extract it from
    ``source_title`` + ``source_snippet`` + ``signal_text`` (already in DB)
    + LinkedIn URL slug. Updates ``person_name``, ``person_role``,
    ``canonical_person_name`` when a candidate is found.
    """
    s = SessionLocal()
    try:
        candidates = s.execute(
            select(AttendanceSignal).where(
                AttendanceSignal.person_name.is_(None),
            )
        ).scalars().all()
        n = 0
        for sig in candidates:
            name: Optional[str] = None
            role: Optional[str] = None
            # 1. Try the LinkedIn URL slug.
            if sig.source_url and "linkedin.com/in/" in sig.source_url.lower():
                name = _slug_to_name(sig.source_url)
            # 2. Try the stored text fields (most informative first).
            if not name:
                for field in (sig.signal_text, sig.source_snippet,
                              sig.source_title):
                    if not field:
                        continue
                    cand_name, cand_role = _extract_name_role_from_text(field)
                    if cand_name:
                        name, role = cand_name, role or cand_role
                        break
            if not name:
                continue
            sig.person_name = name
            if role and not sig.person_role:
                sig.person_role = role
            sig.canonical_person_name = canonical_person_name(name)
            # If we have a person, the entity is a person (was probably "company"
            # or "unknown" before).
            if sig.entity_type in ("unknown", "company"):
                sig.entity_type = "person"
            n += 1
        s.commit()
        return n
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Phase C : optional re-fetch of source URLs to enrich further
# ---------------------------------------------------------------------------


# Set via the CLI flag ``--ner`` — defaults to off because on press / event
# pages spaCy over-fires on product / vehicle / section names and inflates
# the noise. The regex path (Patterns A + B) stays on at all times.
_NER_ENABLED: bool = False


def set_ner_enabled(enabled: bool) -> None:
    """Toggle the NER pass. Used by the CLI."""
    global _NER_ENABLED
    _NER_ENABLED = bool(enabled)


def _extract_all_name_role_pairs(text: str, cap: int = 8) -> list[tuple[str, Optional[str]]]:
    """Find every plausible ``(name, role)`` pair in ``text``. Deduplicates
    by name (first occurrence wins). Returns up to ``cap`` pairs.

    Two patterns, both REQUIRE a role keyword to anchor the extraction —
    a pure capitalised bigram is not enough (way too many false positives
    from press page sidebars, ads, related-link blocks):

      A. ``"<Name>, <Role>"`` (post-comma title — corporate press style)
      B. ``"<Role> <Name>"`` (title precedes name — gov / military copy)
    """
    if not text:
        return []
    t = re.sub(r"\s+", " ", text)
    seen: dict[str, Optional[str]] = {}

    def _record(name: Optional[str], role: Optional[str]) -> None:
        if not name or name in seen:
            return
        seen[name] = (role[:120] if role else None)

    # A — name before role (e.g. "John Smith, CEO of MBDA")
    for m in _NAME_WITH_ROLE_RX.finditer(t):
        if len(seen) >= cap:
            break
        name = _clean_name(m.group(1))
        role = re.sub(r"\s+", " ", m.group(2)).strip(" ,.;:-")
        _record(name, role)

    # B — role before name (e.g. "Minister Florence Parly")
    for m in _TITLE_BEFORE_NAME_RX.finditer(t):
        if len(seen) >= cap:
            break
        name = _clean_name(m.group("name"))
        role = re.sub(r"\s+", " ", m.group("role")).strip(" ,.;:-")
        _record(name, role)

    return list(seen.items())


# Country mapping by TLD (cheap fallback when nothing else worked).
_TLD_TO_COUNTRY: dict[str, tuple[str, str]] = {
    "fr": ("France", "FR"),
    "de": ("Germany", "DE"),
    "uk": ("United Kingdom", "GB"),
    "it": ("Italy", "IT"),
    "es": ("Spain", "ES"),
    "be": ("Belgium", "BE"),
    "nl": ("Netherlands", "NL"),
    "pl": ("Poland", "PL"),
    "se": ("Sweden", "SE"),
    "fi": ("Finland", "FI"),
    "no": ("Norway", "NO"),
    "ch": ("Switzerland", "CH"),
    "at": ("Austria", "AT"),
    "pt": ("Portugal", "PT"),
    "il": ("Israel", "IL"),
    "tr": ("Turkey", "TR"),
    "us": ("United States", "US"),
    "ca": ("Canada", "CA"),
    "au": ("Australia", "AU"),
    "jp": ("Japan", "JP"),
    "kr": ("South Korea", "KR"),
}


def _country_from_url(url: str) -> Optional[tuple[str, str]]:
    """Cheap country guess from the URL host's TLD.

    Returns ``(country_name, iso2)`` or None. Skips ``.com`` / ``.org`` etc.
    that don't carry a country signal.
    """
    if not url:
        return None
    host = urlsplit(url).netloc.lower()
    if "." not in host:
        return None
    tld = host.rsplit(".", 1)[-1]
    return _TLD_TO_COUNTRY.get(tld)


def _fill_country_from_url() -> int:
    """For every signal still without country, try to derive one from the
    source URL's TLD. Returns count.
    """
    s = SessionLocal()
    try:
        candidates = s.execute(
            select(AttendanceSignal).where(
                AttendanceSignal.country.is_(None),
                AttendanceSignal.source_url.is_not(None),
            )
        ).scalars().all()
        n = 0
        for sig in candidates:
            hit = _country_from_url(sig.source_url)
            if hit is None:
                continue
            country, iso2 = hit
            sig.country = country
            sig.country_iso2 = iso2
            n += 1
        s.commit()
        return n
    finally:
        s.close()


async def _refetch_one(client: HttpClient, sig_id: int) -> int:
    """Re-fetch the source URL of a signal and try to enrich.

    Returns the number of database changes (0 if nothing extracted, 1 if
    the original signal got an update, plus +1 for each NEW signal
    derived when multiple distinct ``(name, role)`` pairs were found in
    the article body).
    """
    s = SessionLocal()
    try:
        sig = s.get(AttendanceSignal, sig_id)
        if sig is None or not sig.source_url:
            return 0
        url = sig.source_url
        # Snapshot the seed properties we'll reuse for any spawn signal.
        seed = {
            "edition_year": sig.edition_year,
            "country": sig.country,
            "country_iso2": sig.country_iso2,
            "source_platform": sig.source_platform,
            "signal_type": sig.signal_type,
            "search_query_used": sig.search_query_used,
        }
    finally:
        s.close()
    try:
        r = await client.request("GET", url)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"refetch {url} failed: {e!r}")
        return 0
    if r.status_code >= 400:
        return 0
    html = r.text or ""
    if not html:
        return 0
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return 0
    title = None
    t = tree.css_first("title")
    if t and t.text():
        title = t.text(strip=True)
    og_title = tree.css_first('meta[property="og:title"]')
    if og_title and og_title.attributes.get("content"):
        title = og_title.attributes["content"].strip()

    for n in tree.css("script, style, noscript"):
        n.decompose()
    visible = re.sub(r"\s+", " ", tree.text(separator=" ") or "").strip()

    # Scan the FULL article body — press pieces are typically 3k-15k chars
    # and visitor names often appear deep in the text (quotes, captions,
    # author bios skipped via the noscript/style strip earlier).
    full_text = " ".join(filter(None, [title, visible[:30000]]))

    # Hybrid extraction : regex first (high precision, low recall) then
    # spaCy NER (higher recall, very noisy on press / event sites). NER
    # is gated by ``ENABLE_NER`` because on defense press articles it
    # over-fires on vehicle / system names, navigation tokens, etc. ;
    # the role-AND-named-company rule below clamps the false-positive
    # rate but doesn't eliminate it.
    pairs: list[tuple[str, Optional[str]]] = _extract_all_name_role_pairs(
        full_text, cap=12,
    )
    ner_results: list[dict] = []
    if _NER_ENABLED:
        try:
            from app.attendance.ner_extract import extract_persons_with_context
            ner_results = extract_persons_with_context(
                full_text, max_persons=20,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"NER extraction failed for {url}: {e!r}")

    # Merge NER hits — strictest filter : require BOTH a role AND a
    # company in the NER context. This preserves "Florence Parly /
    # Minister / Ministry of the Armed Forces" style hits while killing
    # bare PERSON mentions that drag in vehicle / system names.
    seen_names = {n for n, _ in pairs}
    extra_ner: list[dict] = []
    for ne in ner_results:
        if ne["name"] in seen_names:
            continue
        if not (ne["role"] and ne["company"]):
            continue
        pairs.append((ne["name"], ne["role"]))
        extra_ner.append(ne)
        seen_names.add(ne["name"])

    company = None
    if "linkedin.com" in url.lower():
        company = _extract_linkedin_company(title)

    if not pairs and not company and not title:
        return 0

    changes = 0
    s = SessionLocal()
    try:
        sig = s.get(AttendanceSignal, sig_id)
        if sig is None:
            return 0
        # 1. Update the seed signal with the FIRST pair (and other fields).
        first_name = pairs[0][0] if pairs else None
        first_role = pairs[0][1] if pairs else None
        seed_changed = False
        if not sig.person_name and first_name:
            sig.person_name = first_name
            sig.canonical_person_name = canonical_person_name(first_name)
            if sig.entity_type in ("unknown", "company", "media"):
                sig.entity_type = "person"
            seed_changed = True
        if not sig.person_role and first_role:
            sig.person_role = first_role
            seed_changed = True
        if not sig.source_title and title:
            sig.source_title = title[:300]
            seed_changed = True
        if not sig.company_name and company:
            sig.company_name = company
            from app.attendance.dedup import canonical_company_name
            sig.canonical_company_name = canonical_company_name(company)
            seed_changed = True
        if seed_changed:
            changes += 1
        s.commit()
    finally:
        s.close()

    # 2. Spawn additional signals for the *other* name+role pairs found in
    # the same article (so a press piece naming 4 visitors → 4 signals).
    # NER hits also bring company / country context when available.
    ner_by_name = {ne["name"]: ne for ne in extra_ner}
    for name, role in pairs[1:]:
        ne = ner_by_name.get(name)
        try:
            spawn = RawSignal(
                edition_year=int(seed["edition_year"] or 2026),
                entity_type="person",
                source_url=url,
                person_name=name,
                person_role=role,
                company_name=(ne["company"] if ne else None),
                country=(ne["country"] if ne else seed["country"]),
                country_iso2=(None if ne and ne["country"] else seed["country_iso2"]),
                source_platform=seed["source_platform"],
                source_title=title,
                source_snippet=full_text[:280],
                search_query_used=(seed["search_query_used"] or "")
                + " (multi-extract)",
                signal_type=seed["signal_type"] or "media_article",
                signal_text=full_text[:280],
                notes=f"Spawn from article #{sig_id} "
                f"({'NER' if ne else 'regex'} extract).",
            )
            _, is_new = upsert_signal(spawn)
            if is_new:
                changes += 1
        except Exception as e:  # noqa: BLE001
            logger.debug(f"spawn upsert failed for {name!r}: {e!r}")
    return changes


async def _refetch_async(limit: int = 200) -> int:
    """Re-fetch up to ``limit`` signals still missing a person name OR a
    company name. The fresh HTML is parsed for both.
    """
    from sqlalchemy import or_
    s = SessionLocal()
    try:
        ids = [
            sid
            for (sid,) in s.execute(
                select(AttendanceSignal.id)
                .where(
                    or_(
                        AttendanceSignal.person_name.is_(None),
                        AttendanceSignal.company_name.is_(None),
                    ),
                    AttendanceSignal.source_url.is_not(None),
                )
                .order_by(AttendanceSignal.id.desc())
                .limit(limit)
            ).all()
        ]
    finally:
        s.close()
    if not ids:
        return 0
    async with HttpClient(
        per_host_delay=0.1, concurrency=10, timeout=10, max_retries=1,
    ) as c:
        results = await asyncio.gather(*[_refetch_one(c, i) for i in ids])
    return int(sum(results))


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


@dataclass
class EnrichResult:
    country_filled: int
    company_filled: int
    person_filled: int
    person_filled_refetch: int

    def to_dict(self) -> dict:
        return {
            "country_filled": self.country_filled,
            "company_filled": self.company_filled,
            "person_filled": self.person_filled,
            "person_filled_refetch": self.person_filled_refetch,
        }


def enrich_signals(
    *,
    include_refetch: bool = False,
    refetch_limit: int = 200,
) -> EnrichResult:
    """Run all phases. ``include_refetch=True`` also re-fetches source URLs
    for signals still missing a person name (capped at ``refetch_limit``).
    """
    a_catalog = _fill_country_from_catalog()
    a_tld = _fill_country_from_url()
    b = _fill_company_from_stored_text()
    c = _fill_person_from_stored_text()
    d = 0
    if include_refetch:
        d = asyncio.run(_refetch_async(limit=refetch_limit))
    return EnrichResult(
        country_filled=a_catalog + a_tld,
        company_filled=b, person_filled=c,
        person_filled_refetch=d,
    )
