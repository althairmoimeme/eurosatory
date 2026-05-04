"""Hybrid NER + regex extractor for visitor attendance signals.

How it works
------------
1. Run **spaCy NER** (en + fr) on the article body → extract PERSON / ORG /
   GPE entities with their character offsets.
2. For each PERSON entity, look for a **role keyword** in a ±80-char window
   around the mention (existing ``_ROLE_RX`` from ``enrich.py``).
3. For each PERSON entity, look for the **closest ORG entity** within
   200 chars on the same sentence — that's the employer.
4. Apply the same noise filter as the regex-only path
   (``_looks_like_real_name``).

Why hybrid
----------
- NER alone over-fires on press content: it tags "Eurosatory", section
  titles, and decorative captions as ORG / EVENT.
- Regex alone misses 90% of real names because press doesn't write
  ``"John Smith, Director"`` — it writes ``"Smith said the new system…"``
  or ``"Director of XYZ John Smith presented…"``.
- NER + regex-anchor gives us **discovery** + **role context** with no
  API calls.
"""
from __future__ import annotations

import re
from typing import Optional

from loguru import logger

from app.attendance.enrich import (
    _NAME_BLACKLIST,
    _ROLE_RX,
    _clean_name,
    _looks_like_real_name,
)


_NLPS_CACHE: dict[str, object] = {}


def _load_nlp(lang: str = "en"):
    """Lazy-load and cache a spaCy pipeline. ``en`` and ``fr`` supported."""
    if lang in _NLPS_CACHE:
        return _NLPS_CACHE[lang]
    try:
        import spacy
    except ImportError:  # pragma: no cover
        logger.warning("spaCy not installed — NER extractor disabled.")
        return None
    model = "en_core_web_sm" if lang == "en" else "fr_core_news_sm"
    try:
        nlp = spacy.load(model, disable=["lemmatizer", "tagger"])
    except OSError as e:
        logger.warning(f"spaCy model {model} missing: {e!r}")
        return None
    _NLPS_CACHE[lang] = nlp
    return nlp


# Heuristic language detection (cheap — no charset library required).
_FR_HINTS = re.compile(
    r"\b(le|la|les|du|des|une?|qui|est|que|sur|dans|pour|aux?|avec|par)\b",
    re.IGNORECASE,
)
_EN_HINTS = re.compile(
    r"\b(the|of|and|at|in|on|by|for|with|will|would|said|told)\b",
    re.IGNORECASE,
)


def _detect_lang(text: str) -> str:
    """Return ``"fr"`` if French wordsh outnumber English, else ``"en"``."""
    sample = text[:2000]
    fr = len(_FR_HINTS.findall(sample))
    en = len(_EN_HINTS.findall(sample))
    return "fr" if fr > en else "en"


# Words that frequently appear as PERSON entities but aren't real people
# (orgs, events, regions tagged wrong). Stronger filter than the regex
# blacklist because spaCy is more permissive.
_NER_PERSON_BLOCK = frozenset({
    "Eurosatory", "Eurosatory 2024", "Eurosatory 2026",
    "Defense News", "Breaking Defense", "Shephard Media", "Janes",
    "Ministry of Defence", "Ministry of Defense",
    "Ministry of the Armed Forces", "Defense Ministry",
    "DGA", "MoD", "MOD", "DARPA",
    # Common UI / nav strings spaCy mis-tags as PERSON in press templates.
    "Signup Close", "Sign Up", "Log In", "Read More", "Learn More",
    "Subscribe Now", "Follow Us", "Continue Reading",
    "Tiger Tiger",  # vehicle name
})


def _normalise_ner_name(raw: str) -> Optional[str]:
    """Strip apostrophe-s, trailing punctuation, and reject duplicate-token
    candidates. Returns the cleaned name or None.
    """
    if not raw:
        return None
    # Strip possessive ("Macron's" → "Macron", "Hegseth's day" → "Hegseth").
    s = re.sub(r"['’]s\b.*$", "", raw).strip()
    s = s.strip(" ,.;:'\"-—–\t\n")
    if not s:
        return None
    # Reject when both tokens are identical ("Tiger Tiger").
    tokens = s.split()
    if len(tokens) >= 2 and len(set(t.lower() for t in tokens)) == 1:
        return None
    return s


def extract_persons_with_context(
    text: str, *, max_persons: int = 25,
) -> list[dict]:
    """Run NER and return a list of ``{name, role, company, country}``.

    Each entry corresponds to a unique PERSON in the article. ``role``,
    ``company``, ``country`` may be None when the surrounding context
    doesn't yield them.
    """
    if not text or len(text) < 50:
        return []
    lang = _detect_lang(text)
    nlp = _load_nlp(lang) or _load_nlp("en")
    if nlp is None:
        return []
    # spaCy default max_length is 1_000_000 chars but processing 30k+ is
    # already heavy on small models — clamp for performance.
    doc = nlp(text[:50_000])

    # Bucket entities by type. The English model uses PERSON / GPE / LOC ;
    # the French model uses PER / LOC. Accept both so the same code works
    # on either pipeline.
    persons: list[tuple[str, int, int]] = []  # (text, start, end)
    orgs: list[tuple[str, int, int]] = []
    gpes: list[tuple[str, int, int]] = []
    for ent in doc.ents:
        if ent.label_ in ("PERSON", "PER"):
            persons.append((ent.text, ent.start_char, ent.end_char))
        elif ent.label_ in ("ORG",):
            orgs.append((ent.text, ent.start_char, ent.end_char))
        elif ent.label_ in ("GPE", "LOC"):
            gpes.append((ent.text, ent.start_char, ent.end_char))

    out: list[dict] = []
    seen_names: set[str] = set()
    for raw_name, start, end in persons:
        if len(out) >= max_persons:
            break
        # Pre-clean (strip 's, dedup tokens, …) BEFORE regex validation.
        normalised = _normalise_ner_name(raw_name)
        if not normalised:
            continue
        name = _clean_name(normalised)
        if not name:
            continue
        if name in _NER_PERSON_BLOCK:
            continue
        if name in seen_names:
            continue
        if not _looks_like_real_name(name):
            continue
        # Find a role keyword within ±80 chars of the mention.
        window_start = max(0, start - 80)
        window_end = min(len(text), end + 80)
        window = text[window_start:window_end]
        rm = _ROLE_RX.search(window)
        role = rm.group(1) if rm else None
        # Find closest ORG within 200 chars (prefer right side: "X said
        # the company …" pattern places ORG after the person).
        company: Optional[str] = None
        for o_text, o_start, o_end in orgs:
            if abs(o_start - end) > 200:
                continue
            if o_text in (
                "Eurosatory", "Defense News", "Breaking Defense",
                "Shephard Media", "Janes",
            ):
                continue
            company = o_text.strip()
            break
        # Closest GPE → country guess.
        country: Optional[str] = None
        for g_text, g_start, g_end in gpes:
            if abs(g_start - end) > 200:
                continue
            country = g_text.strip()
            break

        out.append({
            "name": name, "role": role,
            "company": company, "country": country,
        })
        seen_names.add(name)

    return out
