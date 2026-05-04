"""CRM-ready CSV export for ``AttendanceSignal``.

Exact column set defined by the spec:
- company_name, person_name, job_title, role_category, country, signal_year,
  presence_confidence, presence_score, commercial_relevance, sales_priority,
  target_category, reason_to_contact, recommended_angle, next_best_action,
  source_url, gdpr_risk_level, manual_validation_status, notes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from app.attendance.repository import apply_filters, signals_dataframe
from app.config import EXPORT_DIR

CRM_COLUMNS_OUT = [
    "company_name",
    "person_name",
    "job_title",
    "role_category",
    "country",
    "signal_year",
    "presence_confidence",
    "presence_score",
    "commercial_relevance",
    "sales_priority",
    "target_category",
    "reason_to_contact",
    "recommended_angle",
    "next_best_action",
    "source_url",
    "gdpr_risk_level",
    "manual_validation_status",
    "notes",
]


def _to_crm(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.rename(columns={
        "person_role": "job_title",
        "edition_year": "signal_year",
        "entity_type": "target_category",
    })
    for c in CRM_COLUMNS_OUT:
        if c not in out.columns:
            out[c] = None
    out = out[CRM_COLUMNS_OUT]
    return out


def export_attendance_crm_csv(
    filters: Optional[dict] = None,
    path: Optional[Path] = None,
    include_duplicates: bool = False,
) -> Path:
    df = signals_dataframe(include_duplicates=include_duplicates)
    if filters:
        df = apply_filters(df, **filters)
    crm = _to_crm(df)
    p = path or EXPORT_DIR / f"attendance_signals_crm_export_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    crm.to_csv(p, index=False)
    return p


def export_attendance_full_csv(
    filters: Optional[dict] = None,
    path: Optional[Path] = None,
) -> Path:
    df = signals_dataframe(include_duplicates=True)
    if filters:
        df = apply_filters(df, **filters)
    p = path or EXPORT_DIR / f"attendance_signals_full_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    df.to_csv(p, index=False)
    return p
