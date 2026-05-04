"""Scoring + categorisation engine for ``AttendanceSignal`` rows.

Pure-Python, deterministic, no LLM, no network.  Every output is justified by
``signal_strength_reason`` so a sales operator can audit each score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Role normalisation
# ---------------------------------------------------------------------------

# Order matters: more specific patterns first.
ROLE_PATTERNS: list[tuple[str, list[str]]] = [
    ("CEO / Founder", [
        r"\bCEO\b", r"\bChief Executive\b", r"\bFounder\b", r"\bCo[\-_ ]?Founder\b",
        r"\bManaging Director\b", r"\bMD\b", r"\bPresident\b", r"\bChairman\b",
        r"\bChairwoman\b", r"\bCOO\b", r"\bChief Operating\b",
    ]),
    ("Sales / Business Development", [
        r"\bsales\b", r"\bBusiness Development\b", r"\bBD Director\b", r"\bBD Manager\b",
        r"\bAccount Manager\b", r"\bAccount Executive\b", r"\bcommercial\b",
        r"\bGo[\-_ ]?to[\-_ ]?Market\b", r"\bChief Revenue\b", r"\bCRO\b",
    ]),
    ("Procurement / Purchasing", [
        r"\bprocurement\b", r"\bpurchasing\b", r"\bbuyer\b", r"\bsourcing\b",
        r"\bsupply chain\b", r"\bvendor management\b",
    ]),
    ("Partnerships", [
        r"\bpartnerships?\b", r"\balliances?\b", r"\bchannel\b",
    ]),
    ("Program / Project Manager", [
        r"\bprogram(me)? manager\b", r"\bproject manager\b", r"\bprogram(me)? director\b",
        r"\bportfolio manager\b",
    ]),
    ("Defense / Military", [
        r"\bmilitary\b", r"\barmed forces\b", r"\bcommand\b",
        r"\bcolonel\b", r"\bgeneral\b", r"\bcaptain\b", r"\bmajor\b", r"\badmiral\b",
        r"\bbrigadier\b", r"\blieutenant\b",
        # generic "defense / defence" — placed last in this group so that more
        # specific patterns elsewhere (e.g. Media / Press for "defense reporter")
        # can be tried via separate ordering rules.
        r"\bdefen[sc]e\b",
    ]),
    ("Engineering / Technical", [
        r"\bCTO\b", r"\bChief Technology\b", r"\bengineer\b", r"\barchitect\b",
        r"\btechnical\b", r"\bR&D\b", r"\bresearch\b", r"\bproduct manager\b",
    ]),
    ("Marketing / Communications", [
        r"\bmarketing\b", r"\bcommunications?\b", r"\bbrand\b", r"\bPR\b",
        r"\bpublic relations\b", r"\bgrowth\b",
    ]),
    ("Institutional / Government", [
        r"\bgovernment\b", r"\bministry\b", r"\bofficial\b", r"\bambassador\b",
        r"\battach[eé]\b", r"\bdiplomat\b", r"\bsecretary\b",
    ]),
    ("Media / Press", [
        r"\bjournalist\b", r"\breporter\b", r"\beditor\b", r"\bcorrespondent\b",
    ]),
]

UNKNOWN_ROLE = "Unknown"

# Roles that map to High commercial relevance
HIGH_RELEVANCE_ROLES = {
    "CEO / Founder",
    "Sales / Business Development",
    "Procurement / Purchasing",
    "Partnerships",
    "Program / Project Manager",
    "Defense / Military",
}
LOW_RELEVANCE_ROLES = {
    "Media / Press",
    UNKNOWN_ROLE,
}


def role_category(job_title: Optional[str]) -> str:
    """Map a free-text job title to one of the canonical role categories.

    Media / Press is checked first so titles like "Defense reporter" or
    "Defense correspondent" don't get mis-classified as "Defense / Military".
    """
    if not job_title:
        return UNKNOWN_ROLE
    text = job_title.lower()

    # Strong override: any media-press keyword wins regardless of other terms.
    media_patterns = next(p for label, p in ROLE_PATTERNS if label == "Media / Press")
    for pat in media_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return "Media / Press"

    for label, patterns in ROLE_PATTERNS:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return label
    return UNKNOWN_ROLE


# ---------------------------------------------------------------------------
# Presence score (0-100) — see spec
# ---------------------------------------------------------------------------

EXPLICIT_FUTURE_PATTERNS = [
    r"\bwill attend\b",                # covers "I/we/he/she/they/X will attend"
    r"\bwill be attending\b", r"\bare attending\b",
    r"\bwill exhibit\b", r"\bwill be exhibiting\b", r"\bare exhibiting\b",
    r"\bwill be present\b", r"\bwill be at Eurosatory\b",
    r"\bmeet us at booth\b", r"\bmeet us at our booth\b", r"\bmeet us at stand\b",
    r"\bvisit us at booth\b", r"\bvisit us at stand\b", r"\bvisit our booth\b",
    r"\bcome meet us\b", r"\bsee you at Eurosatory\b",
    r"\bI'll attend\b", r"\bwe'll attend\b",
]
PAST_PRESENCE_PATTERNS = [
    r"\bthank(s)? for visiting\b", r"\bthank you for visiting\b",
    r"\bgreat week\b", r"\bgreat show\b", r"\bgreat eurosatory\b",
    r"\bsuccessful eurosatory\b", r"\bwrapping up eurosatory\b",
    r"\bhappy to have presented\b", r"\bproud to have presented\b",
    r"\bback from eurosatory\b",
]
SHARE_REPOST_PATTERNS = [
    r"\brepost\b", r"\bshared\b", r"\bcheck out\b",
]


@dataclass
class PresenceScore:
    score: int
    reason: str


def compute_presence_score(
    *,
    signal_text: Optional[str],
    signal_type: Optional[str],
    person_name: Optional[str] = None,
    is_official_delegation: bool = False,
) -> PresenceScore:
    """Return ``(score, human-readable reason)``.

    The reason string is stored on the row as ``signal_strength_reason`` so
    the sales operator can see why the score landed where it did.
    """
    text = (signal_text or "").lower()

    # 1) explicit future attendance announcement
    for pat in EXPLICIT_FUTURE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return PresenceScore(95, f"Explicit attendance phrase: ‹{pat.strip(chr(92)+'b')[:40]}…›")

    # 2) past-presence proof (photo / booth / wrap-up post)
    for pat in PAST_PRESENCE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return PresenceScore(90, "Past-presence proof (wrap-up phrasing detected)")

    # 3) official delegation mention
    if is_official_delegation or signal_type == "official_delegation":
        return PresenceScore(80, "Official delegation mention")

    # 4) press release / fiable article
    if signal_type in ("press_release", "media_article"):
        return PresenceScore(78, f"Reliable source ({signal_type})")

    # 5) company announcement without identified person
    if signal_type == "company_announcement" and not person_name:
        return PresenceScore(65, "Company-only announcement, no individual named")

    # 6) social repost / share
    if signal_type == "social_post":
        for pat in SHARE_REPOST_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                return PresenceScore(45, "Repost / share without explicit attendance phrase")

    # 7) hashtag-only mention
    if "#eurosatory" in text and len(text) < 80:
        return PresenceScore(20, "Hashtag-only / very short mention")

    # 8) generic mention — score by signal_type
    fallback = {
        "event_page": 60,
        "national_pavilion": 70,
        "company_announcement": 55,
        "personal_linkedin_post": 50,
        "social_post": 35,
    }
    score = fallback.get(signal_type or "", 30)
    return PresenceScore(score, f"Generic mention (signal_type={signal_type or 'unknown'})")


def presence_confidence(score: float) -> str:
    if score is None:
        return "Low"
    if score >= 80:
        return "High"
    if score >= 50:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# Commercial relevance + sales priority + next best action
# ---------------------------------------------------------------------------


def commercial_relevance(role_cat: str, entity_type: str = "person") -> str:
    if entity_type in {"company", "delegation", "institution"}:
        # the relevance of the *entity* — anchor on entity rather than role
        return "Medium"
    if role_cat in HIGH_RELEVANCE_ROLES:
        return "High"
    if role_cat in LOW_RELEVANCE_ROLES:
        return "Low"
    return "Medium"


def sales_priority(confidence: str, relevance: str) -> str:
    """Spec:
    - A = High confidence + High relevance
    - B = High confidence  OR  High relevance
    - C = Medium confidence
    - D = Low confidence
    """
    if confidence == "High" and relevance == "High":
        return "A"
    if confidence == "High" or relevance == "High":
        return "B"
    if confidence == "Medium":
        return "C"
    return "D"


def meeting_potential(priority: str, role_cat: str) -> str:
    if priority in ("A",) and role_cat in HIGH_RELEVANCE_ROLES:
        return "High"
    if priority in ("A", "B"):
        return "Medium"
    return "Low"


def next_best_action(confidence: str, relevance: str, entity_type: str) -> str:
    if confidence == "High" and relevance == "High":
        return "Add to pre-show outreach list"
    if confidence == "High" and entity_type in {"company", "delegation", "institution"}:
        return "Find decision maker and qualify"
    if confidence == "Medium":
        return "Validate attendance manually"
    return "Keep in watchlist"


# ---------------------------------------------------------------------------
# GDPR risk + manual_validation_status
# ---------------------------------------------------------------------------


def gdpr_risk_level(entity_type: str, score: float) -> str:
    if entity_type in {"company", "delegation", "institution"}:
        return "Low"
    if entity_type == "person":
        if score is not None and score >= 50:
            return "Medium"
        return "High"
    return "Medium"


def initial_validation_status(gdpr_level: str) -> str:
    if gdpr_level == "High":
        return "Review required"
    return "Pending"


# ---------------------------------------------------------------------------
# Recommended outreach text
# ---------------------------------------------------------------------------


def reason_to_contact(
    *, role_cat: str, company_name: Optional[str], confidence: str,
    relevance: str, signal_type: Optional[str],
) -> str:
    parts: list[str] = []
    if confidence == "High":
        parts.append("Présence Eurosatory confirmée publiquement")
    elif confidence == "Medium":
        parts.append("Présence probable (signal à valider)")
    else:
        parts.append("Signal faible — qualification nécessaire")

    if role_cat != UNKNOWN_ROLE:
        parts.append(f"rôle {role_cat}")
    if company_name:
        parts.append(f"chez {company_name}")
    if signal_type and signal_type not in ("social_post", "personal_linkedin_post"):
        parts.append(f"source : {signal_type.replace('_', ' ')}")
    return " — ".join(parts) + "."


def recommended_angle(role_cat: str, relevance: str) -> str:
    if relevance == "High":
        if role_cat == "CEO / Founder":
            return "Approche directe sur la stratégie ou un partenariat capacitaire."
        if role_cat == "Sales / Business Development":
            return "Réunion commerciale avant Eurosatory pour qualifier les programmes 2026."
        if role_cat == "Procurement / Purchasing":
            return "Pitch fournisseur sur la roadmap d'achat 2026."
        if role_cat == "Partnerships":
            return "Discussion partenariat industriel / GTM conjoint."
        if role_cat == "Program / Project Manager":
            return "Brief programme : où vos solutions s'intègrent."
        if role_cat == "Defense / Military":
            return "Discussion utilisateur : retours terrain et besoins capacitaires."
    if relevance == "Medium":
        return "Discovery call avant le salon pour qualifier le besoin."
    return "Garder en veille ; pas d'action immédiate."
