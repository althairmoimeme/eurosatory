"""Parse the GICAT 2025 directory PDF into structured records.

The directory has 436 single-page company entries (out of 548 pages —
the rest is TOC, intros, board lists, indices). Each entry has a stable
shape :

    DOMAINES D'ACTIVITÉ - BUSINESS AREAS
    • <fr label>
      <en label>
    • ...

    CORRESPONDANT GICAT - GIGAT REPRESENTATIVE
    <Person Name>
    Tel. <phone>
    <email>

    PRINCIPAUX DIRIGEANTS - EXECUTIVES
    <Person Name>, <Role>
    <Person Name>, <Role>
    ...

    SECTEURS D'INTERVENTION
    SECTORS OF INTERVENTION
    <sector lines>

    CHIFFRES CLEFS - KEY FIGURES
    <key figure lines>

    <COMPANY NAME, all-caps possibly multi-line>
    <address line 1>
    <address line 2>
    ...
    <postal code> <city> - FRANCE
    <phone>
    <email>
    <website?>

We extract one ``GicatCompany`` per page. Multi-page entries (rare) are
handled by stitching consecutive pages when the second one starts mid
section.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class GicatPerson:
    name: str
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_correspondent: bool = False  # True if this is the "Correspondant GICAT"


@dataclass
class GicatCompany:
    name: str
    address: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: str = "France"
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    business_areas: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    turnover_keur: Optional[int] = None
    capital_keur: Optional[int] = None
    employees: Optional[int] = None
    persons: list[GicatPerson] = field(default_factory=list)


# Section header markers — order matters (later headers stop earlier sections)
_SECTIONS = [
    "DOMAINES D'ACTIVITÉ",
    "DOMAINES D’ACTIVITÉ",
    "CORRESPONDANT GICAT",
    "PRINCIPAUX DIRIGEANTS",
    "SECTEURS D'INTERVENTION",
    "SECTEURS D’INTERVENTION",
    "CHIFFRES CLEFS",
    "CERTIFICATIONS",
    "ACTIVITÉS",
]


def _strip_section_header(line: str) -> bool:
    """True when the line is a section header we should not include in
    a section body.
    """
    up = line.strip().upper()
    if any(s in up for s in (
        "DOMAINES D'ACTIVITÉ", "DOMAINES D’ACTIVITÉ",
        "CORRESPONDANT GICAT", "PRINCIPAUX DIRIGEANTS",
        "SECTEURS D'INTERVENTION", "SECTEURS D’INTERVENTION",
        "CHIFFRES CLEFS", "BUSINESS AREAS", "GIGAT REPRESENTATIVE",
        "EXECUTIVES", "SECTORS OF INTERVENTION", "KEY FIGURES",
        "PLUS D'INFORMATIONS", "PLUS D’INFORMATIONS",
    )):
        return True
    return False


_PHONE_RX = re.compile(r"\+\d[\d\s().-]{8,}\d")
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RX = re.compile(
    r"\b(?:https?://)?(?:www\.)?[A-Za-z0-9-]+\.[A-Za-z]{2,}(?:/[A-Za-z0-9./_-]*)?",
)
_POSTAL_FR_RX = re.compile(r"\b(\d{5})\s+([A-ZÉÈÊÀÂÔÛÇ][A-ZÉÈÊÀÂÔÛÇ\s\-]+)")
_KEY_FIG_TURNOVER_RX = re.compile(
    r"Total\s+turnover\s*:\s*([\d\s.,]+)\s*([KM])€", re.IGNORECASE,
)
_KEY_FIG_CAPITAL_RX = re.compile(
    r"Capital\s*:\s*([\d\s.,]+)\s*([KM])€", re.IGNORECASE,
)
_KEY_FIG_EMP_RX = re.compile(
    r"Employees?\s*:\s*([\d\s.,]+)", re.IGNORECASE,
)


def _amount_keur(num: str, mult: str) -> Optional[int]:
    try:
        clean = num.replace(" ", "").replace(",", ".").replace("\xa0", "")
        v = float(clean)
        return int(v * 1000) if mult.upper() == "M" else int(v)
    except (ValueError, TypeError):
        return None


def _extract_business_areas(text: str) -> list[str]:
    """Pull bulleted French labels from the DOMAINES D'ACTIVITÉ section."""
    m = re.search(
        r"DOMAINES D[’']ACTIVITÉ[\s\S]*?(?=CORRESPONDANT GICAT|"
        r"PRINCIPAUX DIRIGEANTS|SECTEURS|CHIFFRES|$)",
        text,
    )
    if not m:
        return []
    block = m.group(0)
    # Each bullet starts with • and the FR label is on the line after •;
    # the EN translation is the indented next line. We keep only the FR.
    out: list[str] = []
    lines = block.splitlines()
    capture = False
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("•") or stripped.startswith("·") or stripped.startswith("-"):
            label = stripped.lstrip("•·- ").strip()
            if label:
                out.append(label)
                capture = True
            continue
        # Indented translation lines — skip
        if capture and stripped and ln[0:1] in (" ", "\t"):
            capture = False
    return out


def _extract_correspondent(text: str) -> Optional[GicatPerson]:
    m = re.search(
        r"CORRESPONDANT GICAT[^\n]*\n([\s\S]*?)(?=PRINCIPAUX DIRIGEANTS|"
        r"SECTEURS|CHIFFRES|$)",
        text,
    )
    if not m:
        return None
    block = m.group(1).strip()
    if not block:
        return None
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()
             and not _strip_section_header(ln)]
    if not lines:
        return None
    name = lines[0].strip(" .,;:")
    if not name or any(c.isdigit() for c in name):
        return None
    phone = None
    email = None
    for ln in lines[1:]:
        if not phone:
            pm = _PHONE_RX.search(ln)
            if pm:
                phone = pm.group(0).strip()
        if not email:
            em = _EMAIL_RX.search(ln)
            if em:
                email = em.group(0).strip()
    return GicatPerson(name=name, phone=phone, email=email,
                        is_correspondent=True)


def _extract_executives(text: str) -> list[GicatPerson]:
    m = re.search(
        r"PRINCIPAUX DIRIGEANTS[^\n]*\n([\s\S]*?)(?=SECTEURS|CHIFFRES|"
        r"DOMAINES|CORRESPONDANT|$)",
        text,
    )
    if not m:
        return []
    block = m.group(1).strip()
    out: list[GicatPerson] = []
    # Join wrapped role lines onto the previous entry. A "new entry"
    # starts when the line begins with a Firstname / surname pattern AND
    # the previous entry already has a comma (= name,role complete).
    lines: list[str] = []
    for ln in block.splitlines():
        ln = ln.strip()
        if not ln or _strip_section_header(ln):
            continue
        # Continuation rule: lowercase start, OR previous line lacks a
        # comma (= role wrap onto new line).
        starts_with_lc = bool(ln) and ln[0:1].islower()
        prev_lacks_comma = lines and "," not in lines[-1]
        if lines and (starts_with_lc or prev_lacks_comma):
            lines[-1] += " " + ln
        else:
            lines.append(ln)

    for ln in lines:
        # Split on FIRST comma — name is everything before, role after.
        # This preserves hyphenated names like "Jean-Baptiste ADO-SOLABERRIETA"
        # without truncating at the internal hyphen.
        if "," in ln:
            name_part, role_part = ln.split(",", 1)
        else:
            name_part, role_part = ln, ""
        name = name_part.strip(" .,;:")
        role = role_part.strip(" .,;:") or None
        if len(name) < 4 or any(c.isdigit() for c in name):
            continue
        # Reject lines that don't look like a name (must contain at least
        # one uppercase-only token = the surname).
        if not re.search(r"\b[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ\-']{2,}\b", name):
            continue
        if re.search(r"\b(CHIFFRES|EFFECTIF|CAPITAL|TURNOVER)\b", name):
            continue
        out.append(GicatPerson(name=name, role=role))
    return out


def _extract_company_block(text: str) -> dict:
    """Find the trailing "company name + address + contact" block.

    The block is at the very end of the page text, after the CHIFFRES
    section. Strategy : work from the bottom up — collect lines until we
    hit a section header or run out.
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Drop trailing empty lines
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return {}
    # Walk up until we see a section header — that's the start of the block.
    end_idx = len(lines)
    start_idx = end_idx
    for i in range(end_idx - 1, -1, -1):
        if _strip_section_header(lines[i]) or lines[i].startswith(
            ("•", "·", "<@")
        ):
            start_idx = i + 1
            break
        if "Total turnover" in lines[i] or "Capital :" in lines[i] or \
                "Effectif" in lines[i] or "Employees" in lines[i]:
            start_idx = i + 1
            break
    block_lines = [ln.strip() for ln in lines[start_idx:end_idx]
                   if ln.strip()]
    if not block_lines:
        return {}

    # Parse top-down :
    #   1. company name = first line (often spans 2 lines if hyphenated)
    #   2. address lines until phone/email
    #   3. phone, email, website
    name_lines: list[str] = []
    rest: list[str] = []
    flushed_name = False
    for ln in block_lines:
        if not flushed_name and (
            ln.isupper()
            or re.match(r"^[A-ZÉÈÀÂÔÛÇ0-9][A-ZÉÈÀÂÔÛÇ0-9\s&'\-./]+$", ln)
        ) and not _PHONE_RX.search(ln) and not _EMAIL_RX.search(ln):
            name_lines.append(ln)
            continue
        flushed_name = True
        rest.append(ln)
    if not name_lines:
        return {}
    name = " ".join(name_lines).strip()
    name = re.sub(r"\s+", " ", name)

    phone = None
    email = None
    website = None
    address_parts: list[str] = []
    postal_code = None
    city = None
    for ln in rest:
        if not phone and (pm := _PHONE_RX.search(ln)):
            phone = pm.group(0).strip()
            continue
        if not email and (em := _EMAIL_RX.search(ln)):
            email = em.group(0).strip()
            continue
        if not website and re.search(
            r"\b(?:www\.|https?://)\S+", ln,
        ):
            web = re.search(r"\b(?:https?://)?(?:www\.)?[A-Za-z0-9-]+\."
                            r"[A-Za-z]{2,}(?:/\S*)?", ln)
            if web:
                website = web.group(0)
            continue
        if (pm := _POSTAL_FR_RX.search(ln)):
            postal_code = pm.group(1)
            city = pm.group(2).strip().title()
        address_parts.append(ln)
    address = ", ".join(a for a in address_parts if a) or None
    return {
        "name": name,
        "address": address,
        "postal_code": postal_code,
        "city": city,
        "phone": phone,
        "email": email,
        "website": website,
    }


def _extract_key_figures(text: str) -> dict:
    out: dict = {}
    if (m := _KEY_FIG_TURNOVER_RX.search(text)):
        out["turnover_keur"] = _amount_keur(m.group(1), m.group(2))
    if (m := _KEY_FIG_CAPITAL_RX.search(text)):
        out["capital_keur"] = _amount_keur(m.group(1), m.group(2))
    if (m := _KEY_FIG_EMP_RX.search(text)):
        try:
            out["employees"] = int(
                m.group(1).replace(" ", "").replace("\xa0", "")
            )
        except (ValueError, TypeError):
            pass
    return out


def parse_company_page(text: str) -> Optional[GicatCompany]:
    """Parse one PDF page text. Returns None if the page doesn't look
    like a company entry.
    """
    # Normalise non-breaking spaces — pymupdf preserves the U+00A0
    # spaces typeset between section headers (e.g. ``CORRESPONDANT\xa0GICAT``)
    # which would otherwise defeat plain-space regex matches.
    text = text.replace("\xa0", " ")
    if "CORRESPONDANT GICAT" not in text and \
            "PRINCIPAUX DIRIGEANTS" not in text:
        return None
    block = _extract_company_block(text)
    if not block.get("name"):
        return None
    correspondent = _extract_correspondent(text)
    executives = _extract_executives(text)
    persons = []
    if correspondent:
        persons.append(correspondent)
    for ex in executives:
        # Skip executive entries that duplicate the correspondent's name.
        if correspondent and ex.name == correspondent.name:
            # Promote the correspondent's role from the exec list.
            if not correspondent.role and ex.role:
                correspondent.role = ex.role
            continue
        persons.append(ex)

    company = GicatCompany(
        name=block["name"],
        address=block.get("address"),
        postal_code=block.get("postal_code"),
        city=block.get("city"),
        phone=block.get("phone"),
        email=block.get("email"),
        website=block.get("website"),
        business_areas=_extract_business_areas(text),
        persons=persons,
        **_extract_key_figures(text),
    )
    return company


def parse_pdf(pdf_path: Path) -> list[GicatCompany]:
    """Parse the full GICAT directory PDF and return the list of
    extracted companies (with their persons embedded).
    """
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    out: list[GicatCompany] = []
    for i in range(len(doc)):
        text = doc[i].get_text()
        company = parse_company_page(text)
        if company:
            out.append(company)
    return out
