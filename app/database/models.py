from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finderr_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True, index=True)
    code_iso2: Mapped[str] = mapped_column(String(2), unique=True, index=True)
    label_en: Mapped[Optional[str]] = mapped_column(String(120))
    label_fr: Mapped[Optional[str]] = mapped_column(String(120))


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finderr_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    code: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    category_type: Mapped[Optional[str]] = mapped_column(String(60))
    label_en: Mapped[Optional[str]] = mapped_column(String(255))
    label_fr: Mapped[Optional[str]] = mapped_column(String(255))
    parent_finderr_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    tags: Mapped[Optional[str]] = mapped_column(String(255))


class Exhibitor(Base, TimestampMixin):
    __tablename__ = "exhibitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finderr_guid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    finderr_external_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)

    # Identity
    company_name: Mapped[str] = mapped_column(String(400), index=True)
    company_name_alt: Mapped[Optional[str]] = mapped_column(String(400))
    slug: Mapped[Optional[str]] = mapped_column(String(400), index=True)

    # Location
    country_iso2: Mapped[Optional[str]] = mapped_column(String(2), index=True)
    country_name: Mapped[Optional[str]] = mapped_column(String(120))
    address1: Mapped[Optional[str]] = mapped_column(String(255))
    address2: Mapped[Optional[str]] = mapped_column(String(255))
    address3: Mapped[Optional[str]] = mapped_column(String(255))
    zip_code: Mapped[Optional[str]] = mapped_column(String(40))
    city: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    state_province: Mapped[Optional[str]] = mapped_column(String(120))

    # Contact
    website_url: Mapped[Optional[str]] = mapped_column(String(500), index=True)
    website_url_normalized: Mapped[Optional[str]] = mapped_column(String(500), index=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    generic_sales_email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(60))
    fax: Mapped[Optional[str]] = mapped_column(String(60))
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500))
    twitter_url: Mapped[Optional[str]] = mapped_column(String(500))
    facebook_url: Mapped[Optional[str]] = mapped_column(String(500))
    youtube_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Content
    short_presentation: Mapped[Optional[str]] = mapped_column(Text)
    presentation: Mapped[Optional[str]] = mapped_column(Text)
    one_liner: Mapped[Optional[str]] = mapped_column(Text)
    logo_url: Mapped[Optional[str]] = mapped_column(String(700))
    business_areas: Mapped[Optional[list]] = mapped_column(JSON)  # ["DEFENSE", ...]
    keywords: Mapped[Optional[list]] = mapped_column(JSON)  # extracted

    # Eurosatory metadata
    pavilion: Mapped[Optional[str]] = mapped_column(String(120))
    stands: Mapped[Optional[list]] = mapped_column(JSON)  # [{Hall, Name, MapPoint}]
    is_lab: Mapped[bool] = mapped_column(Boolean, default=False)
    is_new_exhibitor: Mapped[bool] = mapped_column(Boolean, default=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)

    # Approximate firmographics (best-effort)
    employee_range: Mapped[Optional[str]] = mapped_column(String(40))
    revenue_range: Mapped[Optional[str]] = mapped_column(String(40))

    # Scoring
    commercial_relevance_score: Mapped[Optional[float]] = mapped_column(Float)
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    priority_level: Mapped[Optional[str]] = mapped_column(String(2), index=True)

    # Data quality
    field_confidence: Mapped[Optional[dict]] = mapped_column(JSON)
    field_sources: Mapped[Optional[dict]] = mapped_column(JSON)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_classified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Commercial workflow
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    # status: new | qualified | to_contact | contacted | not_relevant
    owner: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    sales_team: Mapped[Optional[str]] = mapped_column(String(120))
    last_contact_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    next_action_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Raw audit blob
    raw_search_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    raw_detail_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    raw_enrichment_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    # relations
    contacts: Mapped[list["ExhibitorContact"]] = relationship(
        back_populates="exhibitor", cascade="all, delete-orphan"
    )
    enrichment_sources: Mapped[list["ExhibitorEnrichmentSource"]] = relationship(
        back_populates="exhibitor", cascade="all, delete-orphan"
    )
    classifications: Mapped[list["ExhibitorClassification"]] = relationship(
        back_populates="exhibitor", cascade="all, delete-orphan"
    )
    tags: Mapped[list["ExhibitorTag"]] = relationship(
        back_populates="exhibitor", cascade="all, delete-orphan"
    )
    notes: Mapped[list["CommercialNote"]] = relationship(
        back_populates="exhibitor", cascade="all, delete-orphan"
    )
    categories_link: Mapped[list["ExhibitorCategory"]] = relationship(
        back_populates="exhibitor", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_exhibitors_country_status", "country_iso2", "status"),
        Index("ix_exhibitors_score", "commercial_relevance_score"),
    )


class ExhibitorIntelligence(Base):
    """Premium commercial intelligence layer — kept separate from ``Exhibitor`` so
    the base catalog is usable on its own and intelligence runs are idempotent.
    """

    __tablename__ = "exhibitor_intelligence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), unique=True, index=True
    )

    # Discovery
    eurosatory_profile_url: Mapped[Optional[str]] = mapped_column(String(700))

    # Activity description (enriched)
    activity_summary: Mapped[Optional[str]] = mapped_column(Text)
    headline: Mapped[Optional[str]] = mapped_column(Text)  # one-liner from official site

    # Structured offering — every field carries provenance via field_sources/field_confidence
    built_products: Mapped[Optional[list]] = mapped_column(JSON)
    built_product_summary: Mapped[Optional[str]] = mapped_column(Text)
    sold_offerings: Mapped[Optional[list]] = mapped_column(JSON)
    services: Mapped[Optional[list]] = mapped_column(JSON)
    technologies: Mapped[Optional[list]] = mapped_column(JSON)
    target_clients: Mapped[Optional[list]] = mapped_column(JSON)
    markets_served: Mapped[Optional[list]] = mapped_column(JSON)
    programs_use_cases: Mapped[Optional[list]] = mapped_column(JSON)
    business_model: Mapped[Optional[str]] = mapped_column(String(80))
    company_type: Mapped[Optional[str]] = mapped_column(String(80))
    company_size_estimate: Mapped[Optional[str]] = mapped_column(String(40))
    founding_year: Mapped[Optional[int]] = mapped_column(Integer)
    employee_count_hint: Mapped[Optional[int]] = mapped_column(Integer)
    certifications: Mapped[Optional[list]] = mapped_column(JSON)
    industry_associations: Mapped[Optional[list]] = mapped_column(JSON)
    parent_group: Mapped[Optional[str]] = mapped_column(String(120))
    additional_offices: Mapped[Optional[list]] = mapped_column(JSON)

    # Buying intelligence
    probable_buying_needs: Mapped[Optional[list]] = mapped_column(JSON)
    buying_need_confidence: Mapped[Optional[str]] = mapped_column(String(10))
    buying_need_reasoning: Mapped[Optional[str]] = mapped_column(Text)
    supplier_needs: Mapped[Optional[list]] = mapped_column(JSON)
    partnership_opportunities: Mapped[Optional[list]] = mapped_column(JSON)

    # Defense taxonomy (English, multi-label)
    defense_categories: Mapped[Optional[list]] = mapped_column(JSON)

    # Commercial targeting
    commercial_target_type: Mapped[Optional[list]] = mapped_column(JSON)
    commercial_interest_level: Mapped[Optional[str]] = mapped_column(String(20))
    interest_reason: Mapped[Optional[str]] = mapped_column(Text)
    recommended_sales_angle: Mapped[Optional[str]] = mapped_column(Text)
    recommended_pitch: Mapped[Optional[str]] = mapped_column(Text)
    probable_objections: Mapped[Optional[list]] = mapped_column(JSON)
    prospecting_keywords: Mapped[Optional[list]] = mapped_column(JSON)
    summary_for_sales: Mapped[Optional[str]] = mapped_column(Text)

    # Defense scoring (separate from the basic relevance score)
    defense_commercial_score: Mapped[Optional[float]] = mapped_column(Float, index=True)
    defense_score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    defense_priority_level: Mapped[Optional[str]] = mapped_column(String(2), index=True)
    defense_maturity_score: Mapped[Optional[float]] = mapped_column(Float)

    # Provenance
    field_sources: Mapped[Optional[dict]] = mapped_column(JSON)
    field_confidence: Mapped[Optional[dict]] = mapped_column(JSON)
    fields_to_verify: Mapped[Optional[list]] = mapped_column(JSON)
    extraction_method: Mapped[Optional[str]] = mapped_column(String(40))  # rules | llm | mixed
    last_crawled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)


class CrawledPage(Base):
    """Audit log of every page (HTML or PDF) we fetched while building intelligence."""

    __tablename__ = "crawled_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(900), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # homepage | product | service | about | pdf | other
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    bytes: Mapped[Optional[int]] = mapped_column(Integer)
    text_excerpt: Mapped[Optional[str]] = mapped_column(Text)  # first 4 KB for audit
    error: Mapped[Optional[str]] = mapped_column(Text)


class ExhibitorCategory(Base):
    __tablename__ = "exhibitor_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    category_finderr_id: Mapped[int] = mapped_column(Integer, index=True)
    label_en: Mapped[Optional[str]] = mapped_column(String(255))
    label_fr: Mapped[Optional[str]] = mapped_column(String(255))

    exhibitor: Mapped[Exhibitor] = relationship(back_populates="categories_link")

    __table_args__ = (
        UniqueConstraint("exhibitor_id", "category_finderr_id", name="uq_exh_cat"),
    )


class ExhibitorContact(Base):
    __tablename__ = "exhibitor_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    function: Mapped[Optional[str]] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(60))
    linkedin: Mapped[Optional[str]] = mapped_column(String(500))
    is_generic: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[Optional[str]] = mapped_column(String(10))  # high / medium / low
    source_url: Mapped[Optional[str]] = mapped_column(String(500))

    exhibitor: Mapped[Exhibitor] = relationship(back_populates="contacts")


class ExhibitorEnrichmentSource(Base):
    __tablename__ = "exhibitor_enrichment_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(40))  # finderr_search, finderr_detail, website, ...
    source_url: Mapped[Optional[str]] = mapped_column(String(700))
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    fields_extracted: Mapped[Optional[list]] = mapped_column(JSON)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    exhibitor: Mapped[Exhibitor] = relationship(back_populates="enrichment_sources")


class ExhibitorClassification(Base):
    __tablename__ = "exhibitor_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(80), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    rationale: Mapped[Optional[str]] = mapped_column(Text)

    exhibitor: Mapped[Exhibitor] = relationship(back_populates="classifications")

    __table_args__ = (UniqueConstraint("exhibitor_id", "label", name="uq_exh_class"),)


class ExhibitorTag(Base):
    __tablename__ = "exhibitor_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    tag: Mapped[str] = mapped_column(String(80), index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    exhibitor: Mapped[Exhibitor] = relationship(back_populates="tags")

    __table_args__ = (UniqueConstraint("exhibitor_id", "tag", name="uq_exh_tag"),)


class CommercialNote(Base):
    __tablename__ = "commercial_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    note: Mapped[str] = mapped_column(Text)
    author: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    exhibitor: Mapped[Exhibitor] = relationship(back_populates="notes")


class AttendanceSignal(Base):
    """Public OSINT signal that a person, company, delegation or institution
    attended (or will attend) an Eurosatory edition.

    *Important:* the harness does **not** scrape LinkedIn or other paywalled
    sources.  Each row is created from manually-collected public material
    (corporate press releases, indexed public posts, official delegation press
    pages, Wayback Machine snapshots).  The system normalises, scores,
    deduplicates, and surfaces them — collection is the operator's job.
    """

    __tablename__ = "attendance_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    edition_year: Mapped[int] = mapped_column(Integer, index=True)

    # Entity
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    # person | company | delegation | institution | media | unknown

    person_name: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    person_role: Mapped[Optional[str]] = mapped_column(String(255))  # raw job title
    role_category: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(400), index=True)
    country: Mapped[Optional[str]] = mapped_column(String(120))
    country_iso2: Mapped[Optional[str]] = mapped_column(String(2))

    # Source
    source_platform: Mapped[Optional[str]] = mapped_column(String(40))
    # corporate_site | press | event_page | linkedin | x | wayback | other
    source_url: Mapped[str] = mapped_column(String(900))
    source_title: Mapped[Optional[str]] = mapped_column(String(400))
    source_snippet: Mapped[Optional[str]] = mapped_column(Text)
    search_query_used: Mapped[Optional[str]] = mapped_column(String(255))

    # Signal nature
    signal_type: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    # company_announcement | personal_linkedin_post | official_delegation
    # | national_pavilion | press_release | event_page | social_post | media_article
    signal_text: Mapped[Optional[str]] = mapped_column(Text)
    signal_strength_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Booleans
    is_company_post: Mapped[bool] = mapped_column(Boolean, default=False)
    is_personal_post: Mapped[bool] = mapped_column(Boolean, default=False)
    is_official_delegation: Mapped[bool] = mapped_column(Boolean, default=False)
    is_exhibitor_employee: Mapped[bool] = mapped_column(Boolean, default=False)

    # Scoring
    presence_score: Mapped[Optional[float]] = mapped_column(Float, index=True)
    presence_confidence: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    commercial_relevance: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    sales_priority: Mapped[Optional[str]] = mapped_column(String(2), index=True)
    meeting_potential: Mapped[Optional[str]] = mapped_column(String(10))
    target_type: Mapped[Optional[str]] = mapped_column(String(60))

    # Dedupe
    canonical_company_name: Mapped[Optional[str]] = mapped_column(String(400), index=True)
    canonical_person_name: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    duplicate_group_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # CRM action
    next_best_action: Mapped[Optional[str]] = mapped_column(String(120))
    reason_to_contact: Mapped[Optional[str]] = mapped_column(Text)
    recommended_angle: Mapped[Optional[str]] = mapped_column(Text)

    # GDPR / quality
    gdpr_risk_level: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    manual_validation_status: Mapped[str] = mapped_column(String(40), default="Pending", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Audit
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_attendance_year_priority", "edition_year", "sales_priority"),
        Index("ix_attendance_dedupe", "dedupe_key", "edition_year"),
    )


class AttendanceWatch(Base):
    """A saved watchlist term — alerts the operator when new
    ``AttendanceSignal`` rows match the term.

    ``kind`` is one of ``keyword`` (substring on signal_text/source_title/
    person_name/company_name) / ``company`` (canonical_company_name match)
    / ``person`` (canonical_person_name match) / ``country`` /
    ``role_category``.
    """

    __tablename__ = "attendance_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    term: Mapped[str] = mapped_column(String(255), index=True)
    label: Mapped[Optional[str]] = mapped_column(String(255))
    owner: Mapped[Optional[str]] = mapped_column(String(120))
    last_seen_signal_id: Mapped[Optional[int]] = mapped_column(
        Integer, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class ActivityLog(Base):
    """Single chronological audit log of all CRM-relevant actions on an
    exhibitor: status changes, tag adds, list assignments, contacted-today,
    manual notes.  Drives the timeline view in the company fiche.
    """

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    # status_change | tag_added | tag_removed | list_added | list_removed
    # | note | mark_contacted | review_flag | review_unflag | owner_changed
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[Optional[str]] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CustomList(Base):
    """Sales-team saved list — either a frozen membership (manually picked
    accounts) or a saved filter (criteria_json applied at read time).

    Both modes are supported simultaneously: a list can have explicit members
    AND a saved filter — the UI unions them.
    """

    __tablename__ = "custom_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    owner: Mapped[Optional[str]] = mapped_column(String(120))
    sales_team: Mapped[Optional[str]] = mapped_column(String(120))
    criteria_json: Mapped[Optional[dict]] = mapped_column(JSON)  # saved filter set
    color: Mapped[Optional[str]] = mapped_column(String(20))  # for badge in UI
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    # Smart list — when True, the membership is recomputed from criteria_json
    # at every open (existing per-membership notes are preserved).
    is_dynamic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class CustomListMember(Base):
    __tablename__ = "custom_list_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("custom_lists.id", ondelete="CASCADE"), index=True
    )
    exhibitor_id: Mapped[int] = mapped_column(
        ForeignKey("exhibitors.id", ondelete="CASCADE"), index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    added_by: Mapped[Optional[str]] = mapped_column(String(120))
    # List-scoped note: same exhibitor in two lists can carry two distinct
    # commercial notes (one per campaign).
    note: Mapped[Optional[str]] = mapped_column(Text)
    note_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("list_id", "exhibitor_id", name="uq_custom_list_member"),
    )


class ScrapingRun(Base):
    __tablename__ = "scraping_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))  # eurosatory_search, eurosatory_details, website, classify, ...
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    items_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
