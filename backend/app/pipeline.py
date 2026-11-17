"""Paste a job ad -> extract -> score -> gate -> tailored resume + cover letter (DOCX + PDF). Nothing is submitted."""
import dataclasses
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.ai.base import AIProvider
from app.documents.build import assemble_cover_letter, build_resume
from app.apply.emails import find_application_emails
from app.apply.platforms import find_ats_links, NotAllowed
from app.apply.policy import check_url
from app.documents.factbook import FactBook
from app.documents.render_docx import render_cover_letter_docx, render_resume_docx
from app.documents.render_pdf import render_cover_letter_pdf, render_resume_pdf
from app.documents.validate import GenerationRejected, validate_generated
from app.matching.extract import extract_job
from app.matching.loader import load_candidate_profile
from app.matching.schema import JobExtract
from app.matching.scoring import MatchResult, score_job
from app.matching.skills import SimilarityMatcher
from app.tracker.service import DuplicateCheck, DuplicateVacancyError, check_duplicate, create_or_refresh_draft
from app.db.models import Application

log = logging.getLogger("jobhunter.pipeline")


@dataclass
class DraftResult:
    job: JobExtract
    match: MatchResult
    files: dict[str, Path] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    application: Application | None = None
    duplicates: DuplicateCheck | None = None
    resume: object | None = None  # the resume that was built (used for the ATS report)


def _slug(job: JobExtract, today: date) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", f"{job.employer or 'unknown'}-{job.title}".lower()).strip("-")[:60]
    return f"{today.isoformat()}_{base}"


async def draft(ad_text: str, session: Session, ai: AIProvider, today: date, out_root: Path, *, docs: bool = True,
                source: str = "manual", url: str | None = None, route: tuple[str, str | None] | None = None, job: JobExtract | None = None) -> DraftResult:
    job = job or await extract_job(ai, ad_text)  # `job` lets the quick path skip the slow language-model read
    profile = load_candidate_profile(session)
    matcher = SimilarityMatcher(ai)
    match = await score_job(job, profile, matcher, today)
    result = DraftResult(job, match)
    result.duplicates = dup = check_duplicate(session, job.employer or "", job.title, job.location, url)
    if not job.employer:
        result.warnings.append("No employer found in the ad: the duplicate check can only use the URL")
    if dup.blocking:  # HARD BLOCK: same vacancy already applied to
        a = dup.blocking
        result.skipped_reason = (f"Duplicate vacancy: already applied (#{a.id} {a.employer} / {a.title or 'role not recorded'}, "
                                 f"status {a.status}). No documents generated.")
        return result

    if match.gate.status == "FAIL":  # no override: the candidate cannot meet a hard requirement
        result.skipped_reason = "Gate FAIL: " + "; ".join(match.gate.fail_reasons)
        return result
    if not docs:
        return result

    book = FactBook.from_session(session)
    resume, warnings = await build_resume(book, job, matcher)
    result.resume = resume
    letter = await assemble_cover_letter(book, job, matcher, today)
    result.warnings += warnings
    # Safety net: the assembled text must still pass every fact check before any file is written.
    items = ([s.text for s in resume.sections if s.text] + [x for s in resume.sections for e in s.entries for x in [e.heading, e.sub, *e.bullets] if x]
             + letter.paragraphs + [letter.subject])
    facts_text = "\n".join(book.fact_lines())
    problems = [p for item in items for p in validate_generated(session, item, facts_text, ad_text, job)]
    if problems:
        raise GenerationRejected(list(dict.fromkeys(problems)))

    out = out_root / _slug(job, today)
    out.mkdir(parents=True, exist_ok=True)
    result.files = {
        "resume.docx": render_resume_docx(resume, out / "resume.docx"),
        "resume.pdf": render_resume_pdf(resume, out / "resume.pdf"),
        "cover_letter.docx": render_cover_letter_docx(letter, out / "cover_letter.docx"),
        "cover_letter.pdf": render_cover_letter_pdf(letter, out / "cover_letter.pdf"),
    }
    own = ((book.get("contact", "email") or {}).get("email") or "",)
    links = find_ats_links(ad_text)
    emails = find_application_emails(ad_text, exclude=own)
    for u in ([route[1]] if route and route[0] == "employer_ats" and route[1] else []) + ([url] if url else []):  # where the verified route says to apply comes first
        try:
            check_url(u)
            links = [u, *[x for x in links if x != u]]
        except NotAllowed:
            pass
    if route and route[0] == "email" and route[1] and not any(e["address"] == route[1] for e in emails):
        emails.insert(0, {"address": route[1], "near_apply_cue": True})
    meta = {"cover_letter": {"greeting": letter.greeting, "paragraphs": letter.paragraphs, "signoff": letter.signoff, "subject": letter.subject},
            "apply_emails": emails, "apply_links": links,
            "application_route": {"kind": route[0], "url": route[1]} if route else None}
    try:
        result.application, _ = create_or_refresh_draft(
            session, employer=job.employer or "", title=job.title, location=job.location, source=source, url=url,
            doc_paths={k: p for k, p in result.files.items()}, match_score=match.score, gate_status=match.gate.status, draft_meta=meta)
    except DuplicateVacancyError as e:  # lost a race with another writer: keep the guarantee, drop the files
        for f in result.files.values():
            f.unlink(missing_ok=True)
        result.files, result.skipped_reason = {}, str(e)
        return result
    (out / "match.json").write_text(json.dumps({
        "date": today.isoformat(), "job": job.model_dump(mode="json"), "score": match.score,
        "gate": dataclasses.asdict(match.gate), "breakdown": {k: dataclasses.asdict(v) for k, v in match.breakdown.items()},
        "warnings": result.warnings, "status": "DRAFT - not submitted", "tracker_id": result.application.id}, indent=2))
    log.info("draft written", extra={"dir": str(out), "score": match.score, "gate": match.gate.status})
    return result
