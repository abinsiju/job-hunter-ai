import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Application
from app.tracker.normalize import (employers_match, fingerprint, locations_compatible, norm_employer, norm_location,
                                   norm_title, norm_url, shorten_url)

log = logging.getLogger("jobhunter.tracker")

ALLOWED = {
    "drafted": {"approved", "withdrawn"},
    "approved": {"applied", "withdrawn", "drafted"},  # back to drafted when the documents are regenerated
    "applied": {"interview", "rejected", "withdrawn"},
    "interview": {"offer", "rejected", "withdrawn"},
    "offer": {"withdrawn"},  # declined
    "rejected": set(),
    "withdrawn": {"drafted"},  # only if it was never actually applied (checked below)
}
SUBMITTED = {"applied", "interview", "offer", "rejected"}  # the vacancy has been (or was) applied to
STAMP = {"drafted": "drafted_at", "approved": "approved_at", "applied": "applied_at", "interview": "interview_at",
         "offer": "outcome_at", "rejected": "outcome_at", "withdrawn": "outcome_at"}


class TrackerError(Exception):
    pass


class DuplicateVacancyError(TrackerError):
    def __init__(self, existing: Application):
        self.existing = existing
        super().__init__(f"Already applied to this vacancy: #{existing.id} {existing.employer} / {existing.title or 'role not recorded'} "
                         f"(status {existing.status}). Duplicate applications are blocked.")


class InvalidTransition(TrackerError):
    pass


@dataclass
class DuplicateCheck:
    blocking: Application | None = None  # same vacancy, already applied: HARD BLOCK
    reusable: Application | None = None  # same vacancy, still only a draft/approval: reuse this row
    same_employer: list[Application] = field(default_factory=list)  # different role at an employer already tracked: WARN


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _was_submitted(a: Application) -> bool:
    return a.status in SUBMITTED or (a.status == "withdrawn" and (a.applied_at is not None or a.evidence is not None))


def same_vacancy(a: Application, employer_n: str, title_n: str, location_n: str, url_k: str | None) -> bool:
    if url_k and a.url_key and url_k == a.url_key:
        return True
    # titles must both be known: an employer-only record (role unknown) can only ever produce a warning
    return (employers_match(a.employer_norm, employer_n) and bool(title_n) and a.title_norm == title_n
            and locations_compatible(a.location_norm, location_n))


def check_duplicate(session: Session, employer: str, title: str | None, location: str | None, url: str | None,
                    exclude_id: int | None = None) -> DuplicateCheck:
    e, t, l, u = norm_employer(employer), norm_title(title), norm_location(location), norm_url(url)
    res = DuplicateCheck()
    for a in session.scalars(select(Application).order_by(Application.id)):
        if a.id == exclude_id:
            continue
        if same_vacancy(a, e, t, l, u):
            if _was_submitted(a):
                res.blocking = res.blocking or a
            else:
                res.reusable = res.reusable or a
        elif employers_match(a.employer_norm, e) and a.status != "drafted":
            res.same_employer.append(a)  # different role at an employer already applied to
    return res


def _event(a: Application, status: str, note: str | None) -> None:
    at = _now()
    setattr(a, STAMP[status], at)
    a.history = [*(a.history or []), {"status": status, "at": at.isoformat(), "note": note}]


def create_or_refresh_draft(session: Session, *, employer: str, title: str | None, location: str | None, source: str = "manual",
                            url: str | None = None, doc_paths: dict | None = None, match_score: float | None = None,
                            gate_status: str | None = None, notes: str | None = None,
                            draft_meta: dict | None = None) -> tuple[Application, DuplicateCheck]:
    """Create the 'drafted' row for a vacancy, or refresh the existing draft. Raises DuplicateVacancyError if already applied."""
    url = shorten_url(url)  # a long tracking link must never break saving a draft
    chk = check_duplicate(session, employer, title, location, url)
    if chk.blocking:
        raise DuplicateVacancyError(chk.blocking)
    docs = {k: str(v) for k, v in (doc_paths or {}).items()}
    if chk.reusable:
        a = chk.reusable
        a.doc_paths, a.match_score, a.gate_status = docs or a.doc_paths, match_score, gate_status
        if draft_meta:  # keep what apply-assist recorded earlier (email draft, form fill); refresh the rest
            a.draft_meta = {**(a.draft_meta or {}), **draft_meta}
        if a.status in ("approved", "withdrawn"):  # new documents invalidate an earlier approval
            a.status = "drafted"
            _event(a, "drafted", "documents regenerated")
        session.commit()
        return a, chk
    fp = fingerprint(employer, title, location) if norm_employer(employer) else hashlib.sha256((norm_url(url) or uuid.uuid4().hex).encode()).hexdigest()
    a = Application(fingerprint=fp, employer=employer or "(unknown employer)", title=title, location=location,
                    employer_norm=norm_employer(employer), title_norm=norm_title(title), location_norm=norm_location(location),
                    source=source, source_url=url, url_key=norm_url(url), status="drafted", doc_paths=docs, match_score=match_score,
                    gate_status=gate_status, notes=notes, history=[], draft_meta=draft_meta or {})
    _event(a, "drafted", "documents generated")
    session.add(a)
    session.commit()
    log.info("application drafted", extra={"id": a.id, "employer": employer})
    return a, chk


def update_status(session: Session, app_id: int, status: str, *, evidence: str | None = None, note: str | None = None) -> Application:
    a = session.get(Application, app_id)
    if a is None:
        raise TrackerError(f"No application #{app_id}")
    if status not in ALLOWED:
        raise InvalidTransition(f"Unknown status '{status}'. Use one of: {', '.join(ALLOWED)}")
    if status == a.status:
        raise InvalidTransition(f"#{a.id} is already {status}")
    if status not in ALLOWED[a.status]:
        raise InvalidTransition(f"Cannot move #{a.id} from {a.status} to {status} (allowed: {', '.join(sorted(ALLOWED[a.status])) or 'none'})")
    if a.status == "withdrawn" and _was_submitted(a):
        raise InvalidTransition(f"#{a.id} was submitted before it was withdrawn and cannot be reopened")
    if status in ("approved", "applied"):  # re-check against every other row (e.g. an older record with a different wording)
        chk = check_duplicate(session, a.employer, a.title, a.location, a.source_url, exclude_id=a.id)
        if chk.blocking:
            raise DuplicateVacancyError(chk.blocking)
    if status == "applied":
        if not evidence or len(evidence.strip()) < 5:
            raise TrackerError("Moving to 'applied' needs --evidence: the confirmation (email subject, reference number or file path).")
        a.evidence = evidence.strip()
    a.status = status
    if note:
        a.notes = (a.notes + "\n" if a.notes else "") + note
    _event(a, status, note)
    session.commit()
    return a


def applied_employers(session: Session) -> list[str]:
    """Employers already applied to (feeds the gate's possible-duplicate flag)."""
    return list(dict.fromkeys(a.employer for a in session.scalars(select(Application)) if _was_submitted(a)))
