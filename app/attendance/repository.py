"""Load + filter ``AttendanceSignal`` rows for the UI and exports."""
from __future__ import annotations

from typing import Optional

import pandas as pd
from sqlalchemy import select

from app.database import AttendanceSignal, SessionLocal


# All columns surfaced in the UI / exports (FE order, not storage order).
ALL_COLUMNS = [
    "id",
    "edition_year",
    "entity_type",
    "person_name",
    "person_role",
    "role_category",
    "company_name",
    "country",
    "country_iso2",
    "signal_type",
    "presence_score",
    "presence_confidence",
    "commercial_relevance",
    "sales_priority",
    "meeting_potential",
    "next_best_action",
    "reason_to_contact",
    "recommended_angle",
    "is_official_delegation",
    "is_exhibitor_employee",
    "is_company_post",
    "is_personal_post",
    "canonical_company_name",
    "canonical_person_name",
    "dedupe_key",
    "duplicate_group_id",
    "is_duplicate",
    "source_platform",
    "source_url",
    "source_title",
    "source_snippet",
    "search_query_used",
    "signal_strength_reason",
    "gdpr_risk_level",
    "manual_validation_status",
    "notes",
    "first_seen_at",
    "last_checked_at",
]

# Default visible columns in the table
DEFAULT_TABLE_COLUMNS = [
    "sales_priority",
    "presence_score",
    "person_name",
    "role_category",
    "company_name",
    "country",
    "edition_year",
    "signal_type",
    "presence_confidence",
    "commercial_relevance",
    "next_best_action",
    "source_url",
]


def signals_dataframe(*, include_duplicates: bool = False) -> pd.DataFrame:
    """Load attendance signals into a DataFrame.

    Adds a derived ``is_known_exhibitor`` column : True when the signal's
    canonical (or raw) company name matches an exhibitor in the catalog.
    The Attendance Signals page is meant to surface POTENTIAL VISITORS,
    not exhibitors — exhibitors are already covered by the Companies tab.
    """
    s = SessionLocal()
    try:
        q = select(AttendanceSignal)
        if not include_duplicates:
            q = q.where(AttendanceSignal.is_duplicate.is_(False))
        rows = []
        for r in s.execute(q).scalars():
            rows.append({c: getattr(r, c, None) for c in ALL_COLUMNS})
        # Build a normalised exhibitor name → name lookup so we can
        # surface the matched catalog name on each signal — the operator
        # sees at a glance which contacts work at a known exhibitor and
        # can then open the fiche from the detail card.
        from app.database import Exhibitor
        catalog: dict[str, str] = {}
        for (cn,) in s.execute(select(Exhibitor.company_name)).all():
            if cn:
                catalog[" ".join(str(cn).lower().split())] = cn
    finally:
        s.close()
    df = pd.DataFrame(rows, columns=ALL_COLUMNS)
    if df.empty:
        df["is_known_exhibitor"] = []
        df["matched_exhibitor"] = []
        df["derived_email"] = []
        df["derived_phone"] = []
        df["derived_linkedin"] = []
        return df

    # Extract email + phone + linkedin URLs from the free-form ``notes``
    # field and surface them as proper columns. Most signals coming from
    # the GICAT importer carry "Direct email: x@y. Phone: +33 …" in
    # notes — pulling them out lets the operator see at a glance which
    # contacts have direct outreach info.
    import re as _re
    _email_rx = _re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    _phone_rx = _re.compile(r"\+\d[\d\s().\-]{8,}\d")
    _linkedin_rx = _re.compile(
        r"https?://(?:www\.|fr\.)?linkedin\.com/[^\s,]+",
        _re.IGNORECASE,
    )

    def _derive(row: pd.Series) -> tuple[Optional[str], Optional[str], Optional[str]]:
        notes = str(row.get("notes") or "")
        em = _email_rx.search(notes)
        ph = _phone_rx.search(notes)
        # LinkedIn from source_url first, then notes
        src = str(row.get("source_url") or "")
        li = src if "linkedin.com/" in src.lower() else None
        if not li:
            lm = _linkedin_rx.search(notes)
            if lm:
                li = lm.group(0).rstrip(".,;:)")
        return (
            em.group(0) if em else None,
            ph.group(0).strip() if ph else None,
            li,
        )

    derived = df.apply(_derive, axis=1, result_type="expand")
    derived.columns = ["derived_email", "derived_phone", "derived_linkedin"]
    df = pd.concat([df, derived], axis=1)

    def _match_exhibitor(row) -> tuple[bool, Optional[str]]:
        """Return ``(is_company_signal_matching_exhibitor, matched_name)``.

        The matched name is filled in for ALL signals (incl. persons)
        whose company is in the catalog — so the UI can show a
        "🛡 Exposant" column to indicate the cross-link.

        ``is_known_exhibitor`` (the legacy filter flag) only flips to
        True for company-type signals, never persons.
        """
        matched: Optional[str] = None
        is_person = (row.get("entity_type") or "").lower() == "person"
        for col in ("canonical_company_name", "company_name"):
            v = row.get(col)
            if not v:
                continue
            norm = " ".join(str(v).lower().split())
            cat_hit = catalog.get(norm)
            if cat_hit:
                matched = cat_hit
                break
            if len(norm) >= 4:
                for cn, raw in catalog.items():
                    if len(cn) < 4:
                        continue
                    if cn in norm or norm in cn:
                        matched = raw
                        break
                if matched:
                    break
        is_known_exhibitor_flag = bool(matched and not is_person)
        return is_known_exhibitor_flag, matched

    matched_results = df.apply(_match_exhibitor, axis=1, result_type="expand")
    matched_results.columns = ["is_known_exhibitor", "matched_exhibitor"]
    df = pd.concat([df, matched_results], axis=1)

    # Derive trade-association enrichment fields from the ``notes`` payload.
    # We support both ADS Group UK (``[ads-*]`` markers) and BDSV Germany
    # (``[bdsv-*]`` markers) — the column names are generic so the table
    # works uniformly across sources.
    _ads_cat_rx = _re.compile(r"\[ads-categories\]\s*([^\n]+)")
    _ads_tier_rx = _re.compile(r"\[ads-tier\]\s*([^\n]+)")
    _ads_detail_rx = _re.compile(
        r"\[ads-detail\]\s*(\{.*?\})\s*(?:\n|$)", _re.DOTALL,
    )
    _bdsv_cat_rx = _re.compile(r"\[bdsv-categories\]\s*([^\n]+)")
    _bdsv_tier_rx = _re.compile(r"\[bdsv-tier\]\s*([^\n]+)")
    _gicat_cat_rx = _re.compile(r"\[gicat-categories\]\s*([^\n]+)")
    _gicat_tier_rx = _re.compile(r"\[gicat-tier\]\s*([^\n]+)")
    _aiad_cat_rx = _re.compile(r"\[aiad-categories\]\s*([^\n]+)")
    _aiad_tier_rx = _re.compile(r"\[aiad-tier\]\s*([^\n]+)")

    def _derive_assoc(row: pd.Series) -> tuple[
        Optional[str], Optional[str], Optional[int], Optional[str]
    ]:
        notes = str(row.get("notes") or "")
        cat = tier = None
        # Prefer ADS (UK), else BDSV (DE), else GICAT (FR), else AIAD (IT)
        for cat_rx in (_ads_cat_rx, _bdsv_cat_rx, _gicat_cat_rx, _aiad_cat_rx):
            if (m := cat_rx.search(notes)):
                v = m.group(1).strip()
                if v and v != "(aucune)":
                    cat = v
                    break
        for tier_rx in (_ads_tier_rx, _bdsv_tier_rx, _gicat_tier_rx, _aiad_tier_rx):
            if (m := tier_rx.search(notes)):
                tv = m.group(1).strip()
                if tv:
                    tier = tv
                    break
        n_caps: Optional[int] = None
        capabilities: Optional[str] = None
        if (m := _ads_detail_rx.search(notes)):
            try:
                import json as _json
                blob = _json.loads(m.group(1))
                kw = blob.get("knowsAbout") or []
                n_caps = len(kw)
                capabilities = " · ".join(kw[:30])
                if len(kw) > 30:
                    capabilities += f" · (+{len(kw)-30} autres)"
            except Exception:  # noqa: BLE001
                pass
        return cat, tier, n_caps, capabilities

    assoc_results = df.apply(_derive_assoc, axis=1, result_type="expand")
    assoc_results.columns = [
        "ads_product_categories",
        "ads_supply_chain_tier",
        "ads_capabilities_count",
        "ads_capabilities",
    ]
    df = pd.concat([df, assoc_results], axis=1)

    # Derive ``company_type`` (8-bucket closed-list, same taxonomy as the
    # Companies tab). Used by the Attendance filters so a rep can slice
    # signals by who the company is (OEM, software vendor, distributor…).
    from app.crm.normalizers import derive_company_type

    def _split_pipe_or_dot(v) -> list[str]:
        if not isinstance(v, str) or not v:
            return []
        sep = "·" if "·" in v else ","
        return [c.strip() for c in v.split(sep) if c.strip()]

    df["company_type"] = df.apply(
        lambda r: derive_company_type(
            business_model=None,
            supply_chain_tier=r.get("ads_supply_chain_tier"),
            products_categories=_split_pipe_or_dot(
                r.get("ads_product_categories")
            ),
            activity_1liner=None,
        ),
        axis=1,
    )

    # Geographic zone — same 5-bucket coarse region used by Companies.
    from app.crm.normalizers import country_to_zone
    df["zone"] = df.apply(
        lambda r: country_to_zone(
            iso2=r.get("country_iso2"),
            country_name=r.get("country"),
        ),
        axis=1,
    )
    return df


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def apply_filters(
    df: pd.DataFrame,
    *,
    years: Optional[list[int]] = None,
    countries: Optional[list[str]] = None,
    role_categories: Optional[list[str]] = None,
    entity_types: Optional[list[str]] = None,
    sales_priorities: Optional[list[str]] = None,
    presence_confidences: Optional[list[str]] = None,
    commercial_relevances: Optional[list[str]] = None,
    signal_types: Optional[list[str]] = None,
    gdpr_levels: Optional[list[str]] = None,
    validation_statuses: Optional[list[str]] = None,
    is_official_delegation: Optional[bool] = None,
    is_exhibitor_employee: Optional[bool] = None,
    min_presence_score: int = 0,
    search_text: Optional[str] = None,
    exclude_known_exhibitors: bool = False,
    platforms: Optional[list[str]] = None,
    company_types: Optional[list[str]] = None,
    zones: Optional[list[str]] = None,
) -> pd.DataFrame:
    out = df.copy()
    if exclude_known_exhibitors and "is_known_exhibitor" in out.columns:
        out = out[~out["is_known_exhibitor"].fillna(False)]
    if years:
        out = out[out["edition_year"].isin(years)]
    if zones and "zone" in out.columns:
        out = out[out["zone"].isin(zones)]
    if countries:
        out = out[out["country"].isin(countries)]
    if role_categories:
        out = out[out["role_category"].isin(role_categories)]
    if entity_types:
        out = out[out["entity_type"].isin(entity_types)]
    if sales_priorities:
        out = out[out["sales_priority"].isin(sales_priorities)]
    if presence_confidences:
        out = out[out["presence_confidence"].isin(presence_confidences)]
    if commercial_relevances:
        out = out[out["commercial_relevance"].isin(commercial_relevances)]
    if signal_types:
        out = out[out["signal_type"].isin(signal_types)]
    if gdpr_levels:
        out = out[out["gdpr_risk_level"].isin(gdpr_levels)]
    if validation_statuses:
        out = out[out["manual_validation_status"].isin(validation_statuses)]
    if is_official_delegation is not None:
        out = out[out["is_official_delegation"] == is_official_delegation]
    if is_exhibitor_employee is not None:
        out = out[out["is_exhibitor_employee"] == is_exhibitor_employee]
    if platforms and "source_platform" in out.columns:
        out = out[out["source_platform"].isin(platforms)]
    if company_types and "company_type" in out.columns:
        out = out[out["company_type"].isin(company_types)]
    if min_presence_score:
        out = out[out["presence_score"].fillna(0) >= min_presence_score]
    if search_text:
        s = search_text.lower()
        cols = ["person_name", "company_name", "country", "source_title", "signal_text", "source_snippet"]
        mask = pd.Series(False, index=out.index)
        for c in cols:
            if c in out.columns:
                mask = mask | out[c].fillna("").astype(str).str.lower().str.contains(s, na=False)
        out = out[mask]
    return out


# ---------------------------------------------------------------------------
# Saved views (presets) — buttons in the UI
# ---------------------------------------------------------------------------

VIEW_PRESETS: dict[str, dict] = {
    "A priority people": {
        "sales_priorities": ["A"],
        "entity_types": ["person"],
    },
    "A priority companies": {
        "sales_priorities": ["A"],
        "entity_types": ["company", "delegation", "institution"],
    },
    "2026 likely attendees": {
        "years": [2026],
        "presence_confidences": ["High", "Medium"],
    },
    "2024 confirmed presence": {
        "years": [2024],
        "presence_confidences": ["High"],
    },
    "Official delegations": {
        "is_official_delegation": True,
    },
    "Procurement / Partnerships": {
        "role_categories": ["Procurement / Purchasing", "Partnerships"],
    },
    "Sales / BD contacts": {
        "role_categories": ["Sales / Business Development", "CEO / Founder"],
    },
    "Needs manual validation": {
        "validation_statuses": ["Review required"],
    },
    "Low GDPR risk only": {
        "gdpr_levels": ["Low"],
    },
}


def get_signal(signal_id: int) -> Optional[AttendanceSignal]:
    """Return one signal by id (None if absent)."""
    s = SessionLocal()
    try:
        return s.get(AttendanceSignal, signal_id)
    finally:
        s.close()


def set_signal_validation(signal_id: int, status: str,
                           note: Optional[str] = None) -> None:
    """Update the manual validation status for a single signal.

    ``status`` must be one of ``Pending`` / ``Review required`` /
    ``Validated`` / ``Rejected``. Optional ``note`` is appended to the
    existing notes.
    """
    s = SessionLocal()
    try:
        sig = s.get(AttendanceSignal, signal_id)
        if sig is None:
            return
        sig.manual_validation_status = status
        if note:
            existing = (sig.notes or "").strip()
            sep = "\n\n" if existing else ""
            sig.notes = f"{existing}{sep}[{status}] {note}"
        s.commit()
    finally:
        s.close()


def sibling_signals(signal_id: int, limit: int = 10) -> list[dict]:
    """Return up to ``limit`` other signals that share the same dedupe_key
    or the same canonical company / person as the given signal.

    Useful for the detail view: shows the full cluster of public signals
    pointing at the same event/announcement.
    """
    s = SessionLocal()
    try:
        sig = s.get(AttendanceSignal, signal_id)
        if sig is None:
            return []
        cond = []
        if sig.dedupe_key:
            cond.append(AttendanceSignal.dedupe_key == sig.dedupe_key)
        if sig.canonical_company_name:
            cond.append(AttendanceSignal.canonical_company_name
                        == sig.canonical_company_name)
        if sig.canonical_person_name:
            cond.append(AttendanceSignal.canonical_person_name
                        == sig.canonical_person_name)
        if not cond:
            return []
        from sqlalchemy import or_
        q = (
            select(AttendanceSignal)
            .where(AttendanceSignal.id != signal_id)
            .where(or_(*cond))
            .order_by(AttendanceSignal.first_seen_at.desc())
            .limit(limit)
        )
        out = []
        for r in s.execute(q).scalars():
            out.append({
                "id": r.id,
                "edition_year": r.edition_year,
                "entity_type": r.entity_type,
                "person_name": r.person_name,
                "company_name": r.company_name,
                "country": r.country,
                "signal_type": r.signal_type,
                "source_url": r.source_url,
                "source_title": r.source_title,
                "presence_score": r.presence_score,
                "first_seen_at": r.first_seen_at,
                "manual_validation_status": r.manual_validation_status,
            })
        return out
    finally:
        s.close()


_WATCH_KINDS = ["keyword", "company", "person", "country", "role_category"]


def list_watches() -> list[dict]:
    """Return all attendance watches, oldest first."""
    from app.database import AttendanceWatch
    s = SessionLocal()
    try:
        rows = s.execute(
            select(AttendanceWatch).order_by(AttendanceWatch.created_at)
        ).scalars().all()
        return [
            {
                "id": w.id,
                "kind": w.kind,
                "term": w.term,
                "label": w.label,
                "owner": w.owner,
                "last_seen_signal_id": w.last_seen_signal_id or 0,
                "created_at": w.created_at,
            }
            for w in rows
        ]
    finally:
        s.close()


def add_watch(kind: str, term: str, label: Optional[str] = None,
              owner: Optional[str] = None) -> int:
    """Create a new watch (returns the new id)."""
    from app.database import AttendanceWatch
    if kind not in _WATCH_KINDS:
        raise ValueError(f"Invalid watch kind: {kind}")
    s = SessionLocal()
    try:
        w = AttendanceWatch(
            kind=kind, term=term.strip(),
            label=(label or "").strip() or None,
            owner=(owner or "").strip() or None,
            last_seen_signal_id=0,
        )
        s.add(w)
        s.commit()
        s.refresh(w)
        return int(w.id)
    finally:
        s.close()


def delete_watch(watch_id: int) -> None:
    from app.database import AttendanceWatch
    s = SessionLocal()
    try:
        w = s.get(AttendanceWatch, watch_id)
        if w:
            s.delete(w)
            s.commit()
    finally:
        s.close()


def mark_watch_seen(watch_id: int) -> None:
    """Bump ``last_seen_signal_id`` to the current max id matching the watch."""
    from app.database import AttendanceWatch
    s = SessionLocal()
    try:
        w = s.get(AttendanceWatch, watch_id)
        if w is None:
            return
        ids = _watch_match_ids(s, w.kind, w.term)
        if ids:
            w.last_seen_signal_id = max(ids)
            s.commit()
    finally:
        s.close()


def _watch_match_ids(s, kind: str, term: str) -> list[int]:
    """Return ids of attendance signals matching this watch (helper)."""
    q = select(AttendanceSignal.id).where(
        AttendanceSignal.is_duplicate.is_(False)
    )
    norm = (term or "").strip().lower()
    if not norm:
        return []
    if kind == "keyword":
        from sqlalchemy import or_
        like = f"%{norm}%"
        q = q.where(or_(
            AttendanceSignal.signal_text.ilike(like),
            AttendanceSignal.source_title.ilike(like),
            AttendanceSignal.person_name.ilike(like),
            AttendanceSignal.company_name.ilike(like),
            AttendanceSignal.canonical_company_name.ilike(like),
            AttendanceSignal.canonical_person_name.ilike(like),
        ))
    elif kind == "company":
        from sqlalchemy import or_
        like = f"%{norm}%"
        q = q.where(or_(
            AttendanceSignal.canonical_company_name.ilike(like),
            AttendanceSignal.company_name.ilike(like),
        ))
    elif kind == "person":
        from sqlalchemy import or_
        like = f"%{norm}%"
        q = q.where(or_(
            AttendanceSignal.canonical_person_name.ilike(like),
            AttendanceSignal.person_name.ilike(like),
        ))
    elif kind == "country":
        q = q.where(AttendanceSignal.country.ilike(term))
    elif kind == "role_category":
        q = q.where(AttendanceSignal.role_category == term)
    return [int(i) for (i,) in s.execute(q).all()]


def watch_unread_signals(watch_id: int, limit: int = 20) -> list[dict]:
    """Signals matching the watch with id > last_seen_signal_id."""
    from app.database import AttendanceWatch
    s = SessionLocal()
    try:
        w = s.get(AttendanceWatch, watch_id)
        if w is None:
            return []
        ids = _watch_match_ids(s, w.kind, w.term)
        threshold = int(w.last_seen_signal_id or 0)
        unread_ids = [i for i in ids if i > threshold]
        if not unread_ids:
            return []
        rows = s.execute(
            select(AttendanceSignal)
            .where(AttendanceSignal.id.in_(unread_ids))
            .order_by(AttendanceSignal.first_seen_at.desc())
            .limit(limit)
        ).scalars().all()
        return [
            {
                "id": r.id,
                "edition_year": r.edition_year,
                "person_name": r.person_name,
                "company_name": r.company_name,
                "country": r.country,
                "signal_type": r.signal_type,
                "source_url": r.source_url,
                "first_seen_at": r.first_seen_at,
                "presence_score": r.presence_score,
            }
            for r in rows
        ]
    finally:
        s.close()


def list_duplicate_clusters(limit: int = 30) -> list[dict]:
    """Return clusters of signals sharing a ``dedupe_key`` (≥ 2 rows).

    Each cluster: ``{dedupe_key, size, primary_id, primary_label,
    edition_year, sample_signals}``. Sorted by size DESC then primary id.
    """
    s = SessionLocal()
    try:
        from sqlalchemy import func as _f
        # Find dedupe_keys with more than one row (regardless of is_duplicate)
        sub = (
            select(AttendanceSignal.dedupe_key,
                    _f.count(AttendanceSignal.id).label("n"))
            .where(AttendanceSignal.dedupe_key.is_not(None))
            .group_by(AttendanceSignal.dedupe_key)
            .having(_f.count(AttendanceSignal.id) >= 2)
            .order_by(_f.count(AttendanceSignal.id).desc())
            .limit(limit)
        )
        groups = s.execute(sub).all()
        out = []
        for key, n in groups:
            members = s.execute(
                select(AttendanceSignal)
                .where(AttendanceSignal.dedupe_key == key)
                .order_by(AttendanceSignal.is_duplicate.asc(),
                          AttendanceSignal.first_seen_at.desc())
            ).scalars().all()
            primary = next(
                (m for m in members if not m.is_duplicate), members[0]
            )
            label = (
                primary.canonical_company_name or primary.company_name
                or primary.canonical_person_name or primary.person_name
                or "(sans nom)"
            )
            out.append({
                "dedupe_key": key,
                "size": int(n),
                "primary_id": primary.id,
                "primary_label": label,
                "edition_year": primary.edition_year,
                "sample_signals": [
                    {
                        "id": m.id,
                        "is_duplicate": m.is_duplicate,
                        "signal_type": m.signal_type,
                        "source_url": m.source_url,
                        "source_platform": m.source_platform,
                        "first_seen_at": m.first_seen_at,
                        "presence_score": m.presence_score,
                        "manual_validation_status": m.manual_validation_status,
                    }
                    for m in members
                ],
            })
        return out
    finally:
        s.close()


def merge_duplicate_cluster(dedupe_key: str, primary_id: int) -> int:
    """Mark every signal in the cluster (except ``primary_id``) as
    ``is_duplicate=True`` with ``duplicate_group_id = primary_id``.

    Returns the number of rows flipped to duplicate.
    """
    s = SessionLocal()
    try:
        members = s.execute(
            select(AttendanceSignal)
            .where(AttendanceSignal.dedupe_key == dedupe_key)
        ).scalars().all()
        flipped = 0
        for m in members:
            if m.id == primary_id:
                m.is_duplicate = False
                m.duplicate_group_id = None
            else:
                if not m.is_duplicate or m.duplicate_group_id != primary_id:
                    m.is_duplicate = True
                    m.duplicate_group_id = primary_id
                    flipped += 1
        s.commit()
        return flipped
    finally:
        s.close()


def find_exhibitor_for_signal(signal_id: int) -> Optional[dict]:
    """Try to map an attendance signal to a catalog exhibitor.

    Matches by canonical name first, then raw company name (case-insensitive,
    normalised whitespace). Returns ``{exhibitor_id, account_id, account_name,
    country}`` if a match is found.
    """
    s = SessionLocal()
    try:
        sig = s.get(AttendanceSignal, signal_id)
        if sig is None:
            return None
        from app.database import Exhibitor
        candidates = [sig.canonical_company_name, sig.company_name]
        for raw in candidates:
            if not raw:
                continue
            norm = " ".join(str(raw).lower().split())
            row = s.execute(
                select(Exhibitor).where(
                    Exhibitor.company_name.ilike(raw)
                )
            ).scalar()
            if row is None:
                row = s.execute(
                    select(Exhibitor).where(
                        Exhibitor.company_name.ilike(f"%{norm}%")
                    ).limit(1)
                ).scalar()
            if row is not None:
                return {
                    "exhibitor_id": row.id,
                    "account_id": f"ESY26-{row.id}",
                    "account_name": row.company_name,
                    "country": row.country,
                }
        return None
    finally:
        s.close()


def signals_for_exhibitor(exhibitor_id: int, limit: int = 20) -> list[dict]:
    """Return attendance signals attached to one catalog exhibitor.

    Matched by canonical / raw company name on the catalog company.
    Used by the company fiche to surface the OSINT cluster around it.
    """
    s = SessionLocal()
    try:
        from app.database import Exhibitor
        exh = s.get(Exhibitor, exhibitor_id)
        if exh is None or not exh.company_name:
            return []
        from sqlalchemy import or_
        name = exh.company_name
        rows = s.execute(
            select(AttendanceSignal)
            .where(or_(
                AttendanceSignal.canonical_company_name.ilike(name),
                AttendanceSignal.company_name.ilike(name),
                AttendanceSignal.canonical_company_name.ilike(f"%{name}%"),
                AttendanceSignal.company_name.ilike(f"%{name}%"),
            ))
            .where(AttendanceSignal.is_duplicate.is_(False))
            .order_by(AttendanceSignal.first_seen_at.desc())
            .limit(limit)
        ).scalars().all()
        return [
            {
                "id": r.id,
                "edition_year": r.edition_year,
                "person_name": r.person_name,
                "person_role": r.person_role,
                "role_category": r.role_category,
                "signal_type": r.signal_type,
                "source_url": r.source_url,
                "source_title": r.source_title,
                "presence_score": r.presence_score,
                "sales_priority": r.sales_priority,
                "first_seen_at": r.first_seen_at,
                "manual_validation_status": r.manual_validation_status,
            }
            for r in rows
        ]
    finally:
        s.close()


def kpis(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "total": 0, "a_priority": 0, "high_confidence": 0,
            "official_delegations": 0, "needs_validation": 0,
            "avg_score": 0.0,
        }
    return {
        "total": len(df),
        "a_priority": int((df["sales_priority"] == "A").sum()),
        "high_confidence": int((df["presence_confidence"] == "High").sum()),
        "official_delegations": int(df["is_official_delegation"].fillna(False).sum()),
        "needs_validation": int((df["manual_validation_status"] == "Review required").sum()),
        "avg_score": round(float(df["presence_score"].dropna().mean() or 0), 1),
    }
