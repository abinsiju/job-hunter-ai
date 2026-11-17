from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProfileFact
from app.matching.profile import CandidateProfile
from app.tracker.service import applied_employers


def _months(a: str, b: str) -> int:
    (y1, m1), (y2, m2) = (int(x) for x in a.split("-")), (int(x) for x in b.split("-"))
    return (y2 - y1) * 12 + (m2 - m1) + 1


def load_candidate_profile(session: Session) -> CandidateProfile:
    """Build the scoring profile from facts. Matching may use stated (unconfirmed) skills; documents may not."""
    facts = list(session.scalars(select(ProfileFact)))
    p = CandidateProfile()
    for f in facts:
        v = f.value if isinstance(f.value, dict) else {}
        if f.category == "skill":
            p.skills[f.key] = f.status
        elif f.category == "education" and f.status == "verified":
            p.degree_status = "completed" if v.get("status") == "completed" else "in_progress"
        elif f.category == "license" and f.status == "verified":
            p.has_licence_and_car = bool(v.get("held") and v.get("own_car"))
        elif f.category == "employment" and f.status == "verified" and v.get("relevant_to_it") and v.get("start") and v.get("end"):
            factor = float(v.get("it_relevance_factor", 1.0))  # share of the role that counts as IT experience
            p.relevant_years = round(p.relevant_years + factor * _months(v["start"][:7], v["end"][:7]) / 12, 2)
        elif f.category == "preference" and f.key == "blocked_employers":
            p.blocked_employers = list(v.get("employers") or [])
        elif f.category == "certification" and f.status == "verified":
            p.certifications = list(v.get("items") or [])
    p.applied_employers = applied_employers(session)  # the tracker is the single source of truth
    return p
