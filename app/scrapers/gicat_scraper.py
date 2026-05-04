"""GICAT directory scraper.

Source
------
https://gicat.com/annuaire/ embeds an iframe to a hubj2c.com SaaS that
loads the full member list in one POST to ``getSettings``. ~512 French
defense industry members. No authentication required. Returned JSON is
labelled ``Content-Type: text/html`` but the body is valid JSON.

Per-company fields available
----------------------------
- ``idSociete`` — internal hubj2c id
- ``exposant`` — company name
- ``codePaysAffilie`` — ISO-3 country code (FRA / GBR / ...)
- ``Texte`` — short company description (~200-500 chars, French)
- ``Logo`` — image filename hosted on hubj2c
- ``DomainesActivite`` — ;-joined taxonomy ids (defense activity domains)
- ``SecteursActivite`` — ;-joined sector ids
- ``pictos`` — ;-joined category icons (defen-secu, defense, helped, ...)

What's MISSING (vs an Eurosatory exhibitor row)
- website_url / email / phone / linkedin (not in this endpoint)
- city / postal address
- detailed firmographics

So this endpoint is best used as a **directory cross-reference** : tag
existing Eurosatory exhibitors as GICAT members, and surface
GICAT-only companies (not in our Eurosatory catalog) as a separate
list of "additional French defense industry contacts" the user can
enrich later via web search.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.scrapers.http_client import HttpClient


GICAT_API_URL = (
    "https://new-liste-exposants.hubj2c.com/gicat/main/fr/getSettings"
)
GICAT_LOGO_BASE = (
    "https://new-liste-exposants.hubj2c.com/gicat/main/uploads/logos/"
)


@dataclass
class GicatMember:
    id_societe: str
    name: str
    country_iso3: Optional[str]
    description: Optional[str]
    logo_url: Optional[str]
    domains: list[str]   # taxonomy ids (raw)
    sectors: list[str]   # taxonomy ids (raw)
    categories: list[str]  # picto names (defen-secu, defense, helped, …)
    region: Optional[int]


# ISO-3 → ISO-2 mapping for the codes we'll commonly see in this directory.
_ISO3_TO_ISO2 = {
    "FRA": "FR", "GBR": "GB", "USA": "US", "DEU": "DE", "ITA": "IT",
    "ESP": "ES", "BEL": "BE", "NLD": "NL", "POL": "PL", "SWE": "SE",
    "FIN": "FI", "NOR": "NO", "CHE": "CH", "AUT": "AT", "PRT": "PT",
    "ISR": "IL", "TUR": "TR", "CAN": "CA", "AUS": "AU", "JPN": "JP",
    "KOR": "KR", "ROU": "RO", "CZE": "CZ", "DNK": "DK", "GRC": "GR",
}


_ISO2_TO_COUNTRY = {
    "FR": "France", "GB": "United Kingdom", "US": "United States",
    "DE": "Germany", "IT": "Italy", "ES": "Spain", "BE": "Belgium",
    "NL": "Netherlands", "PL": "Poland", "SE": "Sweden", "FI": "Finland",
    "NO": "Norway", "CH": "Switzerland", "AT": "Austria", "PT": "Portugal",
    "IL": "Israel", "TR": "Turkey", "CA": "Canada", "AU": "Australia",
    "JP": "Japan", "KR": "South Korea", "RO": "Romania", "CZ": "Czechia",
    "DK": "Denmark", "GR": "Greece",
}


def country_iso3_to_name(iso3: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return ``(country_name, iso2)`` from an ISO-3 code (best-effort)."""
    if not iso3:
        return None, None
    iso2 = _ISO3_TO_ISO2.get(iso3.upper())
    if iso2 is None:
        return None, None
    return _ISO2_TO_COUNTRY.get(iso2), iso2


async def _fetch_async() -> list[GicatMember]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://new-liste-exposants.hubj2c.com",
        "Referer": "https://new-liste-exposants.hubj2c.com/gicat/main/fr",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with HttpClient(
        extra_headers=headers, per_host_delay=0.3, concurrency=2,
        timeout=30, max_retries=2,
    ) as c:
        r = await c.request("POST", GICAT_API_URL)
        if r.status_code != 200:
            logger.warning(
                f"GICAT getSettings returned {r.status_code} ; aborting."
            )
            return []
        try:
            payload = r.json()
        except Exception as e:  # noqa: BLE001
            logger.error(f"GICAT response was not valid JSON: {e!r}")
            return []
    raw_list = payload.get("list") or []
    out: list[GicatMember] = []
    for row in raw_list:
        name = (row.get("exposant") or "").strip()
        if not name:
            continue
        logo_filename = (row.get("Logo") or "").strip()
        out.append(
            GicatMember(
                id_societe=str(row.get("idSociete") or ""),
                name=name,
                country_iso3=(row.get("codePaysAffilie") or "").strip() or None,
                description=(row.get("Texte") or "").strip() or None,
                logo_url=(GICAT_LOGO_BASE + logo_filename
                          if logo_filename else None),
                domains=[
                    x for x in (row.get("DomainesActivite") or "").split(",")
                    if x
                ],
                sectors=[
                    x for x in (row.get("SecteursActivite") or "").split(",")
                    if x
                ],
                categories=[
                    x for x in (row.get("pictos") or "").split(",") if x
                ],
                region=(int(row["Region"])
                        if row.get("Region") not in (None, "") else None),
            )
        )
    return out


def fetch_gicat_members() -> list[GicatMember]:
    """Sync entrypoint — returns the full GICAT member list."""
    return asyncio.run(_fetch_async())


# ---------------------------------------------------------------------------
# Cross-reference with the existing Exhibitor catalog
# ---------------------------------------------------------------------------


def cross_reference_with_exhibitors(
    members: list[GicatMember],
) -> dict:
    """Match each GICAT member against the existing Exhibitor catalog by
    normalised company name. Returns ``{matched, unmatched, by_country}``.
    """
    import re
    from app.database import Exhibitor, SessionLocal
    from sqlalchemy import select

    s = SessionLocal()
    try:
        catalog: dict[str, int] = {}
        for cn, eid in s.execute(
            select(Exhibitor.company_name, Exhibitor.id)
        ).all():
            if not cn:
                continue
            norm = re.sub(r"[^a-z0-9]+", " ", cn.lower()).strip()
            catalog[norm] = eid
    finally:
        s.close()

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    matched: list[tuple[GicatMember, int]] = []
    unmatched: list[GicatMember] = []
    for m in members:
        n = _norm(m.name)
        eid = catalog.get(n)
        if eid is None:
            # Loose substring fallback for "ACME GROUP" vs "ACME Group SAS"
            for cat_name, cat_id in catalog.items():
                if len(cat_name) < 4 or len(n) < 4:
                    continue
                if n in cat_name or cat_name in n:
                    eid = cat_id
                    break
        if eid is not None:
            matched.append((m, eid))
        else:
            unmatched.append(m)

    by_country: dict[str, int] = {}
    for m in members:
        c = m.country_iso3 or "?"
        by_country[c] = by_country.get(c, 0) + 1

    return {
        "total": len(members),
        "matched": matched,
        "unmatched": unmatched,
        "by_country": by_country,
    }
