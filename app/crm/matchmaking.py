"""Matchmaking score — compute, on the fly, how each company in the CRM
matches the *buyer's* commercial profile.

Why it exists
-------------
The generic ``lead_score`` / ``priority_level`` reflect a company's defense
data strength (clarity of products, sourcing potential, completeness).  They
do **not** answer "is this company a target *for me*".  Each buyer of the
database is itself an Eurosatory exhibitor with its own offering and its own
sourcing list — they need a per-buyer reading on top.

This module produces, given the buyer's declared
``my_offerings`` (what they sell) and ``my_sourcing`` (what they want to buy),
two views per company:

* **buy_fit**   — does this company likely *buy* what I sell?
                  Overlap between my_offerings and the company's
                  ``probable_buying_needs``.
* **sell_fit**  — does this company *sell* what I want to source?
                  Overlap between my_sourcing and the company's
                  ``built_products`` / ``sold_offerings``.

The combined ``match_score`` (0-100) is the max of the two views, weighted by
how many offerings the buyer declared.  We add a small bonus for explicit
defense-segment alignment so the result is biased toward strategic fits.
"""
from __future__ import annotations

import pandas as pd

from app.crm.pitch import BUYING_NEED_FR, OFFERING_FR, PRODUCT_FR

# ---------------------------------------------------------------------------
# Canonical vocabulary the buyer can pick from
# ---------------------------------------------------------------------------

# "What I sell" — same labels as BUILT_PRODUCTS so we can match against the
# probable_buying_needs of target companies (which are derived from the same
# taxonomy via BUYING_NEEDS_BY_BUILT).
SELLABLE_OFFERINGS: list[str] = sorted(set(PRODUCT_FR.keys()) | set(OFFERING_FR.keys()))

# "What I'm sourcing" — same labels (the buyer is shopping for products /
# services that other companies build / sell).
SOURCEABLE_PRODUCTS: list[str] = sorted(PRODUCT_FR.keys())

# Buying-need vocabulary (richer than offerings — includes "energetic
# materials", "industrial subcontracting", "export financing"…).
BUYING_NEEDS_VOCAB: list[str] = sorted(BUYING_NEED_FR.keys())


def _split_join(joined: str) -> list[str]:
    """Split a ``;``-joined CRM column back into the canonical English labels.
    Tolerates ``,``-joined fields too (prospecting_keywords)."""
    if not joined or pd.isna(joined):
        return []
    parts = []
    for chunk in str(joined).split(";"):
        parts.extend(p.strip() for p in chunk.split(","))
    return [p for p in parts if p]


def _set_overlap_pct(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = sa & sb
    if not inter:
        return 0.0
    # ratio of buyer's picks satisfied by the row's set
    return round(len(inter) / max(1, len(sa)) * 100, 1)


def compute_match_dataframe(
    df: pd.DataFrame,
    *,
    my_offerings: list[str],
    my_sourcing: list[str],
    my_segments: list[str] | None = None,
) -> pd.DataFrame:
    """Return a copy of ``df`` with three new columns:
    ``buy_fit_pct``, ``sell_fit_pct``, ``match_score``, ``match_level``.

    Empty inputs → zeros (no matchmaking active).
    """
    out = df.copy()
    n = len(out)
    if n == 0 or (not my_offerings and not my_sourcing):
        out["buy_fit_pct"] = 0.0
        out["sell_fit_pct"] = 0.0
        out["match_score"] = 0.0
        out["match_level"] = "—"
        return out

    # Pre-compute label sets per row
    buying = out["probable_buying_needs"].apply(_split_join)
    # also fold the secondary buying needs in (CRM column joined to "; ")
    extra_buying = out["buying_needs_secondary"].apply(_split_join) \
        if "buying_needs_secondary" in out.columns else None
    if extra_buying is not None:
        buying = [list(set(b + e)) for b, e in zip(buying, extra_buying)]

    selling = out["products_built"].apply(_split_join)
    if "products_sold" in out.columns:
        sold = out["products_sold"].apply(_split_join)
        selling = [list(set(b + s)) for b, s in zip(selling, sold)]
    if "services_sold" in out.columns:
        services = out["services_sold"].apply(_split_join)
        selling = [list(set(b + s)) for b, s in zip(selling, services)]

    # buy_fit: how much of my_offerings does this company buy?
    buy_pct = [_set_overlap_pct(my_offerings, b) if my_offerings else 0.0 for b in buying]
    # sell_fit: how much of my_sourcing does this company sell?
    sell_pct = [_set_overlap_pct(my_sourcing, s) if my_sourcing else 0.0 for s in selling]

    # Defense-segment bonus (0-15 pts) — only when buyer declared segments
    if my_segments and "defense_segment_main" in out.columns:
        seg_set = set(my_segments)
        seg_bonus = out["defense_segment_main"].apply(
            lambda v: 15 if v in seg_set else 0
        ).tolist()
    else:
        seg_bonus = [0] * n

    # Combined match score: max(buy_pct, sell_pct) weighted, plus segment bonus
    match_score = [
        min(round(max(bp, sp) * 0.85 + sb, 1), 100.0)
        for bp, sp, sb in zip(buy_pct, sell_pct, seg_bonus)
    ]
    match_level = [
        "Strong" if m >= 70 else
        "Medium" if m >= 40 else
        "Weak" if m > 0 else
        "—"
        for m in match_score
    ]

    out["buy_fit_pct"] = buy_pct
    out["sell_fit_pct"] = sell_pct
    out["match_score"] = match_score
    out["match_level"] = match_level
    return out


def matched_columns_for_table(my_offerings: list[str], my_sourcing: list[str]) -> list[str]:
    """Pick the columns to surface in the table when matchmaking is active."""
    cols = [
        "match_score", "match_level",
        "account_name", "country", "core_business",
    ]
    if my_offerings:
        cols.append("buying_need_main")
    if my_sourcing:
        cols.append("products_built")
    cols += ["target_type", "next_best_action", "lead_status", "data_confidence"]
    return cols
