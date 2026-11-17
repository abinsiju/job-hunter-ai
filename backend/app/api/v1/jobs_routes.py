import dataclasses
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v1.deps import generated_root
from app.api.v1.serializers import application_brief, application_row
from app.core.config import settings
from app.db.models import Listing
from app.discovery.adzuna import AdzunaSource
from app.discovery.base import RawListing, SourceError
from app.discovery.triage import triage_pending, upsert_listings
from app.discovery.workable import WorkableSource
from app.pipeline import DraftResult, draft

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobIn(BaseModel):
    kind: Literal["draft", "discover_adzuna", "discover_careers", "rescore", "source_search"]
    params: dict = Field(default_factory=dict)


def serialize_draft(r: DraftResult) -> dict:
    j = r.job
    return {
        "job": {"title": j.title, "employer": j.employer, "location": j.location, "employment_type": j.employment_type,
                "required_skills": j.required_skills, "nice_to_have_skills": j.nice_to_have_skills, "min_years": j.min_years_experience,
                "qualifications": [q.text for q in j.qualifications], "deadline": j.deadline.isoformat() if j.deadline else None},
        "score": r.match.score, "gate": {"status": r.match.gate.status, "fail_reasons": r.match.gate.fail_reasons, "flags": r.match.gate.flags},
        "breakdown": {k: dataclasses.asdict(v) for k, v in r.match.breakdown.items()},
        "skipped_reason": r.skipped_reason, "warnings": r.warnings, "files": sorted(r.files),
        "application": application_row(r.application) if r.application else None,
        "same_employer": [application_brief(a) for a in (r.duplicates.same_employer if r.duplicates else [])],
    }


@router.post("", status_code=202)
async def submit(body: JobIn, request: Request) -> dict:
    factory, ai, mgr = request.app.state.session_factory, request.app.state.ai_factory(), request.app.state.jobs
    p = body.params

    if body.kind == "draft":
        text, url, source = str(p.get("text", "")).strip(), p.get("url") or None, str(p.get("source") or "manual")
        route = None
        if p.get("listing_id") is not None:  # draft straight from a stored listing that has the full ad
            with factory() as s:
                l = s.get(Listing, int(p["listing_id"]))
                if not l:
                    raise HTTPException(404, "Listing not found")
                if l.open_status == "closed":
                    raise HTTPException(422, f"This job is closed, so no documents are drafted. {l.closed_reason or ''}".strip())
                if not (l.description_complete or l.employer_description):
                    raise HTTPException(422, "This listing only has a snippet. Open the original ad, copy the full text, and paste it in Draft studio.")
                text, url, source = RawListing(l.source, l.source_id, l.url, l.title, l.employer, l.location, l.employer_description or l.description, True,
                                               l.posted_at, l.employment_hint).ad_text(), (l.route_url if l.application_route == "employer_ats" and l.route_url else l.url), l.source
                route = (l.application_route, l.route_url) if l.application_route and l.application_route != "unknown" else None
        if len(text) < 40:
            raise HTTPException(422, "Paste the full job ad (at least a few sentences).")

        async def run(progress):
            progress("Reading the ad and scoring it")
            with factory() as s:
                r = await draft(text, s, ai, date.today(), generated_root(), docs=not p.get("score_only", False), source=source, url=url, route=route)
                return serialize_draft(r)

    elif body.kind in ("discover_adzuna", "discover_careers"):
        limit = max(1, min(int(p.get("limit", 15)), 40))
        if body.kind == "discover_adzuna":
            if not (settings.adzuna_app_id and settings.adzuna_app_key):
                raise HTTPException(422, "Adzuna keys are not set. Add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env.")
            what = str(p.get("what", "")).strip()
            if not what:
                raise HTTPException(422, "Enter what to search for, for example 'it support'.")

        async def run(progress):
            progress("Fetching listings")
            try:
                if body.kind == "discover_adzuna":
                    raws = await AdzunaSource(settings.adzuna_app_id, settings.adzuna_app_key).search(
                        what=what, where=str(p.get("where") or "Melbourne"), pages=max(1, min(int(p.get("pages", 1)), 3)),
                        max_days_old=int(p.get("max_days_old") or 14), full_time=bool(p.get("full_time")), part_time=bool(p.get("part_time")))
                else:
                    raws = await WorkableSource("first-focus", "First Focus").search(
                        country="Australia", city=p.get("city") or None,
                        title_contains=p.get("title_contains") or ["support", "service desk", "helpdesk", "help desk", "technician", "analyst", "administrator"])
            except SourceError as e:
                raise RuntimeError(str(e)) from None
            with factory() as s:
                new, known = upsert_listings(s, raws)
                progress(f"Scoring up to {limit} new listings (about 6 seconds each)")
                done = await triage_pending(s, ai, date.today(), limit=limit)
                left = s.query(Listing).filter(Listing.triaged_at.is_(None)).count()
            return {"fetched": len(raws), "new": new, "known": known, "scored": len(done), "waiting_to_score": left}

    elif body.kind == "source_search":
        from app.api.v1.sources import CriteriaIn
        from app.sources.connectors import build_registry
        from app.sources.service import unified_search
        crit = CriteriaIn(**p).to_criteria()
        reg = getattr(request.app.state, "source_registry", None) or build_registry()

        async def run(progress):
            progress("Searching your sources")
            with factory() as s:
                res = await unified_search(s, reg, crit)
                progress("Scoring new jobs (about 6 seconds each)")
                done = await triage_pending(s, ai, date.today(), limit=12)
                res["scored"] = len(done)
                res["waiting_to_score"] = s.query(Listing).filter(Listing.triaged_at.is_(None)).count()
            return res

    else:  # rescore
        async def run(progress):
            progress("Re-scoring stored listings")
            with factory() as s:
                n = s.query(Listing).update({Listing.triaged_at: None})
                s.commit()
                done = await triage_pending(s, ai, date.today(), limit=max(1, min(int(p.get("limit", 30)), 60)))
                left = s.query(Listing).filter(Listing.triaged_at.is_(None)).count()
            return {"reset": n, "scored": len(done), "waiting_to_score": left}

    return mgr.submit(body.kind, run).view()


@router.get("")
def recent(request: Request) -> list[dict]:
    return request.app.state.jobs.recent()


@router.get("/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    j = request.app.state.jobs.get(job_id)
    if not j:
        raise HTTPException(404, "Job not found (the server may have restarted)")
    return j.view()
