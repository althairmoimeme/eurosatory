"""Deduplication helpers.

Within an Eurosatory run, each exhibitor has a stable ``finderr_guid`` so the
primary dedup key is trivial (we upsert by GUID).  This module addresses the
*secondary* dedup case: detecting that two distinct GUIDs are likely the same
real-world company (subsidiaries, brand variants, re-registered booths) so the
sales team gets a clean list.

Heuristics
----------
* same canonical website domain → near-certain duplicate (confidence 0.95)
* same normalized company name + same country → likely duplicate (0.85)
* fuzzy name ratio > 92 + same country → possible duplicate (0.7)

We never auto-merge — we expose candidate clusters and let a human confirm.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from rapidfuzz import fuzz

from app.processors.normalizer import normalize_company_name


@dataclass
class DuplicateCluster:
    members: list[int]  # exhibitor IDs
    method: str
    confidence: float
    key: str


def cluster_by_website(rows: Sequence[dict]) -> list[DuplicateCluster]:
    by_key: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        k = r.get("website_url_normalized")
        if k:
            by_key[k].append(r["id"])
    return [
        DuplicateCluster(members=ids, method="website", confidence=0.95, key=k)
        for k, ids in by_key.items()
        if len(ids) > 1
    ]


def cluster_by_name_country(rows: Sequence[dict]) -> list[DuplicateCluster]:
    by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    for r in rows:
        n = normalize_company_name(r.get("company_name"))
        c = (r.get("country_iso2") or "").upper()
        if n and c:
            by_key[(n.lower(), c)].append(r["id"])
    return [
        DuplicateCluster(
            members=ids, method="name+country", confidence=0.85, key=f"{name}|{country}"
        )
        for (name, country), ids in by_key.items()
        if len(ids) > 1
    ]


def fuzzy_name_pairs(
    rows: Sequence[dict], threshold: int = 92
) -> list[DuplicateCluster]:
    by_country: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        c = (r.get("country_iso2") or "").upper()
        if c and r.get("company_name"):
            by_country[c].append(r)
    clusters: list[DuplicateCluster] = []
    for country, group in by_country.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                score = fuzz.token_set_ratio(a["company_name"], b["company_name"])
                if score >= threshold:
                    clusters.append(
                        DuplicateCluster(
                            members=[a["id"], b["id"]],
                            method="fuzzy_name",
                            confidence=round(score / 100.0, 2),
                            key=f"{country}|{a['company_name']}~{b['company_name']}",
                        )
                    )
    return clusters


def find_duplicates(rows: Iterable[dict]) -> list[DuplicateCluster]:
    rows = list(rows)
    clusters = []
    clusters.extend(cluster_by_website(rows))
    clusters.extend(cluster_by_name_country(rows))
    clusters.extend(fuzzy_name_pairs(rows))
    return clusters
