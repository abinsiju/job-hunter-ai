from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.serializers import listing_row
from app.blocklist import add_blocked, list_blocked
from app.db.models import Listing
from app.discovery.triage import ranked
from app.tracker.service import check_duplicate
from app.verify.fetch import PoliteFetcher
from app.verify.service import verify_and_rescore

router = APIRouter(prefix="/listings", tags=["listings"])


def _tracked(db: Session, l: Listing):
    if not l.employer:
        return None
    chk = check_duplicate(db, l.employer, l.title, l.location, l.url)
    return chk.blocking or chk.reusable


def _plain_explanation(l: Listing) -> list[str]:
    """Plain-English reasons behind the score, from the stored breakdown."""
    lines = []
    for r in l.fail_reasons or []:
        lines.append(f"Excluded: {r}.")
    b = l.breakdown or {}
    labels = {"technical_skills": "Technical skills", "experience": "Experience", "qualification": "Qualifications", "role_relevance": "Role fit",
              "location_preferences": "Location & hours", "other_eligibility": "Other eligibility"}
    gaps = sorted(((c["max_points"] - c["points"], k, c) for k, c in b.items()), reverse=True)
    for lost, k, c in gaps[:3]:
        if lost >= 1:
            why = f" Missing: {', '.join(c['missing'][:4])}." if c.get("missing") else (f" {c['notes'][0]}." if c.get("notes") else "")
            lines.append(f"{labels.get(k, k)}: {c['points']:g} of {c['max_points']}.{why}")
    return lines


@router.get("")
def list_listings(gate: str = Query("open", pattern="^(open|pass|review|fail|all|dismissed|closed)$"), q: str = "", source: str = "",
                  min_score: float = 0, sort: str = Query("rank", pattern="^(rank|newest)$"), limit: int = Query(50, le=200), offset: int = 0,
                  db: Session = Depends(get_db)) -> dict:
    everything = ranked(db, include_fail=True, collapse=gate not in ("fail", "dismissed", "closed"), include_dismissed=True, include_closed=True)
    closed = [l for l in everything if l.open_status == "closed" and l.dismissed_at is None]
    everything = [l for l in everything if l.open_status != "closed" or gate == "closed"]
    live = [l for l in everything if l.dismissed_at is None and l.open_status != "closed"]
    counts = {"pass": sum(l.gate_status == "PASS" for l in live), "review": sum(l.gate_status == "REVIEW" for l in live),
              "fail": sum(l.gate_status == "FAIL" for l in live), "dismissed": sum(l.dismissed_at is not None for l in everything), "closed": len(closed),
              "untriaged": db.query(Listing).filter(Listing.triaged_at.is_(None), Listing.dismissed_at.is_(None)).count()}
    if gate == "dismissed":
        rows = [l for l in everything if l.dismissed_at is not None]
    elif gate == "closed":
        rows = closed
    else:
        rows = {"open": [l for l in live if l.gate_status in ("PASS", "REVIEW")], "pass": [l for l in live if l.gate_status == "PASS"],
                "review": [l for l in live if l.gate_status == "REVIEW"], "fail": [l for l in live if l.gate_status == "FAIL"], "all": live}[gate]
    ql = q.strip().lower()
    if ql:
        rows = [l for l in rows if ql in f"{l.title} {l.employer or ''} {l.location or ''}".lower()]
    if source:
        rows = [l for l in rows if l.source.startswith(source)]
    rows = [l for l in rows if (l.score or 0) >= min_score]
    if sort == "newest":
        rows.sort(key=lambda l: (l.posted_at or l.first_seen.date()), reverse=True)
    return {"total": len(rows), "counts": counts, "items": [listing_row(l, _tracked(db, l)) for l in rows[offset:offset + limit]]}


@router.get("/{listing_id}")
def get_listing(listing_id: int, db: Session = Depends(get_db)) -> dict:
    l = db.get(Listing, listing_id)
    if not l:
        raise HTTPException(404, "Listing not found")
    ex = l.extract or {}
    chk = check_duplicate(db, l.employer or "", l.title, l.location, l.url) if l.employer else None
    blocked = {e.lower() for e in list_blocked(db)}
    row = listing_row(l, chk.blocking or chk.reusable if chk else None)
    return row | {
        "description": l.description, "breakdown": l.breakdown or {}, "explanation": _plain_explanation(l), "dismissed": l.dismissed_at is not None,
        "extract": {k: ex.get(k) for k in ("required_skills", "nice_to_have_skills", "qualifications", "min_years_experience", "employment_type",
                                          "hours_per_week_min", "hours_per_week_max", "deadline", "requires_drivers_licence", "remote")},
        "same_employer": [{"id": a.id, "status": a.status, "title": a.title} for a in (chk.same_employer if chk else [])],
        "employer_blocked": bool(l.employer and l.employer.lower() in blocked),
        "can_draft": bool(l.description_complete or l.employer_description) and l.gate_status != "FAIL" and l.open_status != "closed",
        "draft_hint": ("This job is closed, so nothing will be drafted." if l.open_status == "closed" else None if (l.description_complete or l.employer_description)
                       else "Only a snippet of the ad is available, so the work-rights line cannot be checked. Paste the employer's link below, or open the ad and paste it in Draft studio."),
        "verification": {"confidence": l.confidence, "open_status": l.open_status, "closed_reason": l.closed_reason, "employer_url": l.employer_url,
                         "application_route": l.application_route, "route_url": l.route_url, "discrepancies": l.discrepancies or [],
                         "notes": l.verification_notes or [], "verified_at": l.verified_at.isoformat() if l.verified_at else None,
                         "third_party": l.third_party_extract and {k: l.third_party_extract.get(k) for k in ("gate_status", "score", "fail_reasons", "flags")}},
    }


class VerifyIn(BaseModel):
    url: str | None = Field(default=None, max_length=2000)  # the employer's own link, pasted by the user


@router.post("/{listing_id}/verify")
async def verify(listing_id: int, body: VerifyIn, request: Request, db: Session = Depends(get_db)) -> dict:
    l = db.get(Listing, listing_id)
    if not l:
        raise HTTPException(404, "Listing not found")
    url = (body.url or "").strip() or None
    if url and not url.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "That is not a web address (it must start with https://).")
    factory = getattr(request.app.state, "fetcher_factory", PoliteFetcher)
    f = factory()
    try:
        r = await verify_and_rescore(db, request.app.state.ai_factory(), f, l, date.today(), employer_url=url)
    finally:
        if hasattr(f, "aclose"):
            await f.aclose()
    return {"id": l.id, "confidence": r.confidence, "open_status": r.open_status, "discrepancies": r.discrepancies, "notes": r.notes,
            "gate": l.gate_status, "employer_url": r.employer_url}


@router.post("/{listing_id}/dismiss")
def dismiss(listing_id: int, db: Session = Depends(get_db)) -> dict:
    l = db.get(Listing, listing_id)
    if not l:
        raise HTTPException(404, "Listing not found")
    l.dismissed_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": l.id, "dismissed": True}


@router.post("/{listing_id}/restore")
def restore(listing_id: int, db: Session = Depends(get_db)) -> dict:
    l = db.get(Listing, listing_id)
    if not l:
        raise HTTPException(404, "Listing not found")
    l.dismissed_at = None
    db.commit()
    return {"id": l.id, "dismissed": False}


@router.post("/{listing_id}/block-employer")
def block_employer(listing_id: int, db: Session = Depends(get_db)) -> dict:
    l = db.get(Listing, listing_id)
    if not l or not l.employer:
        raise HTTPException(404, "Listing or employer not found")
    added = add_blocked(db, l.employer, f"blocked from listing #{l.id}")
    return {"employer": l.employer, "added": added, "note": "Re-score to apply the block to existing listings."}
