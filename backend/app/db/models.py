from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

STATUSES = ("verified", "needs_confirmation")
# document     = may appear on resumes / cover letters (when verified)
# forms_only   = only when an application form explicitly asks (referees, full address, work rights)
# internal     = never leaves the system (eligibility rules, applied list, guardrails)
VISIBILITIES = ("document", "forms_only", "internal")


class ProfileFact(Base):
    """One candidate fact. Document generation may only read status='verified' AND visibility='document'."""

    __tablename__ = "profile_facts"
    __table_args__ = (
        UniqueConstraint("category", "key", name="uq_fact_category_key"),
        CheckConstraint("status in ('verified','needs_confirmation')", name="ck_fact_status"),
        CheckConstraint("visibility in ('document','forms_only','internal')", name="ck_fact_visibility"),
        CheckConstraint("status <> 'verified' or last_confirmed is not null", name="ck_verified_has_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(200))
    last_confirmed: Mapped[date | None] = mapped_column(Date, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), default="document")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


APPLICATION_STATUSES = ("drafted", "approved", "applied", "interview", "offer", "rejected", "withdrawn")


class Application(Base):
    """One row per vacancy. Never submitted by the system: a person moves it to 'applied' and records the evidence."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_application_fingerprint"),
        CheckConstraint("status in ('drafted','approved','applied','interview','offer','rejected','withdrawn')", name="ck_application_status"),
        # every submitted application must have its confirmation on record (withdrawn/rejected rows may never have been sent)
        CheckConstraint("status not in ('applied','interview','offer') or evidence is not null", name="ck_applied_has_evidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))  # sha256(employer_norm|title_norm|location_norm)
    employer: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employer_norm: Mapped[str] = mapped_column(String(200))
    title_norm: Mapped[str] = mapped_column(String(300), default="")
    location_norm: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(60), default="manual")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_key: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="drafted")
    drafted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # offer / rejected / withdrawn
    doc_paths: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_score: Mapped[float | None] = mapped_column(nullable=True)
    gate_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    history: Mapped[list] = mapped_column(JSONB, default=list)  # [{status, at, note}]
    # cover_letter text, email addresses / ATS links found in the ad, and what apply-assist did (email draft, form fill)
    draft_meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Listing(Base):
    """A job found by a discovery source, with its stored triage result. Never an application: see `Application`."""

    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_listing_source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300))
    employer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    description_complete: Mapped[bool] = mapped_column(default=False)  # False = a source snippet: gate can never PASS
    posted_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    employment_hint: Mapped[str | None] = mapped_column(String(30), nullable=True)
    salary_min: Mapped[float | None] = mapped_column(nullable=True)
    salary_max: Mapped[float | None] = mapped_column(nullable=True)
    salary_period: Mapped[str] = mapped_column(String(10), default="unknown")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    extract: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    score: Mapped[float | None] = mapped_column(nullable=True)
    gate_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fail_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    flags: Mapped[list] = mapped_column(JSONB, default=list)
    breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 'not interested': hidden, never deleted
    # --- source verification: the employer's own posting, when it could be found ---
    employer_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    employer_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)  # VERIFIED | THIRD_PARTY_FULL | SNIPPET_ONLY
    open_status: Mapped[str] = mapped_column(String(10), default="unknown", server_default="unknown")  # open | closed | unknown
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_route: Mapped[str | None] = mapped_column(String(24), nullable=True)  # employer_ats | employer_site | recruiter | email | seek_quick_apply | ...
    route_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    discrepancies: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    third_party_extract: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # what the third-party version said, kept next to the employer's
    verification_notes: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # --- shared job schema (job sources): what is not given stays empty, never invented ---
    source_method: Mapped[str | None] = mapped_column(String(24), nullable=True)  # api | feed | web_page | manual_import
    permission_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # permitted | user_supplied | ...
    state: Mapped[str | None] = mapped_column(String(10), nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(3), default="AUD", server_default="AUD")
    requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    closing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    application_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # where the application itself happens (may differ from the listing)
    ats_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)


class SourceConfig(Base):
    """One row per job source: how it connects, what it is permitted to do, and how it has behaved. Never holds a credential."""

    __tablename__ = "source_configs"
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))  # non-secret settings only
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_link_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # a search link was handed to the person
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceRef(Base):
    """One appearance of a job on a source. A canonical `Listing` can have several; none is ever deleted."""

    __tablename__ = "source_refs"
    __table_args__ = (UniqueConstraint("source", "source_job_id", name="uq_source_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(30))
    source_job_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(String(24), default="api")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchRun(Base):
    __tablename__ = "search_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    criteria: Mapped[dict] = mapped_column(JSONB, default=dict)
    results: Mapped[dict] = mapped_column(JSONB, default=dict)  # per source: status, count, error
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImportHistory(Base):
    __tablename__ = "import_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(30))
    method: Mapped[str] = mapped_column(String(24))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"), nullable=True)
    outcome: Mapped[str] = mapped_column(String(40))  # created | merged_into_existing | rejected
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceAudit(Base):
    __tablename__ = "source_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(30))
    event: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
