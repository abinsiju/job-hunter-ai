"""Store discovered listings and score them with the same extract -> score -> gate pipeline as `draft`."""
import dataclasses
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.base import AIProvider
from app.db.models import Listing, SourceRef
from app.discovery.base import RawListing
from app.matching.extract import extract_job
from app.matching.loader import load_candidate_profile
from app.matching.schema import Salary
from app.matching.scoring import TIER, score_job
from app.matching.skills import SimilarityMatcher
from app.tracker.normalize import fingerprint
from app.tracker.service import check_duplicate

log = logging.getLogger("jobhunter.triage")


PER_YEAR = {"year": 1, "month": 12, "fortnight": 26, "week": 52, "day": 260}


def annual_salary(lo: float | None, hi: float | None, period: str) -> Salary | None:
    """The scorer understands hourly and yearly pay. Other units (month, week, day) are converted to a yearly figure; anything else is unknown, never an error."""
    if period == "hour":
        return Salary(min=lo, max=hi, period="hour")
    k = PER_YEAR.get(period)
    return Salary(min=lo * k if lo is not None else None, max=hi * k if hi is not None else None, period="year") if k else None


def _raw(l: Listing) -> RawListing:
    """The employer's own full ad wins over the third-party text whenever source verification found it."""
    own = bool(l.employer_description)
    return RawListing(l.source, l.source_id, l.url, l.title, l.employer, l.location, l.employer_description if own else l.description,
                      True if own else l.description_complete,
                      l.posted_at, l.employment_hint, l.salary_min, l.salary_max, l.salary_period)


def upsert_listings(session: Session, raws: list[RawListing]) -> tuple[int, int]:
    """Insert new listings, refresh last_seen on known ones (their triage result is kept). Returns (new, already_known)."""
    new = known = 0
    now = datetime.now(timezone.utc)
    for r in raws:
        row = session.scalar(select(Listing).where(Listing.source == r.source, Listing.source_id == r.source_id))
        if row:
            row.last_seen = now
            known += 1
            continue
        l = Listing(source=r.source, source_id=r.source_id, url=r.url, title=r.title, employer=r.employer, location=r.location,
                    description=r.description, description_complete=r.description_complete, posted_at=r.posted_at,
                    employment_hint=r.employment_hint, salary_min=r.salary_min, salary_max=r.salary_max, salary_period=r.salary_period,
                    source_method="feed" if r.source.startswith("workable") else "web_page" if r.source == "web" else "api", permission_status="permitted")
        session.add(l)
        session.flush()
        session.add(SourceRef(listing_id=l.id, source=r.source.split(":")[0], source_job_id=r.source_id, url=r.url, method=l.source_method))
        new += 1
    session.commit()
    return new, known


def _verification_effects(l: Listing, status: str, fails: list, flags: list) -> tuple[str, list, list]:
    """A work-rights or mandatory-requirement mismatch between the two ads can never stay PASS: the user must read both quotes."""
    bad = [d for d in (l.discrepancies or []) if d.get("severity") == "review"]
    if bad:
        flags = flags + [f"The employer's ad and the listing disagree on {d['label'].lower()}: listing says {d['third_party']!r}, employer says {d['employer']!r}" for d in bad]
        if status == "PASS":
            status = "REVIEW"
    if l.confidence == "SNIPPET_ONLY" and status == "PASS":
        status = "REVIEW"
    return status, fails, flags


async def triage_listing(session: Session, ai: AIProvider, l: Listing, today: date, profile=None, matcher=None) -> Listing:
    try:
        job = await extract_job(ai, _raw(l).ad_text())
    except ValueError as e:  # extraction failed: leave untriaged so a later run retries
        log.warning("triage extraction failed", extra={"listing": l.id, "error": str(e)[:120]})
        return l
    # the source's structured fields beat the model's guesses
    job.title = l.title
    job.employer = l.employer or job.employer
    job.location = l.location or job.location
    if job.employment_type == "unknown" and l.employment_hint:
        job.employment_type = l.employment_hint if l.employment_hint in ("full_time", "part_time", "casual", "contract") else "unknown"
    if l.salary_min is not None and job.salary.min is None and l.salary_period != "unknown":
        sal = annual_salary(l.salary_min, l.salary_max, l.salary_period)
        if sal:
            job.salary = sal
    profile = profile or load_candidate_profile(session)
    matcher = matcher or SimilarityMatcher(ai)
    match = await score_job(job, profile, matcher, today, text_complete=l.description_complete)
    fails, flags, status = list(match.gate.fail_reasons), list(match.gate.flags), match.gate.status
    status, fails, flags = _verification_effects(l, status, fails, flags)
    dup = check_duplicate(session, job.employer or "", job.title, job.location, l.url)
    if dup.blocking:  # same vacancy already applied to: never surfaced as a candidate
        fails.append(f"already applied to this vacancy (#{dup.blocking.id}, status {dup.blocking.status})")
        status = "FAIL"
    l.extract, l.score, l.gate_status, l.fail_reasons, l.flags = job.model_dump(mode="json"), match.score, status, fails, flags
    l.breakdown = {k: dataclasses.asdict(v) for k, v in match.breakdown.items()}
    l.triaged_at = datetime.now(timezone.utc)
    session.commit()
    return l


async def triage_pending(session: Session, ai: AIProvider, today: date, limit: int = 15) -> list[Listing]:
    """Triage up to `limit` not-yet-scored listings (each costs one LLM extraction)."""
    pending = list(session.scalars(select(Listing).where(Listing.triaged_at.is_(None)).order_by(Listing.id).limit(limit)))
    if not pending:
        return []
    profile, matcher = load_candidate_profile(session), SimilarityMatcher(ai)
    return [await triage_listing(session, ai, l, today, profile, matcher) for l in pending]


def ranked(session: Session, *, include_fail: bool = False, limit: int | None = None, collapse: bool = True,
           include_dismissed: bool = False, include_closed: bool = False) -> list[Listing]:
    """PASS before REVIEW, then by score. Gate failures are hidden unless include_fail. Repeated postings of the same
    vacancy (same normalised employer + title + location) collapse into the best-ranked one, which gets `similar` = N."""
    rows = [l for l in session.scalars(select(Listing).where(Listing.triaged_at.is_not(None)))
            if (include_fail or l.gate_status != "FAIL") and (include_dismissed or l.dismissed_at is None)
            and (include_closed or l.open_status != "closed")]
    rows.sort(key=lambda l: (TIER.get(l.gate_status, 2), -(l.score or 0), -(l.posted_at.toordinal() if l.posted_at else 0)))
    if collapse:
        kept, first = [], {}
        for l in rows:
            l.similar = 0
            key = fingerprint(l.employer, l.title, l.location) if l.employer else None
            if key and key in first:
                first[key].similar += 1
                continue
            if key:
                first[key] = l
            kept.append(l)
        rows = kept
    return rows[:limit] if limit else rows
