"""Find the employer's own version of a listing, read it, and report: which text to trust, whether it is still open,
where the application goes, and where the two ads disagree. Nothing here applies to anything or logs in to anything."""
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.apply.platforms import URL_RE, NotAllowed, classify_url
from app.db.models import Listing
from app.discovery.base import html_to_text
from app.verify.compare import compare
from app.verify.feeds import search_employer, smartrecruiters_detail
from app.verify.fetch import Blocked, PoliteFetcher
from app.verify.posting import Posting, judge_open, postings_from_html, posting_from_workday, workday_json_url
from app.verify.resolve import Target, choose, detect_route, ids_from, is_job_board, match_one

VERIFIED, THIRD_PARTY_FULL, SNIPPET_ONLY = "VERIFIED", "THIRD_PARTY_FULL", "SNIPPET_ONLY"
FULL_AD_CHARS = 200
MAX_LINKS = 2


@dataclass
class Result:
    confidence: str = SNIPPET_ONLY
    open_status: str = "unknown"
    closed_reason: str | None = None
    employer_url: str | None = None
    employer_text: str | None = None
    application_route: str = "unknown"
    route_url: str | None = None
    discrepancies: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    posting: Posting | None = None

    @property
    def review_needed(self) -> bool:
        return any(d["severity"] == "review" for d in self.discrepancies)


def target_of(l: Listing) -> Target:
    return Target(title=l.title, employer=l.employer, location=l.location, text=l.description or "", url=l.url or "", posted=l.posted_at, ids=ids_from(l.url, l.source_id))


def is_employer_own(l: Listing) -> bool:
    """Listings read straight from the employer's own feed (Workable board, career-page watch-list on an allowed ATS)."""
    if l.source.startswith("workable:"):
        return True
    try:
        return classify_url(l.url or "") in ("workable", "greenhouse", "lever", "smartrecruiters")
    except NotAllowed:
        return False


async def read_url(f: PoliteFetcher, url: str, today: date) -> Posting:
    """One employer page -> a Posting (JSON-LD if the page has it, else its text). Raises Blocked when the site says no."""
    wd = workday_json_url(url)
    if wd:
        page = await f.get(wd)
        if page.status == 200:
            try:
                p = posting_from_workday(json.loads(page.text), url)
            except ValueError:
                p = None
            if p:
                return p
    page = await f.get(url)
    if page.status in (404, 410):
        return judge_open(Posting(title="", url=url, source="page"), "", page.status, today)
    found = postings_from_html(page.text, page.url)
    p = found[0] if found else Posting(title="", url=page.url, text=html_to_text(page.text), source="page")
    return judge_open(p, page.text, page.status, today)


async def verify_listing(f: PoliteFetcher, l: Listing, today: date, *, employer_url: str | None = None, cache: dict | None = None) -> Result:
    r, t = Result(), target_of(l)
    posting: Posting | None = None
    if employer_url:  # the user pasted the employer's link: use it, no guessing
        try:
            posting = await read_url(f, employer_url, today)
            r.notes.append("used the employer link you gave")
        except Blocked as e:
            r.notes.append(f"could not read the employer link: {e}")
    else:
        if l.employer:
            found, notes = await search_employer(f, l.employer, cache)
            r.notes += notes
            m, why = choose(t, found)
            r.notes.append(why)
            if m:
                posting = m.posting
        if not posting:  # links in the ad that lead to the employer's own site
            tried = 0
            for link in dict.fromkeys(x.group(0).rstrip(".,;:") for x in URL_RE.finditer(l.description or "")):
                if tried >= MAX_LINKS:
                    break
                if is_job_board(link) or link == l.url:
                    continue
                tried += 1
                try:
                    p = await read_url(f, link, today)
                except Blocked as e:
                    r.notes.append(f"{link}: {e}")
                    continue
                if p.open is False and not p.title:
                    continue
                if match_one(t, p):
                    posting = p
                    r.notes.append(f"found the employer's page through a link in the ad ({link})")
                    break
    if posting and posting.detail_url:
        try:
            posting = await smartrecruiters_detail(f, posting)
        except Blocked as e:
            r.notes.append(f"employer ad detail unavailable: {e}")
    own = is_employer_own(l)
    r.posting = posting
    if posting:
        r.employer_url = posting.url
        r.open_status = "open" if posting.open else "closed" if posting.open is False else "unknown"
        r.closed_reason = posting.closed_reason
    if posting and len(posting.text or "") >= FULL_AD_CHARS:
        r.confidence, r.employer_text = VERIFIED, posting.text
        r.discrepancies = [] if own else compare(l.description or "", posting, l.location, (l.salary_min, l.salary_max, l.salary_period))
    elif own and l.description_complete:
        r.confidence, r.employer_text, r.employer_url = VERIFIED, l.description, r.employer_url or l.url
        r.notes.append("this ad was read directly from the employer's own job board")
    else:
        if posting:
            r.notes.append("found the employer's page but it holds no full ad text")
        r.confidence = THIRD_PARTY_FULL if l.description_complete else SNIPPET_ONLY
    r.application_route, r.route_url = detect_route(r.employer_text or l.description or "", l.url, l.employer, posting)
    return r


def apply_result(l: Listing, r: Result) -> None:
    """Write a verification onto the listing (the caller commits and re-scores). The third-party version is kept beside the employer's."""
    if r.employer_text and l.confidence != VERIFIED and l.third_party_extract is None and l.extract:
        l.third_party_extract = {"gate_status": l.gate_status, "score": l.score, "fail_reasons": l.fail_reasons, "flags": l.flags, "extract": l.extract}
    l.employer_url, l.employer_description = r.employer_url, r.employer_text
    l.confidence, l.open_status, l.closed_reason = r.confidence, r.open_status, r.closed_reason
    l.application_route, l.route_url = r.application_route, r.route_url
    l.discrepancies, l.verification_notes = r.discrepancies, r.notes
    l.verified_at = datetime.now(timezone.utc)
