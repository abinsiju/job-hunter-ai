from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProfileFact
from app.documents.guardrails import ForbiddenClaimError, find_forbidden


def document_facts(session: Session) -> list[ProfileFact]:
    """The ONLY facts resumes / cover letters may use: verified AND visibility='document'."""
    return list(session.scalars(select(ProfileFact).where(
        ProfileFact.status == "verified", ProfileFact.visibility == "document").order_by(ProfileFact.category, ProfileFact.key)))


def form_facts(session: Session) -> list[ProfileFact]:
    """For application forms that explicitly ask (referees, full address, work rights). Still verified only."""
    return list(session.scalars(select(ProfileFact).where(
        ProfileFact.status == "verified", ProfileFact.visibility.in_(["document", "forms_only"]))))


def _guardrail(session: Session, key: str) -> list[str]:
    f = session.scalar(select(ProfileFact).where(ProfileFact.category == "guardrail", ProfileFact.key == key))
    return list(f.value) if f else []


def forbidden_terms(session: Session) -> list[str]:
    return _guardrail(session, "forbidden_terms")


def check_document_text(session: Session, text: str) -> list[str]:
    """Return forbidden claims found in generated text (empty list = OK). Code rules plus guardrail rows."""
    return find_forbidden(text, forbidden_terms(session), _guardrail(session, "forbidden_patterns"))


def assert_document_clean(session: Session, text: str) -> None:
    hits = check_document_text(session, text)
    if hits:
        raise ForbiddenClaimError(f"Generated text contains forbidden claims: {hits}")
