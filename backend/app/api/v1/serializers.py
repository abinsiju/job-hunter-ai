"""JSON shapes the dashboard consumes. One place, so the UI and the tests agree."""
from datetime import date, datetime

from app.db.models import Application, Listing, ProfileFact
from app.tracker.service import ALLOWED, _was_submitted

SOURCE_LABELS = {"adzuna": "Adzuna", "workable": "Employer feed", "manual": "Pasted ad", "user-reported": "Before tracker"}
STATUS_ORDER = ["drafted", "approved", "applied", "interview", "offer", "rejected", "withdrawn"]


def iso(v: date | datetime | None) -> str | None:
    return v.isoformat() if v else None


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source.split(":")[0], source)


def salary_text(l: Listing) -> str | None:
    if l.salary_min is None or l.salary_period == "unknown":
        return None
    hi = f" – {l.salary_max:,.0f}" if l.salary_max and l.salary_max != l.salary_min else ""
    unit = "/hr" if l.salary_period == "hour" else "/yr"
    return f"${l.salary_min:,.0f}{hi}{unit}"


def listing_row(l: Listing, tracked: Application | None = None) -> dict:
    ex = l.extract or {}
    return {
        "id": l.id, "title": l.title, "employer": l.employer, "location": l.location, "source": l.source, "source_label": source_label(l.source),
        "snippet_only": not (l.description_complete or l.employer_description), "confidence": l.confidence, "open_status": l.open_status,
        "application_route": l.application_route, "discrepancy_count": len(l.discrepancies or []), "score": l.score, "gate": l.gate_status, "fail_reasons": l.fail_reasons or [],
        "flags": l.flags or [], "posted_at": iso(l.posted_at), "url": l.url, "similar": getattr(l, "similar", 0),
        "employment": ex.get("employment_type") or l.employment_hint, "salary": salary_text(l), "triaged": l.triaged_at is not None,
        "tracked": application_brief(tracked) if tracked else None,
    }


def application_brief(a: Application) -> dict:
    return {"id": a.id, "status": a.status, "employer": a.employer, "title": a.title}


def allowed_next(a: Application) -> list[str]:
    nxt = set(ALLOWED[a.status])
    if a.status == "withdrawn" and _was_submitted(a):
        nxt.discard("drafted")
    return [s for s in STATUS_ORDER if s in nxt]


def application_row(a: Application, *, detail: bool = False) -> dict:
    row = {
        "id": a.id, "employer": a.employer, "title": a.title, "location": a.location, "status": a.status, "source": a.source,
        "source_label": source_label(a.source), "url": a.source_url, "score": a.match_score, "gate": a.gate_status,
        "drafted_at": iso(a.drafted_at), "approved_at": iso(a.approved_at), "applied_at": iso(a.applied_at),
        "interview_at": iso(a.interview_at), "outcome_at": iso(a.outcome_at), "updated_at": iso(a.updated_at),
        "documents": sorted((a.doc_paths or {}).keys()), "has_evidence": bool(a.evidence), "before_tracker": a.source == "user-reported",
        "allowed_next": allowed_next(a),
    }
    if detail:
        row |= {"evidence": a.evidence, "notes": a.notes, "history": a.history or []}
    return row


CATEGORY_LABELS = {
    "identity": "Identity", "contact": "Contact", "education": "Education", "employment": "Experience", "virtual_experience": "Virtual experience",
    "project": "Projects", "skill": "Skills", "license": "Licence", "certification": "Certifications", "referee": "Referees",
    "work_rights": "Work rights", "eligibility": "Eligibility rules", "preference": "Preferences", "guardrail": "Guardrails",
}
CATEGORY_ORDER = list(CATEGORY_LABELS)


def fact_row(f: ProfileFact) -> dict:
    return {
        "id": f.id, "category": f.category, "key": f.key, "value": f.value, "status": f.status, "source": f.source,
        "last_confirmed": iso(f.last_confirmed), "visibility": f.visibility, "notes": f.notes,
        "used_in_documents": f.status == "verified" and f.visibility == "document",
    }
