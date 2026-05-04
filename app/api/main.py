"""FastAPI app exposing exhibitors, filters, exports and quality stats."""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import (
    CommercialNote,
    Exhibitor,
    ExhibitorClassification,
    ExhibitorContact,
    ExhibitorTag,
    SessionLocal,
)
from app.exports.exporters import export_dataframe
from app.pipelines.report import build_report

app = FastAPI(title="Eurosatory Scraper API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_session() -> Session:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ----- DTOs -----------------------------------------------------------------


class ExhibitorOut(BaseModel):
    id: int
    finderr_guid: str
    company_name: str
    country_iso2: Optional[str]
    country_name: Optional[str]
    city: Optional[str]
    website_url: Optional[str]
    contact_email: Optional[str]
    generic_sales_email: Optional[str]
    phone: Optional[str]
    linkedin_url: Optional[str]
    short_presentation: Optional[str]
    presentation: Optional[str]
    business_areas: Optional[list]
    stands: Optional[list]
    is_featured: bool
    is_new_exhibitor: bool
    priority_level: Optional[str]
    commercial_relevance_score: Optional[float]
    status: str
    needs_review: bool
    field_confidence: Optional[dict]
    field_sources: Optional[dict]
    last_scraped_at: Optional[datetime]
    last_enriched_at: Optional[datetime]
    last_classified_at: Optional[datetime]
    taxonomy_labels: list[str] = []
    tags: list[str] = []

    class Config:
        from_attributes = True


class ExhibitorDetail(ExhibitorOut):
    address1: Optional[str]
    address2: Optional[str]
    zip_code: Optional[str]
    state_province: Optional[str]
    twitter_url: Optional[str]
    facebook_url: Optional[str]
    youtube_url: Optional[str]
    keywords: Optional[list]
    score_breakdown: Optional[dict]
    contacts: list[dict] = []
    notes: list[dict] = []
    raw_search_payload: Optional[dict]
    raw_detail_payload: Optional[dict]


class TagIn(BaseModel):
    tag: str


class StatusIn(BaseModel):
    status: str


class NoteIn(BaseModel):
    note: str
    author: Optional[str] = None


# ----- helpers --------------------------------------------------------------


def _enrich_dto(s: Session, e: Exhibitor) -> dict:
    labels = sorted(
        {
            r.label
            for r in s.execute(
                select(ExhibitorClassification).where(ExhibitorClassification.exhibitor_id == e.id)
            ).scalars()
        }
    )
    tags = sorted(
        {
            r.tag
            for r in s.execute(
                select(ExhibitorTag).where(ExhibitorTag.exhibitor_id == e.id)
            ).scalars()
        }
    )
    base = ExhibitorOut.model_validate(e).model_dump()
    base["taxonomy_labels"] = labels
    base["tags"] = tags
    return base


# ----- routes ---------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "eurosatory-scraper", "version": "0.1.0"}


@app.get("/exhibitors", response_model=dict)
def list_exhibitors(
    s: Session = Depends(get_session),
    q: Optional[str] = Query(None, description="Search in name / city / presentation"),
    country: Optional[list[str]] = Query(None),
    priority: Optional[list[str]] = Query(None),
    status: Optional[list[str]] = Query(None),
    label: Optional[list[str]] = Query(None, description="Taxonomy label"),
    needs_review: Optional[bool] = Query(None),
    min_score: Optional[float] = Query(None),
    has_email: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("score_desc", description="score_desc | score_asc | name | country"),
) -> dict[str, Any]:
    stmt = select(Exhibitor)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                Exhibitor.company_name.ilike(like),
                Exhibitor.city.ilike(like),
                Exhibitor.short_presentation.ilike(like),
                Exhibitor.presentation.ilike(like),
            )
        )
    if country:
        stmt = stmt.where(Exhibitor.country_iso2.in_([c.upper() for c in country]))
    if priority:
        stmt = stmt.where(Exhibitor.priority_level.in_([p.upper() for p in priority]))
    if status:
        stmt = stmt.where(Exhibitor.status.in_(status))
    if needs_review is not None:
        stmt = stmt.where(Exhibitor.needs_review.is_(needs_review))
    if min_score is not None:
        stmt = stmt.where(Exhibitor.commercial_relevance_score >= min_score)
    if has_email is True:
        stmt = stmt.where(Exhibitor.contact_email.is_not(None))
    elif has_email is False:
        stmt = stmt.where(Exhibitor.contact_email.is_(None))
    if label:
        # join via classification
        sub = (
            select(ExhibitorClassification.exhibitor_id)
            .where(ExhibitorClassification.label.in_(label))
            .scalar_subquery()
        )
        stmt = stmt.where(Exhibitor.id.in_(sub))

    # sort
    if sort == "score_desc":
        stmt = stmt.order_by(Exhibitor.commercial_relevance_score.desc().nulls_last())
    elif sort == "score_asc":
        stmt = stmt.order_by(Exhibitor.commercial_relevance_score.asc().nulls_last())
    elif sort == "name":
        stmt = stmt.order_by(Exhibitor.company_name.asc())
    elif sort == "country":
        stmt = stmt.order_by(Exhibitor.country_iso2.asc(), Exhibitor.company_name.asc())

    total = len(list(s.execute(stmt).scalars().unique()))  # cheap & correct for SQLite size
    rows = list(
        s.execute(stmt.offset((page - 1) * page_size).limit(page_size)).scalars()
    )
    items = [_enrich_dto(s, r) for r in rows]
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


@app.get("/exhibitors/{exhibitor_id}", response_model=ExhibitorDetail)
def get_exhibitor(exhibitor_id: int, s: Session = Depends(get_session)) -> Any:
    e = s.get(Exhibitor, exhibitor_id)
    if not e:
        raise HTTPException(404, "exhibitor not found")
    base = _enrich_dto(s, e)
    detail = ExhibitorDetail.model_validate({**base, **{
        "address1": e.address1,
        "address2": e.address2,
        "zip_code": e.zip_code,
        "state_province": e.state_province,
        "twitter_url": e.twitter_url,
        "facebook_url": e.facebook_url,
        "youtube_url": e.youtube_url,
        "keywords": e.keywords,
        "score_breakdown": e.score_breakdown,
        "raw_search_payload": e.raw_search_payload,
        "raw_detail_payload": e.raw_detail_payload,
        "contacts": [
            {
                "full_name": c.full_name,
                "function": c.function,
                "email": c.email,
                "phone": c.phone,
                "linkedin": c.linkedin,
                "is_generic": c.is_generic,
                "confidence": c.confidence,
                "source_url": c.source_url,
            }
            for c in s.execute(
                select(ExhibitorContact).where(ExhibitorContact.exhibitor_id == e.id)
            ).scalars()
        ],
        "notes": [
            {"note": n.note, "author": n.author, "created_at": n.created_at}
            for n in s.execute(
                select(CommercialNote).where(CommercialNote.exhibitor_id == e.id)
            ).scalars()
        ],
    }}).model_dump()
    return detail


@app.post("/exhibitors/{exhibitor_id}/tags", status_code=201)
def add_tag(exhibitor_id: int, body: TagIn, s: Session = Depends(get_session)) -> dict:
    e = s.get(Exhibitor, exhibitor_id)
    if not e:
        raise HTTPException(404, "exhibitor not found")
    existing = s.scalar(
        select(ExhibitorTag).where(
            ExhibitorTag.exhibitor_id == exhibitor_id, ExhibitorTag.tag == body.tag
        )
    )
    if not existing:
        s.add(ExhibitorTag(exhibitor_id=exhibitor_id, tag=body.tag))
        s.commit()
    return {"ok": True}


@app.delete("/exhibitors/{exhibitor_id}/tags/{tag}")
def remove_tag(exhibitor_id: int, tag: str, s: Session = Depends(get_session)) -> dict:
    s.query(ExhibitorTag).filter_by(exhibitor_id=exhibitor_id, tag=tag).delete()
    s.commit()
    return {"ok": True}


@app.put("/exhibitors/{exhibitor_id}/status")
def set_status(exhibitor_id: int, body: StatusIn, s: Session = Depends(get_session)) -> dict:
    e = s.get(Exhibitor, exhibitor_id)
    if not e:
        raise HTTPException(404, "exhibitor not found")
    valid = {"new", "qualified", "to_contact", "contacted", "not_relevant"}
    if body.status not in valid:
        raise HTTPException(400, f"invalid status, expected one of {sorted(valid)}")
    e.status = body.status
    s.commit()
    return {"ok": True}


@app.put("/exhibitors/{exhibitor_id}/needs-review")
def set_needs_review(
    exhibitor_id: int,
    value: bool = Query(...),
    s: Session = Depends(get_session),
) -> dict:
    e = s.get(Exhibitor, exhibitor_id)
    if not e:
        raise HTTPException(404, "exhibitor not found")
    e.needs_review = value
    s.commit()
    return {"ok": True}


@app.post("/exhibitors/{exhibitor_id}/notes", status_code=201)
def add_note(exhibitor_id: int, body: NoteIn, s: Session = Depends(get_session)) -> dict:
    e = s.get(Exhibitor, exhibitor_id)
    if not e:
        raise HTTPException(404, "exhibitor not found")
    s.add(CommercialNote(exhibitor_id=exhibitor_id, note=body.note, author=body.author))
    s.commit()
    return {"ok": True}


@app.get("/labels")
def list_labels(s: Session = Depends(get_session)) -> dict:
    rows = s.execute(select(ExhibitorClassification.label)).all()
    counts: dict[str, int] = {}
    for (lbl,) in rows:
        counts[lbl] = counts.get(lbl, 0) + 1
    return counts


@app.get("/quality")
def quality() -> dict:
    return build_report()


@app.get("/export/{fmt}")
def export(
    fmt: str,
    country: Optional[list[str]] = Query(None),
    priority: Optional[list[str]] = Query(None),
    status: Optional[list[str]] = Query(None),
    min_score: Optional[float] = Query(None),
):
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(400, "fmt must be csv or xlsx")
    filters: dict = {}
    if country:
        filters["country_iso2"] = [c.upper() for c in country]
    if priority:
        filters["priority_level"] = [p.upper() for p in priority]
    if status:
        filters["status"] = status
    if min_score is not None:
        filters["min_score"] = min_score

    df = export_dataframe(filters)
    buf = io.BytesIO()
    if fmt == "csv":
        buf.write(df.to_csv(index=False).encode("utf-8"))
        media = "text/csv"
        filename = f"eurosatory_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    else:
        with __import__("pandas").ExcelWriter(buf, engine="openpyxl") as w:  # type: ignore[attr-defined]
            df.to_excel(w, index=False, sheet_name="Exhibitors")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"eurosatory_{datetime.utcnow():%Y%m%d_%H%M%S}.xlsx"
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
