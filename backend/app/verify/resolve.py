"""Is this employer posting the same vacancy as the third-party listing? Title + employer + location, plus one piece of real evidence
(the snippet's words appear in the full ad, the same job ID, or a posting date within 3 days). Ambiguity is never guessed through."""
import difflib
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from app.apply.emails import find_application_emails
from app.apply.platforms import NotAllowed, classify_url
from app.tracker.normalize import employers_match, locations_compatible, norm_employer, norm_location, norm_title
from app.verify.fetch import JOB_BOARDS
from app.verify.posting import Posting

TITLE_MIN = 0.85
CONTAINMENT_MIN = 0.6
DATE_DAYS = 3
TOKEN = re.compile(r"[a-z0-9]+")
RECRUITERS = ("hays", "randstad", "robert half", "talent international", "adecco", "michael page", "page personnel", "kelly services", "chandler macleod",
              "interleave", "hudson", "programmed", "labour hire", "recruitment", "staffing", "search & selection", "manpower", "peoplebank", "paxus", "directly")


@dataclass
class Target:
    """What the third-party listing told us."""
    title: str
    employer: str | None
    location: str | None
    text: str
    url: str = ""
    posted: date | None = None
    ids: set[str] = field(default_factory=set)


@dataclass
class Match:
    posting: Posting
    slug: str | None
    title_sim: float
    evidence: list[str]

    @property
    def strength(self) -> float:
        return self.title_sim + 0.5 * len(self.evidence)


def ids_from(url: str, source_id: str | None) -> set[str]:
    out = {source_id} if source_id and len(source_id) >= 5 else set()
    out |= {seg for seg in re.split(r"[/?&=_]", urlparse(url or "").path + "?" + (urlparse(url or "").query or "")) if len(seg) >= 5 and re.search(r"\d", seg)}
    return {x.lower() for x in out}


def _shingles(text: str, n: int = 4) -> set[str]:
    toks = TOKEN.findall((text or "").lower())
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def containment(snippet: str, full: str) -> float:
    s = _shingles(snippet)
    if len(s) < 3:
        return 0.0
    return len(s & _shingles(full)) / len(s)


def match_one(t: Target, p: Posting, slug: str | None = None) -> Match | None:
    sim = difflib.SequenceMatcher(None, norm_title(t.title), norm_title(p.title)).ratio()
    if sim < TITLE_MIN:
        return None
    if t.employer and p.employer and not employers_match(norm_employer(t.employer), norm_employer(p.employer)):
        return None
    tl = norm_location(t.location)
    if tl and p.locations and not p.remote and not any(locations_compatible(tl, norm_location(x)) for x in p.locations if x):
        return None  # same title, different place: a different vacancy
    ev: list[str] = []
    if containment(t.text, p.text) >= CONTAINMENT_MIN:
        ev.append("text")
    pid = (p.job_id or "").lower()
    if pid and len(pid) >= 4 and (pid in t.ids or pid in (t.url or "").lower()):
        ev.append("job_id")
    if t.posted and p.posted and abs((t.posted - p.posted).days) <= DATE_DAYS:
        ev.append("date")
    return Match(p, slug, sim, ev) if ev else None


def choose(t: Target, candidates: list[tuple[Posting, str | None]]) -> tuple[Match | None, str]:
    matches = [m for p, s in candidates if (m := match_one(t, p, s))]
    if not matches:
        return None, "no posting on the employer's feeds matched title, employer, location and content"
    matches.sort(key=lambda m: -m.strength)
    if len(matches) > 1 and matches[1].strength >= matches[0].strength - 0.01 and matches[1].posting.url != matches[0].posting.url:
        return None, f"{len(matches)} employer postings fit equally well, so none was chosen (checked: {', '.join(m.posting.url for m in matches[:3])})"
    return matches[0], "matched on " + " + ".join(["title", *matches[0].evidence])


def is_job_board(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == b or host.endswith("." + b) for b in JOB_BOARDS) or "adzuna." in host


def detect_route(text: str, listing_url: str, employer: str | None, posting: Posting | None) -> tuple[str, str | None]:
    """Where the application actually goes. The employer's own ATS wins, then email, recruiter, SEEK quick apply."""
    for u in ([posting.apply_url, posting.url] if posting else []) + [listing_url]:
        if u:
            try:
                classify_url(u)
                return "employer_ats", u
            except NotAllowed:
                pass
    emails = find_application_emails(text or "")
    if emails and emails[0]["near_apply_cue"]:
        return "email", emails[0]["address"]
    if posting and posting.url and not is_job_board(posting.url):
        return "employer_site", posting.apply_url or posting.url
    host = (urlparse(listing_url or "").hostname or "").lower()
    if "seek.com" in host or re.search(r"quick apply", text or "", re.I):
        return "seek_quick_apply", listing_url or None
    if employer and any(r in employer.lower() for r in RECRUITERS):
        return "recruiter", None
    if emails:
        return "email", emails[0]["address"]
    return "unknown", None
