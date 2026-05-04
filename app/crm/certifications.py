"""Detect public defense / industrial certifications mentioned on a company's
own pages.

Defense procurement filters heavily on these — having them surfaced in the
fiche lets a buyer immediately see who's qualified for which programmes
(NATO supply chains, US export control, EU defence funds, automotive defense
crossovers).

Heuristics
----------
For each canonical certification we keep a list of regex patterns that should
appear *literally* in the company's own text.  We do NOT match generic words
like "quality" — only well-known certification IDs that companies advertise
verbatim.

Detection is conservative: each match keeps the source URL so a sales rep
can audit. False positives are rare because these IDs are very specific.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select

from app.database import CrawledPage

# Order matters — labels are listed once, more specific patterns first to
# avoid double-counting (AS9100D matches AS9100, etc.).
CERTIFICATIONS: dict[str, list[str]] = {
    # Aerospace & defense quality
    "AS9100":      [r"\bAS\s?9100[A-Z]?\b"],
    "EN 9100":     [r"\bEN\s?9100[A-Z]?\b"],
    "EN 9110":     [r"\bEN\s?9110\b"],
    "EN 9120":     [r"\bEN\s?9120\b"],
    "ISO 9001":    [r"\bISO\s?9001(?::\d{4})?\b"],
    "ISO 14001":   [r"\bISO\s?14001(?::\d{4})?\b"],
    "ISO 45001":   [r"\bISO\s?45001(?::\d{4})?\b"],

    # Cybersecurity
    "ISO 27001":   [r"\bISO\s?27001(?::\d{4})?\b"],
    "ISO 27017":   [r"\bISO\s?27017\b"],
    "ISO 27018":   [r"\bISO\s?27018\b"],
    "SOC 2":       [r"\bSOC\s?2\b", r"\bSOC2\b"],
    "FIPS 140":    [r"\bFIPS\s?140(?:-2|-3)?\b"],
    "Common Criteria": [r"\bCommon Criteria\b", r"\bEAL\s?\d\b"],
    "NIST 800-171": [r"\bNIST\s?(?:SP\s?)?800-171\b"],
    "CMMC":        [r"\bCMMC(?:\s?(?:Level\s?\d|L\d))?\b"],

    # Defense / NATO
    "NATO AQAP 2110": [r"\bAQAP\s?2110\b"],
    "NATO AQAP 2210": [r"\bAQAP\s?2210\b"],
    "NATO AQAP":   [r"\bAQAP\s?\d+\b"],
    "NCAGE code":  [r"\bNCAGE\b"],
    "Q+ NATO":     [r"\bNATO\s?Q\+\b"],

    # Export control
    "ITAR":        [r"\bITAR(?:\s?compliant|\s?registered)?\b"],
    "EAR":         [r"\bEAR(?:\s?99)?\b"],
    "OFAC":        [r"\bOFAC\b"],
    "Wassenaar":   [r"\bWassenaar\b"],

    # Industrial
    "IATF 16949": [r"\bIATF\s?16949\b"],
    "ISO/TS 16949": [r"\bISO/TS\s?16949\b"],
    "ISO 13485":  [r"\bISO\s?13485\b"],
    "ECSS":       [r"\bECSS-?[QEM]?-?\d+\b"],

    # French national
    "France Habilitation Confidentiel Défense": [r"\bConfidentiel\s+D[ée]fense\b"],
    "France Secret Défense": [r"\bSecret\s+D[ée]fense\b"],
    "DGA Qualification": [r"\bDGA\s?(?:Qualified|Qualifi[ée])\b"],

    # German national
    "BSI": [r"\bBSI[\-\s]?(?:Zertifizierung|Zertifikat|certified|certification)\b"],
    "VS-NfD": [r"\bVS-NfD\b"],

    # UK
    "Cyber Essentials": [r"\bCyber Essentials(?:\s+Plus)?\b"],
    "List X": [r"\bList\s?X\b"],
}

_COMPILED = {
    label: [re.compile(p, re.IGNORECASE) for p in patterns]
    for label, patterns in CERTIFICATIONS.items()
}


def find_certifications_in_text(text: str) -> list[str]:
    """Return the set of certification labels found in ``text``."""
    if not text:
        return []
    out: list[str] = []
    for label, patterns in _COMPILED.items():
        for pat in patterns:
            if pat.search(text):
                if label not in out:
                    out.append(label)
                break
    return out


def extract_certifications(session, exhibitor_id: int) -> list[str]:
    """Sweep every crawled page (HTML + PDF) for the exhibitor and return
    the deduped list of certifications detected.
    """
    pages = list(
        session.execute(
            select(CrawledPage).where(
                CrawledPage.exhibitor_id == exhibitor_id,
                CrawledPage.text_excerpt.is_not(None),
            )
        ).scalars()
    )
    found: list[str] = []
    for p in pages:
        for label in find_certifications_in_text(p.text_excerpt or ""):
            if label not in found:
                found.append(label)
    return found
