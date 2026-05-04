"""Normalisation of raw scraping data into clean DB rows.

Conventions
-----------
- ``website_url_normalized`` strips schemes, ``www.``, trailing slashes and
  lower-cases the host so that "https://www.aselsan.com/" and "aselsan.com"
  collide for deduplication.
- ``slug`` is a lower-cased ASCII representation of the canonical company name,
  used as a stable secondary key when GUID is missing.
- We never invent values: missing fields stay ``None``; only string cleanups
  (whitespace, quote stripping) are applied.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import tldextract
from slugify import slugify

GENERIC_EMAIL_PREFIXES = (
    "contact",
    "info",
    "sales",
    "business",
    "export",
    "hello",
    "office",
    "commercial",
    "sale",
    "enquiries",
    "inquiry",
    "inquiries",
    "marketing",
)


def clean_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().strip('"').strip()
    return s or None


def normalize_company_name(raw: Optional[str]) -> Optional[str]:
    s = clean_str(raw)
    if not s:
        return None
    # collapse internal whitespace, drop surrounding quotes
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_website(url: Optional[str]) -> Optional[str]:
    """Return a fully-qualified https URL, ``None`` if it doesn't look like a URL."""
    s = clean_str(url)
    if not s:
        return None
    if not re.match(r"^https?://", s, re.IGNORECASE):
        s = "http://" + s
    try:
        parsed = urlparse(s)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return None
    # rebuild with original scheme but cleaned host
    return urlunparse(
        (parsed.scheme.lower(), host + (f":{parsed.port}" if parsed.port else ""),
         parsed.path or "/", parsed.params, parsed.query, "")
    )


def website_canonical_key(url: Optional[str]) -> Optional[str]:
    """Lower-case registered domain (e.g. 'aselsan.com')."""
    s = clean_str(url)
    if not s:
        return None
    if not re.match(r"^https?://", s, re.IGNORECASE):
        s = "http://" + s
    ext = tldextract.extract(s)
    if not ext.domain:
        return None
    if ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()
    return ext.domain.lower()


def normalize_email(email: Optional[str]) -> Optional[str]:
    s = clean_str(email)
    if not s:
        return None
    s = s.lower()
    if "@" not in s or " " in s:
        return None
    return s


def is_generic_email(email: Optional[str]) -> bool:
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0]
    return local in GENERIC_EMAIL_PREFIXES or any(
        local.startswith(p + ".") or local.startswith(p + "-") for p in GENERIC_EMAIL_PREFIXES
    )


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    s = clean_str(phone)
    if not s:
        return None
    cleaned = re.sub(r"[^\d+]", "", s)
    if not cleaned or len(cleaned) < 6:
        return None
    return cleaned


def normalize_country_code(code: Optional[str]) -> Optional[str]:
    s = clean_str(code)
    if not s:
        return None
    return s.upper()[:2]


def make_slug(name: Optional[str]) -> Optional[str]:
    s = clean_str(name)
    if not s:
        return None
    return slugify(s, max_length=120) or None


def normalize_finderr_search_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a Finderr search-exhibitors record onto our ``Exhibitor`` columns."""
    name = normalize_company_name(row.get("Exhi_CompanyName") or row.get("Exhi_CompanyName2"))
    website = normalize_website(row.get("Exhi_Website"))
    return {
        "finderr_guid": row.get("Exhi_Guid"),
        "finderr_external_id": clean_str(row.get("Exhi_ExternalId")),
        "company_name": name,
        "company_name_alt": clean_str(row.get("Exhi_CompanyName2")),
        "slug": make_slug(name),
        "country_iso2": normalize_country_code(row.get("Exhi_Country_CodeISO2")),
        "phone": normalize_phone(row.get("Exhi_Phone")),
        "website_url": website,
        "website_url_normalized": website_canonical_key(website),
        "logo_url": clean_str(row.get("Exhi_Logo")),
        "short_presentation": clean_str(row.get("ShortPresentation")),
        "one_liner": clean_str(row.get("OneLiner")),
        "business_areas": row.get("BusinessAreas") or [],
        "stands": row.get("Stands") or [],
        "pavilion": clean_str(row.get("Exhi_Pavilion")),
        "is_lab": bool(row.get("Exhi_IsLab")),
        "is_new_exhibitor": bool(row.get("Exhi_IsNewExhibitor")),
        "is_featured": bool(row.get("IsFeatured")),
        "raw_search_payload": row,
    }


def merge_finderr_detail(target: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    """Merge fields from a ``v3/get_exhibitor`` payload onto the search-derived dict.

    Only fills missing values; never overwrites a non-empty value with a worse one.
    Stores the raw payload too for audit.
    """
    def _set(key: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        if not target.get(key):
            target[key] = value

    contact_email = normalize_email(detail.get("Exhi_ContactEmail"))
    _set("contact_email", contact_email)
    if contact_email and is_generic_email(contact_email):
        _set("generic_sales_email", contact_email)

    _set("address1", clean_str(detail.get("Exhi_Address1")))
    _set("address2", clean_str(detail.get("Exhi_Address2")))
    _set("address3", clean_str(detail.get("Exhi_Address3")))
    _set("zip_code", clean_str(detail.get("Exhi_ZipCode")))
    _set("city", clean_str(detail.get("Exhi_City")))
    _set("state_province", clean_str(detail.get("Exhi_StateProvince")))
    country_name = clean_str(detail.get("Exhi_Country_Name"))
    if country_name and country_name != "NOT AVAILABLE LANGUAGE":
        _set("country_name", country_name)
    _set("phone", normalize_phone(detail.get("Exhi_Phone")))
    _set("fax", normalize_phone(detail.get("Exhi_Fax")))

    pres = clean_str(detail.get("Presentation"))
    if pres and pres != "NOT AVAILABLE LANGUAGE":
        _set("presentation", pres)

    socials = detail.get("SocialNetworks") or []
    for s in socials:
        url = clean_str(s.get("URL"))
        kind = (s.get("Type") or "").upper()
        if not url:
            continue
        if kind == "LINKEDIN":
            _set("linkedin_url", url)
        elif kind in ("TWITTER", "X"):
            _set("twitter_url", url)
        elif kind == "FACEBOOK":
            _set("facebook_url", url)
        elif kind == "YOUTUBE":
            _set("youtube_url", url)

    # Top-level social fields too
    _set("linkedin_url", clean_str(detail.get("Linkedin")))
    _set("twitter_url", clean_str(detail.get("Twitter")))
    _set("facebook_url", clean_str(detail.get("FaceBook")))
    _set("youtube_url", clean_str(detail.get("Youtube")))

    target["raw_detail_payload"] = detail
    target["_categories_labels"] = list(zip(
        detail.get("Categories") or [], detail.get("CategoriesLabels") or []
    ))
    target["_contacts"] = detail.get("Contacts") or []
    return target
