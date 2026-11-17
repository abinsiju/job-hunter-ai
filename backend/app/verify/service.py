"""Verify a stored listing against the employer's own posting, then score it again on the better text."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.base import AIProvider
from app.db.models import Listing
from app.discovery.triage import triage_listing
from app.matching.loader import load_candidate_profile
from app.matching.skills import SimilarityMatcher
from app.verify.fetch import PoliteFetcher
from app.verify.verify import Result, apply_result, verify_listing


async def verify_and_rescore(session: Session, ai: AIProvider, f: PoliteFetcher, l: Listing, today: date, *, employer_url: str | None = None,
                             cache: dict | None = None, profile=None, matcher=None) -> Result:
    r = await verify_listing(f, l, today, employer_url=employer_url, cache=cache)
    apply_result(l, r)
    session.commit()
    await triage_listing(session, ai, l, today, profile or load_candidate_profile(session), matcher or SimilarityMatcher(ai))
    return r


async def verify_all(session: Session, ai: AIProvider, f: PoliteFetcher, today: date, *, only_open: bool = True, limit: int | None = None, unverified_only: bool = False) -> list[tuple[Listing, Result]]:
    rows = [l for l in session.scalars(select(Listing).order_by(Listing.id)) if l.dismissed_at is None and (not only_open or l.gate_status != "FAIL") and not (unverified_only and l.verified_at)]
    rows = rows[:limit] if limit else rows
    profile, matcher, cache = load_candidate_profile(session), SimilarityMatcher(ai), {}
    return [(l, await verify_and_rescore(session, ai, f, l, today, cache=cache, profile=profile, matcher=matcher)) for l in rows]
