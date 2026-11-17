from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.db.models import ImportHistory, Listing, SourceAudit, SourceConfig, SourceRef
from app.sources.base import SearchCriteria
from app.sources.connectors import build_registry
from app.sources.links import BUILDERS
from app.sources.service import ImportRejected, _config, manual_import, source_board

router = APIRouter(prefix="/sources", tags=["sources"])


def registry(request: Request) -> dict:
    return getattr(request.app.state, "source_registry", None) or build_registry()


class CriteriaIn(BaseModel):
    keywords: str = Field("", max_length=200)
    title: str = Field("", max_length=120)
    location: str = Field("", max_length=80)
    city: str = Field("", max_length=60)
    suburb: str = Field("", max_length=60)
    state: str = Field("", max_length=3)
    distance_km: int | None = Field(None, ge=0, le=500)
    salary_min: float | None = Field(None, ge=0)
    salary_max: float | None = Field(None, ge=0)
    salary_period: str = Field("year", pattern="^(year|hour)$")
    employment_types: list[str] = Field(default_factory=list, max_length=4)
    level: str = Field("", max_length=12)
    experience_years: int | None = Field(None, ge=0, le=40)
    industry: str = Field("", max_length=60)
    date_posted_days: int | None = Field(None, ge=1, le=60)
    work_rights: str = Field("", max_length=40)
    employer: str = Field("", max_length=80)
    sources: list[str] = Field(default_factory=list, max_length=8)

    def to_criteria(self) -> SearchCriteria:
        return SearchCriteria(**self.model_dump())


@router.get("")
async def board(request: Request, db: Session = Depends(get_db)) -> dict:
    items = await source_board(db, registry(request))
    return {"sources": items, "legend": {"real_data": "Returns listings by itself (API, authorised feed or public employer pages).",
                                          "link_only": "Gives you a search link and manual import. Nothing is fetched from the board.", "never": "No scraping, no bots, no automated apply on SEEK, Indeed or Jora."}}


@router.post("/search-links")
def search_links(body: CriteriaIn, request: Request, db: Session = Depends(get_db)) -> dict:
    c = body.to_criteria()
    reg = registry(request)
    out = []
    for name in (c.sources or list(BUILDERS)):
        if name in BUILDERS:
            out.append({**BUILDERS[name](c), "label": reg[name].label, "kind": "search link (opens in your own browser, nothing is fetched)"})
            _config(db, name).last_link_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    db.commit()
    return {"links": out}


class ImportIn(BaseModel):
    text: str = Field(..., max_length=40000)
    url: str | None = Field(None, max_length=2000)
    title: str = Field("", max_length=300)
    employer: str = Field("", max_length=200)
    location: str = Field("", max_length=200)
    application_url: str | None = Field(None, max_length=2000)
    closing_date: date | None = None


@router.post("/{source}/import")
def import_listing(source: str, body: ImportIn, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        listing, outcome = manual_import(db, registry(request), source, text=body.text, url=body.url, title=body.title, employer=body.employer, location=body.location,
                                         application_url=body.application_url, closing_date=body.closing_date)
    except ImportRejected as e:
        raise HTTPException(422, str(e)) from None
    refs = db.scalars(select(SourceRef).where(SourceRef.listing_id == listing.id)).all()
    return {"id": listing.id, "outcome": outcome, "title": listing.title, "employer": listing.employer, "needs_scoring": listing.triaged_at is None,
            "sources": sorted({r.source for r in refs}), "open_status": listing.open_status,
            "message": {"created": "Imported. It will be scored with your next search or hunt, or press Re-score on the Jobs page.",
                        "merged": "This job was already known from another source. Added as another reference to the same job.", "known": "You had already imported this one."}[outcome]}


@router.get("/imports")
def imports(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ImportHistory).order_by(ImportHistory.id.desc()).limit(50))
    return [{"id": r.id, "source": r.source, "method": r.method, "outcome": r.outcome, "listing_id": r.listing_id, "at": r.at.isoformat(), "url": r.url} for r in rows]


@router.get("/audit")
def audit_log(db: Session = Depends(get_db)) -> list[dict]:
    return [{"source": r.source, "event": r.event, "detail": r.detail, "at": r.at.isoformat()} for r in db.scalars(select(SourceAudit).order_by(SourceAudit.id.desc()).limit(100))]


class EnabledIn(BaseModel):
    enabled: bool


@router.put("/{source}/enabled")
def set_enabled(source: str, body: EnabledIn, request: Request, db: Session = Depends(get_db)) -> dict:
    if source not in registry(request):
        raise HTTPException(404, "Unknown source")
    _config(db, source).enabled = body.enabled
    db.commit()
    return {"source": source, "enabled": body.enabled}


@router.get("/listing/{listing_id}")
def listing_sources(listing_id: int, db: Session = Depends(get_db)) -> dict:
    l = db.get(Listing, listing_id)
    if not l:
        raise HTTPException(404, "Listing not found")
    refs = db.scalars(select(SourceRef).where(SourceRef.listing_id == listing_id).order_by(SourceRef.id))
    return {"listing_id": listing_id, "references": [{"source": r.source, "source_job_id": r.source_job_id, "url": r.url, "method": r.method,
                                                      "first_seen_at": r.first_seen_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat()} for r in refs],
            "discovery": {"source_method": l.source_method, "permission_status": l.permission_status}, "original_url": l.url, "application_url": l.application_url,
            "ats_provider": l.ats_provider, "application_route": l.application_route}
