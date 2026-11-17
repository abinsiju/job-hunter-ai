"""Employers the user never wants to hear about, stored as a verified internal profile fact. The gate FAILs them."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProfileFact
from app.tracker.normalize import employers_match, norm_employer


def _row(session: Session, create: bool = False) -> ProfileFact | None:
    f = session.scalar(select(ProfileFact).where(ProfileFact.category == "preference", ProfileFact.key == "blocked_employers"))
    if f is None and create:
        f = ProfileFact(category="preference", key="blocked_employers", value={"employers": [], "reasons": {}}, status="verified",
                        source="user instruction", last_confirmed=date.today(), visibility="internal")
        session.add(f)
    return f


def list_blocked(session: Session) -> dict[str, str]:
    f = _row(session)
    return {e: (f.value.get("reasons") or {}).get(e, "") for e in (f.value.get("employers", []) if f else [])}


def add_blocked(session: Session, employer: str, reason: str = "") -> bool:
    employer = employer.strip()
    if not norm_employer(employer):
        raise ValueError("Employer name is empty")
    f = _row(session, create=True)
    employers = list(f.value.get("employers", []))
    if any(employers_match(norm_employer(employer), norm_employer(e)) for e in employers):
        return False
    f.value = {"employers": employers + [employer], "reasons": {**(f.value.get("reasons") or {}), employer: reason}}
    f.last_confirmed = date.today()
    session.commit()
    return True


def remove_blocked(session: Session, employer: str) -> bool:
    f = _row(session)
    if not f:
        return False
    keep = [e for e in f.value.get("employers", []) if not employers_match(norm_employer(employer), norm_employer(e))]
    if len(keep) == len(f.value.get("employers", [])):
        return False
    f.value = {"employers": keep, "reasons": {k: v for k, v in (f.value.get("reasons") or {}).items() if k in keep}}
    session.commit()
    return True
