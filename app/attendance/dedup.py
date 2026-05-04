"""Deduplication for ``AttendanceSignal`` rows.

We never delete duplicates — we mark them.  Keeping every signal lets a sales
operator audit *all* the public material backing the canonical record.

Strategy
--------
* Compute a stable ``dedupe_key`` from
  ``(canonical_person_name, canonical_company_name, edition_year)``.
* The first row inserted with a given key becomes the canonical record (that
  one stays ``is_duplicate = False``).  Subsequent rows with the same key
  share its id via ``duplicate_group_id`` and are marked
  ``is_duplicate = True``.
* When the operator imports a higher-confidence signal for an existing key,
  we re-elect the canonical: the signal with the highest ``presence_score``
  wins; ties broken by lowest ``id`` (oldest).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Optional

# Suffixes typical of legal company names — stripped during canonicalisation.
COMPANY_SUFFIXES = [
    "inc", "incorporated", "corp", "corporation", "co", "company", "llc",
    "ltd", "limited", "plc", "gmbh", "ag", "ab", "as", "oy", "sa", "spa",
    "bv", "kft", "sas", "sarl", "srl", "sl", "uab", "ou", "doo", "kg",
    "pty", "se", "nv", "spzoo", "pl", "kk",
]
COMPANY_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in COMPANY_SUFFIXES) + r")\b\.?$",
    re.IGNORECASE,
)

# Common name prefixes / titles to strip from person names
PERSON_TITLE_RE = re.compile(
    r"^\s*(?:mr|mrs|ms|miss|dr|prof|col|lt|gen|capt|maj|adm)\.?\s+",
    re.IGNORECASE,
)


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def canonical_company_name(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = _strip_accents(raw).lower().strip()
    s = re.sub(r"[“”\"'`’]", "", s)
    s = re.sub(r"[,;]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # strip common trailing legal suffix tokens
    parts = s.split()
    while parts and COMPANY_SUFFIX_RE.match(parts[-1]):
        parts.pop()
    s = " ".join(parts)
    s = re.sub(r"[^\w\s\-&]", "", s)
    return s.strip() or None


def canonical_person_name(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = _strip_accents(raw).lower().strip()
    s = PERSON_TITLE_RE.sub("", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z\-\s]", "", s)
    return s.strip() or None


def dedupe_key(
    *,
    canonical_person: Optional[str],
    canonical_company: Optional[str],
    edition_year: Optional[int],
) -> str:
    """Stable key for matching duplicates.

    For *people*: keyed on (person, year) — same person posting from two
    different sources is one record.
    For *companies / delegations*: keyed on (company, year).
    """
    parts = [
        canonical_person or "",
        canonical_company or "",
        str(edition_year or 0),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
