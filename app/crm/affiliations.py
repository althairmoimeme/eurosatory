"""Detect *industry associations* (trade groups) and *corporate
affiliations* (parent group, subsidiary status) from the company's own pages.

Why these matter
----------------
- **Trade associations** signal active participation in the defense ecosystem
  and let buyers filter by alliance (e.g. ASD members are EU defence supply
  chain insiders). Membership in NDIA/AIA = qualified US defence player.
- **Parent group** identifies the ultimate decision-maker. A French SME
  flagged as "subsidiary of Thales" tells a sales rep the buying authority
  sits at the parent level.

Both are regex-based, deterministic, conservative.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.database import CrawledPage
from app.processors.intelligence import _looks_like_keyword_stuffing

# ---------------------------------------------------------------------------
# Trade associations / alliances — exact tokens or full names
# ---------------------------------------------------------------------------

ASSOCIATIONS: dict[str, list[str]] = {
    # Europe
    "ASD (AeroSpace and Defence Europe)": [
        r"\bASD(?:[-\s]?Europe)?\b\s*(?:member|membre)?",
        r"AeroSpace and Defence Industries Association of Europe",
    ],
    "EDA (European Defence Agency)": [r"\bEuropean Defence Agency\b", r"\bEDA\s+(?:project|partner|funded)"],
    "EUROSPACE": [r"\bEUROSPACE\b"],
    # France
    "GIFAS": [r"\bGIFAS\b", r"\bGroupement des Industries Fran[cç]aises A[ée]ronautiques"],
    "GICAT": [r"\bGICAT\b", r"\bGroupement des Industries Fran[cç]aises de D[ée]fense Terrestre"],
    "GICAN": [r"\bGICAN\b", r"\bGroupement des Industries de Construction et Activit[ée]s Navales"],
    "CIDEF": [r"\bCIDEF\b"],
    "COFIS": [r"\bCOFIS\b"],
    # UK
    "ADS Group": [r"\bADS Group\b", r"\bA member of ADS\b"],
    "techUK": [r"\btechUK\b"],
    # Germany
    "BDLI": [r"\bBDLI\b", r"\bBundesverband der Deutschen Luft-?\s*und Raumfahrtindustrie\b"],
    "BDSV": [r"\bBDSV\b", r"\bBundesverband der Deutschen Sicherheits-?\s*und Verteidigungsindustrie\b"],
    # Italy
    "AIAD": [r"\bAIAD\b", r"\bFederazione Aziende Italiane per l'Aerospazio\b"],
    # Spain
    "TEDAE": [r"\bTEDAE\b"],
    # Sweden
    "SOFF": [r"\bSOFF\b", r"\bSecurity and Defence Industry Association\b"],
    # Belgium / NL
    "BSDI": [r"\bBSDI\b", r"\bBelgian Security and Defence Industry\b"],
    "NIDV": [r"\bNIDV\b"],
    # USA
    "NDIA": [r"\bNDIA\b", r"\bNational Defense Industrial Association\b"],
    "AIA": [r"\bAerospace Industries Association\b"],
    "AUSA": [r"\bAUSA\b", r"\bAssociation of the United States Army\b"],
    "AUVSI": [r"\bAUVSI\b"],
    "NATO Industry Forum": [r"\bNATO Industry Forum\b"],
    # Cybersecurity
    "ECSO (European Cyber Security Organisation)": [r"\bECSO\b", r"\bEuropean Cyber Security Organisation\b"],
    "Hexatrust": [r"\bHexatrust\b"],
}

_COMPILED_ASSOC = {
    label: [re.compile(p, re.IGNORECASE) for p in patterns]
    for label, patterns in ASSOCIATIONS.items()
}


def find_associations_in_text(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for label, patterns in _COMPILED_ASSOC.items():
        for pat in patterns:
            if pat.search(text):
                if label not in found:
                    found.append(label)
                break
    return found


def extract_associations(session, exhibitor_id: int) -> list[str]:
    pages = list(
        session.execute(
            select(CrawledPage).where(
                CrawledPage.exhibitor_id == exhibitor_id,
                CrawledPage.text_excerpt.is_not(None),
            )
        ).scalars()
    )
    out: list[str] = []
    for p in pages:
        for label in find_associations_in_text(p.text_excerpt or ""):
            if label not in out:
                out.append(label)
    return out


# ---------------------------------------------------------------------------
# Parent group / subsidiary status
# ---------------------------------------------------------------------------

# Match phrases like "subsidiary of X", "part of the X Group", "a Y company",
# "owned by Z", "member of the X group". The captured token is the parent.
_PARENT_RES = [
    re.compile(
        r"\b(?:wholly[\-\s]owned\s+)?subsidiary\s+of\s+(?:the\s+)?([A-Z][A-Za-z0-9 &\-\.]{2,60}?)(?:\s+(?:group|holdings?|corporation|company|companies|inc\.?|ltd\.?|gmbh|sa|spa|plc|nv|ag|bv))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:part of|member of)\s+(?:the\s+)?([A-Z][A-Za-z0-9 &\-\.]{2,60}?)\s+(?:group|holdings?|family|corporation)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bowned by\s+(?:the\s+)?([A-Z][A-Za-z0-9 &\-\.]{2,60}?)(?:\s+(?:group|holdings?|family|corporation))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\ba\s+([A-Z][A-Za-z0-9 &\-\.]{2,60}?)\s+(?:group|holdings?)\s+company\b",
        re.IGNORECASE,
    ),
]

# Common false-positives we want to avoid (when match is a generic phrase).
_PARENT_BLACKLIST = {
    "the company", "our company", "the world", "our team", "our group",
    "a global", "a leading", "a french", "the french", "the european",
    "the same", "this company", "a company", "an independent",
    "the year", "the project", "our", "we", "us", "their", "his", "her",
    "its", "you", "your", "they", "them", "the", "this", "that",
    "a", "an", "all", "some", "many", "most", "more", "less",
}


def _clean_parent(text: str) -> Optional[str]:
    parent = re.sub(r"\s+", " ", text).strip(" .,:;-")
    if not parent or len(parent) < 4 or len(parent) > 80:
        return None
    if parent.lower() in _PARENT_BLACKLIST:
        return None
    # Pronouns or single-word lowercase tokens are almost always false matches.
    if " " not in parent and parent[0].islower():
        return None
    return parent


def find_parent_in_text(text: str) -> Optional[str]:
    if not text or _looks_like_keyword_stuffing(text):
        return None
    for regex in _PARENT_RES:
        m = regex.search(text)
        if not m:
            continue
        parent = _clean_parent(m.group(1))
        if parent:
            return parent
    return None


def _looks_self_reference(parent: str, company_name: Optional[str]) -> bool:
    """Reject when the regex captured the company's own name (or a fragment).

    Common false-positive: "ArianeGroup is a leader" → match "ArianeGroup …".
    """
    if not parent or not company_name:
        return False
    p_norm = re.sub(r"[^a-z]", "", parent.lower())
    c_norm = re.sub(r"[^a-z]", "", company_name.lower())
    if not p_norm or not c_norm:
        return False
    # exact or substring overlap of 6+ chars
    if p_norm == c_norm:
        return True
    return len(p_norm) >= 6 and (p_norm in c_norm or c_norm[: len(p_norm)] == p_norm)


def extract_parent_group(session, exhibitor_id: int, company_name: Optional[str] = None) -> Optional[str]:
    pages = list(
        session.execute(
            select(CrawledPage).where(
                CrawledPage.exhibitor_id == exhibitor_id,
                CrawledPage.kind != "pdf",
                CrawledPage.text_excerpt.is_not(None),
            )
        ).scalars()
    )
    pages.sort(key=lambda p: 0 if p.kind == "about" else 1 if p.kind == "homepage" else 2)
    for p in pages:
        parent = find_parent_in_text(p.text_excerpt or "")
        if not parent:
            continue
        if _looks_self_reference(parent, company_name):
            continue
        return parent
    return None


# ---------------------------------------------------------------------------
# Additional offices — list of cities / countries mentioned beyond HQ
# ---------------------------------------------------------------------------

_OFFICES_RES = [
    re.compile(
        r"\b(?:offices? in|presence in|sites? in|locations? in|"
        r"bureaux\s+(?:[ÀaA]\s+)?|implant[ée]e?\s+[ÀaA]\s+)"
        r"([A-Z][A-Za-z\u00C0-\u017F]+(?:\s*,\s*[A-Z][A-Za-z\u00C0-\u017F]+){0,9})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:headquartered\s+in|si[èe]ge\s+(?:social\s+)?(?:[ÀaA]\s+)?)"
        r"([A-Z][A-Za-z\u00C0-\u017F]+(?:\s*,\s*[A-Z][A-Za-z\u00C0-\u017F]+){0,3})",
        re.IGNORECASE,
    ),
]

_OFFICE_NOISE = {
    "the world", "our team", "the company", "this company",
    "the field", "the industry", "the country", "the region",
    "the project", "the year", "the centre", "the center",
}


def find_offices_in_text(text: str) -> list[str]:
    if not text or _looks_like_keyword_stuffing(text):
        return []
    out: list[str] = []
    for regex in _OFFICES_RES:
        for m in regex.finditer(text):
            chunk = re.split(r"\s+(?:and|et)\s+", m.group(1))[0]
            for piece in chunk.split(","):
                piece = piece.strip(" .,:;-")
                if not piece or len(piece) < 3 or len(piece) > 50:
                    continue
                if piece.lower() in _OFFICE_NOISE:
                    continue
                if piece not in out:
                    out.append(piece)
    return out[:10]


def extract_offices(session, exhibitor_id: int) -> list[str]:
    pages = list(
        session.execute(
            select(CrawledPage).where(
                CrawledPage.exhibitor_id == exhibitor_id,
                CrawledPage.kind != "pdf",
                CrawledPage.text_excerpt.is_not(None),
            )
        ).scalars()
    )
    pages.sort(key=lambda p: 0 if p.kind in {"about", "contact"} else 1 if p.kind == "homepage" else 2)
    out: list[str] = []
    for p in pages:
        for o in find_offices_in_text(p.text_excerpt or ""):
            if o not in out:
                out.append(o)
        if len(out) >= 8:
            break
    return out[:8]
