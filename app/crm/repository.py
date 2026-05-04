"""Materialise the CRM dataframe + helpers for filtering.

The CRM record is **always computed on the fly** from ``Exhibitor`` +
``ExhibitorIntelligence`` + ``ExhibitorTag`` + ``CommercialNote`` —
the database is the source of truth.

For the UI, results are cached at the call-site (Streamlit ``@st.cache_data``).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import pandas as pd
from sqlalchemy import or_, select

from app.crm.schema import CRM_COLUMNS
from app.crm.transformer import to_crm
from app.database import (
    CommercialNote,
    CustomList,
    CustomListMember,
    Exhibitor,
    ExhibitorIntelligence,
    ExhibitorTag,
    SessionLocal,
)


def _all_tags(s, exh_id: int) -> list[str]:
    return sorted(
        {t.tag for t in s.execute(
            select(ExhibitorTag).where(ExhibitorTag.exhibitor_id == exh_id)
        ).scalars()}
    )


def _latest_note(s, exh_id: int) -> Optional[str]:
    n = s.scalar(
        select(CommercialNote)
        .where(CommercialNote.exhibitor_id == exh_id)
        .order_by(CommercialNote.created_at.desc())
        .limit(1)
    )
    return n.note if n else None


def _custom_lists_for(s, exh_id: int) -> list[str]:
    rows = list(
        s.execute(
            select(CustomList.name)
            .join(CustomListMember, CustomListMember.list_id == CustomList.id)
            .where(CustomListMember.exhibitor_id == exh_id)
        ).all()
    )
    return [r[0] for r in rows]


def crm_dataframe(
    *,
    only_with_intelligence: bool = False,
    list_id: Optional[int] = None,
) -> pd.DataFrame:
    """Build the full CRM dataframe.

    Set ``only_with_intelligence=True`` to drop exhibitors not yet analysed.
    Set ``list_id`` to restrict to a saved list's members (frozen membership only;
    saved-filter criteria_json is applied client-side).
    """
    s = SessionLocal()
    try:
        q = select(Exhibitor, ExhibitorIntelligence).join(
            ExhibitorIntelligence,
            ExhibitorIntelligence.exhibitor_id == Exhibitor.id,
            isouter=not only_with_intelligence,
        )
        if list_id is not None:
            sub = (
                select(CustomListMember.exhibitor_id)
                .where(CustomListMember.list_id == list_id)
                .scalar_subquery()
            )
            q = q.where(Exhibitor.id.in_(sub))

        rows: list[dict[str, Any]] = []
        for exh, intel in s.execute(q).all():
            tags = _all_tags(s, exh.id)
            note = _latest_note(s, exh.id)
            lists = _custom_lists_for(s, exh.id)
            rows.append(
                to_crm(exh, intel, tags=tags, latest_note=note, custom_lists=lists)
            )
    finally:
        s.close()

    df = pd.DataFrame(rows, columns=CRM_COLUMNS)
    return df


# ---------------------------------------------------------------------------
# Filtering helpers (apply on the dataframe)
# ---------------------------------------------------------------------------


def _multiselect_match(value: Any, selected: list[str]) -> bool:
    if not selected:
        return True
    if value is None:
        return False
    text = str(value)
    return any(s in text for s in selected)


def _any_of_in_joined_column(
    df: pd.DataFrame, col: str, picks: Optional[list[str]]
) -> pd.Series:
    """Boolean mask: row matches if at least one ``pick`` appears in the
    ``;``-joined value of ``col``.  Empty ``picks`` returns all-True.
    """
    if not picks or col not in df.columns:
        return pd.Series(True, index=df.index)
    sentinels = [p.strip() for p in picks if p.strip()]
    if not sentinels:
        return pd.Series(True, index=df.index)

    def _hit(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return False
        text = str(v)
        return any(s in text for s in sentinels)

    return df[col].apply(_hit)


def apply_filters(
    df: pd.DataFrame,
    *,
    countries: Optional[list[str]] = None,
    defense_segments: Optional[list[str]] = None,
    target_types: Optional[list[str]] = None,
    priority_levels: Optional[list[str]] = None,
    lead_statuses: Optional[list[str]] = None,
    crm_stages: Optional[list[str]] = None,
    company_types: Optional[list[str]] = None,
    buying_needs: Optional[list[str]] = None,
    interest_levels: Optional[list[str]] = None,
    data_confidences: Optional[list[str]] = None,
    custom_lists: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    products_built_any: Optional[list[str]] = None,
    products_sold_any: Optional[list[str]] = None,
    services_sold_any: Optional[list[str]] = None,
    buying_needs_any: Optional[list[str]] = None,
    certifications_any: Optional[list[str]] = None,
    associations_any: Optional[list[str]] = None,
    has_parent_group: Optional[bool] = None,
    founding_year_range: Optional[tuple[int, int]] = None,
    employee_count_range: Optional[tuple[int, int]] = None,
    halls_any: Optional[list[str]] = None,
    only_favorites: bool = False,
    next_action_filter: Optional[str] = None,
    only_with_website: bool = False,
    only_high_confidence: bool = False,
    only_priority_targets: bool = False,
    manual_review: Optional[bool] = None,
    min_lead_score: int = 0,
    search_text: Optional[str] = None,
    # === Eurosatory 2026 targeting profile filters ===
    targeting_product_categories: Optional[list[str]] = None,
    targeting_service_categories: Optional[list[str]] = None,
    targeting_technology_categories: Optional[list[str]] = None,
    target_buyers_any: Optional[list[str]] = None,
    min_targeting_score: int = 0,
    targeting_sources: Optional[list[str]] = None,
    supply_chain_tier: Optional[list[str]] = None,
) -> pd.DataFrame:
    out = df.copy()

    if countries:
        out = out[out["country"].isin(countries)]
    if defense_segments:
        out = out[out["defense_segment_main"].isin(defense_segments)]
    if target_types:
        out = out[out["target_type"].isin(target_types)]
    if priority_levels:
        out = out[out["priority_level"].isin(priority_levels)]
    if lead_statuses:
        out = out[out["lead_status"].isin(lead_statuses)]
    if crm_stages:
        out = out[out["crm_stage"].isin(crm_stages)]
    if company_types:
        out = out[out["company_type"].isin(company_types)]
    if buying_needs:
        out = out[out["buying_need_main"].isin(buying_needs)]
    if interest_levels:
        out = out[out["commercial_interest_level"].isin(interest_levels)]
    if data_confidences:
        out = out[out["data_confidence"].isin(data_confidences)]
    if custom_lists:
        mask = out["custom_list"].fillna("").apply(
            lambda v: any(name in v for name in custom_lists)
        )
        out = out[mask]
    if tags:
        mask = out["tags"].fillna("").apply(
            lambda v: any(t in v for t in tags)
        )
        out = out[mask]
    if only_with_website:
        out = out[out["website_url"].notna() & (out["website_url"] != "")]
    if only_high_confidence:
        out = out[out["data_confidence"] == "High"]
    if only_priority_targets:
        out = out[out["priority_level"].isin(["A+", "A"])]
    if manual_review is not None:
        out = out[out["manual_review_required"] == manual_review]
    if min_lead_score:
        out = out[out["lead_score"].fillna(0) >= min_lead_score]
    # Bidirectional any-of filters on ;-joined columns
    if products_built_any:
        out = out[_any_of_in_joined_column(out, "products_built", products_built_any)]
    if products_sold_any:
        out = out[_any_of_in_joined_column(out, "products_sold", products_sold_any)]
    if services_sold_any:
        out = out[_any_of_in_joined_column(out, "services_sold", services_sold_any)]
    if buying_needs_any:
        # Match against both buying_need_main AND buying_needs_secondary
        mask_main = _any_of_in_joined_column(out, "buying_need_main", buying_needs_any)
        mask_sec = _any_of_in_joined_column(out, "buying_needs_secondary", buying_needs_any)
        out = out[mask_main | mask_sec]
    if certifications_any:
        out = out[_any_of_in_joined_column(out, "certifications", certifications_any)]
    if associations_any:
        out = out[_any_of_in_joined_column(out, "industry_associations", associations_any)]
    if has_parent_group is not None:
        if has_parent_group:
            out = out[out["parent_group"].fillna("").astype(str).str.len() > 0]
        else:
            out = out[out["parent_group"].fillna("").astype(str).str.len() == 0]
    if founding_year_range and "founding_year" in out.columns:
        lo, hi = founding_year_range
        # rows without a known year are kept only if the range covers everything
        all_years = out["founding_year"].dropna()
        if not all_years.empty and (lo > all_years.min() or hi < all_years.max()):
            mask = out["founding_year"].between(lo, hi)
            out = out[mask.fillna(False)]
    if employee_count_range and "company_size" in out.columns:
        lo, hi = employee_count_range
        import re as _re
        def _extract_emp(s_):
            if not isinstance(s_, str):
                return None
            m = _re.search(r"~(\d{2,6})", s_)
            return int(m.group(1)) if m else None
        emp_series = out["company_size"].apply(_extract_emp)
        if emp_series.dropna().empty is False:
            full_lo, full_hi = emp_series.dropna().min(), emp_series.dropna().max()
            if lo > full_lo or hi < full_hi:
                mask = emp_series.between(lo, hi)
                out = out[mask.fillna(False)]
    if halls_any and "booth_number" in out.columns:
        def _hall_match(b):
            if not isinstance(b, str):
                return False
            head = b.split("/", 1)[0].strip()
            return head in halls_any
        out = out[out["booth_number"].apply(_hall_match)]
    if only_favorites and "is_favorite" in out.columns:
        out = out[out["is_favorite"] == True]  # noqa: E712
    if next_action_filter and next_action_filter != "(any)" and "next_action_date" in out.columns:
        from datetime import datetime as _dt, timedelta as _td
        now = _dt.utcnow()
        nad = pd.to_datetime(out["next_action_date"], errors="coerce")
        if next_action_filter == "Overdue":
            out = out[(nad.notna()) & (nad < now)]
        elif next_action_filter == "Due this week":
            out = out[(nad.notna()) & (nad >= now) & (nad <= now + _td(days=7))]
        elif next_action_filter == "Due in 30 days":
            out = out[(nad.notna()) & (nad >= now) & (nad <= now + _td(days=30))]
        elif next_action_filter == "Sans date prévue":
            out = out[nad.isna()]
    if search_text:
        s = search_text.lower()
        cols = [
            "account_name", "city", "description_short", "products_built",
            "technologies", "buying_need_main", "ideal_seller_profile",
            "short_pitch", "tags",
            # also search the new targeting fields
            "activity_1liner", "products_specific", "products_categories",
            "why_target",
        ]
        mask = pd.Series(False, index=out.index)
        for c in cols:
            if c in out.columns:
                mask = mask | out[c].fillna("").astype(str).str.lower().str.contains(s, na=False)
        out = out[mask]

    # === Eurosatory 2026 targeting filters ============================
    # The category fields hold ' · '-joined canonical category strings
    # (e.g. "Drones aériens (UAV) · Systèmes anti-drone (C-UAS)"). We
    # match any of the picked categories — companies with at least one
    # listed bucket pass the filter.
    if targeting_product_categories and "products_categories" in out.columns:
        out = out[
            _any_of_in_joined_column(
                out, "products_categories", targeting_product_categories,
            )
        ]
    if targeting_service_categories and "services_categories" in out.columns:
        out = out[
            _any_of_in_joined_column(
                out, "services_categories", targeting_service_categories,
            )
        ]
    if (
        targeting_technology_categories
        and "technologies_categories" in out.columns
    ):
        out = out[
            _any_of_in_joined_column(
                out, "technologies_categories", targeting_technology_categories,
            )
        ]
    if target_buyers_any and "target_buyers" in out.columns:
        out = out[
            _any_of_in_joined_column(out, "target_buyers", target_buyers_any)
        ]
    if min_targeting_score and "targeting_score" in out.columns:
        out = out[out["targeting_score"].fillna(0) >= min_targeting_score]
    if targeting_sources and "targeting_source" in out.columns:
        out = out[out["targeting_source"].isin(targeting_sources)]
    if supply_chain_tier and "supply_chain_tier" in out.columns:
        out = out[out["supply_chain_tier"].isin(supply_chain_tier)]

    return out


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def kpis(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "total": 0, "a_or_aplus": 0, "potential_buyers": 0,
            "potential_partners": 0, "to_verify": 0, "avg_lead_score": 0.0,
        }
    total = len(df)
    return {
        "total": total,
        "a_or_aplus": int(df["priority_level"].isin(["A+", "A"]).sum()),
        "potential_buyers": int(
            df["all_target_types"].fillna("").str.contains("Acheteur potentiel", na=False).sum()
        ),
        "potential_partners": int(
            df["all_target_types"].fillna("").str.contains("Partenaire industriel", na=False).sum()
        ),
        "to_verify": int(df["manual_review_required"].fillna(False).sum()),
        "avg_lead_score": round(float(df["lead_score"].dropna().mean() or 0.0), 1),
    }


# ---------------------------------------------------------------------------
# Custom list CRUD
# ---------------------------------------------------------------------------


def list_custom_lists(include_archived: bool = False) -> list[CustomList]:
    """Return non-archived lists by default, sorted pinned-first then most-recent.

    Pass ``include_archived=True`` to also see archived lists (e.g. for the
    "Show archived" toggle in the UI).
    """
    s = SessionLocal()
    try:
        q = select(CustomList)
        if not include_archived:
            q = q.where(CustomList.archived_at.is_(None))
        # SQLite needs an explicit cast / coalesce-style ordering for booleans
        q = q.order_by(CustomList.is_pinned.desc(), CustomList.created_at.desc())
        return list(s.execute(q).scalars())
    finally:
        s.close()


def list_archived_custom_lists() -> list[CustomList]:
    s = SessionLocal()
    try:
        q = (
            select(CustomList)
            .where(CustomList.archived_at.is_not(None))
            .order_by(CustomList.archived_at.desc())
        )
        return list(s.execute(q).scalars())
    finally:
        s.close()


def toggle_pin_list(list_id: int) -> bool:
    """Flip the pinned flag.  Returns the new value."""
    s = SessionLocal()
    try:
        cl = s.get(CustomList, list_id)
        if cl is None:
            return False
        cl.is_pinned = not bool(cl.is_pinned)
        s.commit()
        return bool(cl.is_pinned)
    finally:
        s.close()


def archive_list(list_id: int) -> None:
    s = SessionLocal()
    try:
        cl = s.get(CustomList, list_id)
        if cl is None:
            return
        from datetime import datetime as _dt
        cl.archived_at = _dt.utcnow()
        s.commit()
    finally:
        s.close()


def unarchive_list(list_id: int) -> None:
    s = SessionLocal()
    try:
        cl = s.get(CustomList, list_id)
        if cl is None:
            return
        cl.archived_at = None
        s.commit()
    finally:
        s.close()


def create_custom_list(
    name: str, description: Optional[str] = None,
    owner: Optional[str] = None, criteria: Optional[dict] = None,
    color: Optional[str] = None,
) -> int:
    s = SessionLocal()
    try:
        existing = s.scalar(select(CustomList).where(CustomList.name == name))
        if existing:
            return existing.id
        cl = CustomList(
            name=name, description=description, owner=owner,
            criteria_json=criteria, color=color,
        )
        s.add(cl)
        s.commit()
        return cl.id
    finally:
        s.close()


def add_to_list(list_id: int, exhibitor_ids: Iterable[int], added_by: Optional[str] = None) -> int:
    s = SessionLocal()
    try:
        added = 0
        for eid in exhibitor_ids:
            existing = s.scalar(
                select(CustomListMember).where(
                    CustomListMember.list_id == list_id,
                    CustomListMember.exhibitor_id == eid,
                )
            )
            if existing:
                continue
            s.add(CustomListMember(list_id=list_id, exhibitor_id=eid, added_by=added_by))
            added += 1
        s.commit()
        return added
    finally:
        s.close()


def remove_from_list(list_id: int, exhibitor_ids: Iterable[int]) -> int:
    s = SessionLocal()
    try:
        removed = (
            s.query(CustomListMember)
            .filter(
                CustomListMember.list_id == list_id,
                CustomListMember.exhibitor_id.in_(list(exhibitor_ids)),
            )
            .delete(synchronize_session=False)
        )
        s.commit()
        return removed
    finally:
        s.close()


def update_custom_list(
    list_id: int,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    owner: Optional[str] = None,
    sales_team: Optional[str] = None,
    color: Optional[str] = None,
    criteria: Optional[dict] = None,
    is_dynamic: Optional[bool] = None,
) -> None:
    s = SessionLocal()
    try:
        cl = s.get(CustomList, list_id)
        if cl is None:
            return
        if name is not None:
            cl.name = name
        if description is not None:
            cl.description = description or None
        if owner is not None:
            cl.owner = owner or None
        if sales_team is not None:
            cl.sales_team = sales_team or None
        if color is not None:
            cl.color = color or None
        if criteria is not None:
            cl.criteria_json = criteria
        if is_dynamic is not None:
            cl.is_dynamic = bool(is_dynamic)
        s.commit()
    finally:
        s.close()


def sync_dynamic_list(list_id: int) -> dict[str, int]:
    """Recompute membership of a list from its ``criteria_json``.

    Adds members that match the criteria but aren't in the list, removes
    members that no longer match. Per-membership notes attached to removed
    rows are dropped (the ``custom_list_members`` row is deleted).

    Returns ``{"added": N, "removed": M, "total": K}``. If the list has no
    criteria, returns zeros without touching membership.
    """
    from datetime import datetime as _dt
    s = SessionLocal()
    try:
        cl = s.get(CustomList, list_id)
        if cl is None or not cl.criteria_json:
            return {"added": 0, "removed": 0, "total": 0}
        # Compute target membership from criteria using the same path as the UI.
        df = crm_dataframe()
        df = apply_filters(df, **cl.criteria_json)
        target_ids = {
            int(a.replace("ESY26-", ""))
            for a in df["account_id"].tolist() if isinstance(a, str)
        }
        current_members = s.execute(
            select(CustomListMember).where(CustomListMember.list_id == list_id)
        ).scalars().all()
        current_ids = {m.exhibitor_id for m in current_members}
        to_add = target_ids - current_ids
        to_remove_ids = current_ids - target_ids
        added = 0
        for eid in to_add:
            s.add(CustomListMember(list_id=list_id, exhibitor_id=eid))
            added += 1
        removed = 0
        if to_remove_ids:
            for m in current_members:
                if m.exhibitor_id in to_remove_ids:
                    s.delete(m)
                    removed += 1
        cl.last_synced_at = _dt.utcnow()
        s.commit()
        return {"added": added, "removed": removed, "total": len(target_ids)}
    finally:
        s.close()


def get_custom_list(list_id: int) -> CustomList | None:
    s = SessionLocal()
    try:
        return s.get(CustomList, list_id)
    finally:
        s.close()


def get_member_note(list_id: int, exhibitor_id: int) -> Optional[str]:
    s = SessionLocal()
    try:
        m = s.scalar(
            select(CustomListMember).where(
                CustomListMember.list_id == list_id,
                CustomListMember.exhibitor_id == exhibitor_id,
            )
        )
        return m.note if m else None
    finally:
        s.close()


def set_member_note(list_id: int, exhibitor_id: int,
                    note: Optional[str]) -> None:
    s = SessionLocal()
    try:
        m = s.scalar(
            select(CustomListMember).where(
                CustomListMember.list_id == list_id,
                CustomListMember.exhibitor_id == exhibitor_id,
            )
        )
        if m is None:
            return
        from datetime import datetime as _dt
        m.note = (note or "").strip() or None
        m.note_updated_at = _dt.utcnow() if m.note else None
        s.commit()
    finally:
        s.close()


def lists_for_exhibitor(exhibitor_id: int) -> list[dict]:
    """Return every (non-archived) custom list that contains this exhibitor.

    Each dict: ``{list_id, name, color, owner, sales_team, is_pinned,
    is_dynamic, member_count, note}`` — the per-membership note included
    so the fiche can surface campaign-specific context inline.
    """
    s = SessionLocal()
    try:
        from sqlalchemy import func as _f
        rows = s.execute(
            select(
                CustomList.id, CustomList.name, CustomList.color,
                CustomList.owner, CustomList.sales_team,
                CustomList.is_pinned, CustomList.is_dynamic,
                CustomListMember.note,
            )
            .join(CustomListMember,
                   CustomListMember.list_id == CustomList.id)
            .where(
                CustomListMember.exhibitor_id == exhibitor_id,
                CustomList.archived_at.is_(None),
            )
            .order_by(CustomList.is_pinned.desc(), CustomList.name)
        ).all()
        out = []
        for lid, name, color, owner, team, pinned, dynamic, note in rows:
            count = s.scalar(
                select(_f.count(CustomListMember.id))
                .where(CustomListMember.list_id == lid)
            ) or 0
            out.append({
                "list_id": lid, "name": name, "color": color,
                "owner": owner, "sales_team": team,
                "is_pinned": bool(pinned),
                "is_dynamic": bool(dynamic),
                "member_count": int(count),
                "note": note,
            })
        return out
    finally:
        s.close()


def list_activity_timeline(list_id: int, limit: int = 80) -> list[dict]:
    """Return the most recent ``ActivityLog`` entries for the members of a
    list, joined with the company name.

    Each entry: ``{id, exhibitor_id, account_name, kind, text, author,
    created_at}``. Sorted DESC by ``created_at``.
    """
    from app.database import ActivityLog, Exhibitor
    s = SessionLocal()
    try:
        member_ids = [
            m for m, in s.execute(
                select(CustomListMember.exhibitor_id)
                .where(CustomListMember.list_id == list_id)
            ).all()
        ]
        if not member_ids:
            return []
        rows = s.execute(
            select(ActivityLog, Exhibitor.company_name)
            .join(Exhibitor, Exhibitor.id == ActivityLog.exhibitor_id)
            .where(ActivityLog.exhibitor_id.in_(member_ids))
            .order_by(ActivityLog.created_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": log.id,
                "exhibitor_id": log.exhibitor_id,
                "account_name": name,
                "kind": log.kind,
                "text": log.text,
                "author": log.author,
                "created_at": log.created_at,
            }
            for log, name in rows
        ]
    finally:
        s.close()


def list_member_notes_map(list_id: int) -> dict[int, str]:
    """Return ``{exhibitor_id: note}`` for the members of a list (notes only)."""
    s = SessionLocal()
    try:
        rows = s.execute(
            select(CustomListMember.exhibitor_id, CustomListMember.note)
            .where(
                CustomListMember.list_id == list_id,
                CustomListMember.note.is_not(None),
            )
        ).all()
        return {eid: note for eid, note in rows if note}
    finally:
        s.close()


def list_member_ids(list_id: int) -> list[int]:
    s = SessionLocal()
    try:
        return [
            r[0] for r in s.execute(
                select(CustomListMember.exhibitor_id)
                .where(CustomListMember.list_id == list_id)
            ).all()
        ]
    finally:
        s.close()


def duplicate_list(source_id: int, new_name: str) -> int:
    """Clone a list (metadata + members) under a new name."""
    src = get_custom_list(source_id)
    if src is None:
        raise ValueError("source list not found")
    new_id = create_custom_list(
        name=new_name,
        description=src.description,
        owner=src.owner,
        criteria=src.criteria_json,
        color=src.color,
    )
    update_custom_list(new_id, sales_team=src.sales_team)
    add_to_list(new_id, list_member_ids(source_id))
    return new_id


def merge_lists(source_ids: list[int], new_name: str,
                description: Optional[str] = None,
                owner: Optional[str] = None) -> int:
    """Create a new list whose members are the UNION of the input lists."""
    if not source_ids:
        raise ValueError("at least one source list required")
    union_ids: set[int] = set()
    for sid in source_ids:
        union_ids.update(list_member_ids(sid))
    new_id = create_custom_list(
        name=new_name, description=description, owner=owner,
    )
    add_to_list(new_id, sorted(union_ids))
    return new_id


def list_overlap(list_a_id: int, list_b_id: int) -> dict:
    """Compute the membership overlap between two lists.

    Returns a dict with three lists of exhibitor ids: ``in_both``, ``a_only``,
    ``b_only`` plus the totals.
    """
    a = set(list_member_ids(list_a_id))
    b = set(list_member_ids(list_b_id))
    return {
        "in_both": sorted(a & b),
        "a_only": sorted(a - b),
        "b_only": sorted(b - a),
        "a_total": len(a),
        "b_total": len(b),
    }


def delete_list(list_id: int) -> None:
    s = SessionLocal()
    try:
        cl = s.get(CustomList, list_id)
        if cl is None:
            return
        s.delete(cl)
        s.commit()
    finally:
        s.close()
