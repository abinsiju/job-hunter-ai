"""An employer's own posting, from a page (schema.org JobPosting JSON-LD) or a public ATS feed, in one common shape. Also: is it still open?"""
import html as htmllib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlparse

from app.discovery.base import html_to_text

LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
CLOSED_RE = re.compile(
    r"no longer (accepting|available|open|taking)|(position|role|job|vacancy|posting|advert|opportunity)\s+(has\s+|is\s+|have\s+)?(now\s+)?(been\s+)?"
    r"(filled|closed|expired|removed|withdrawn)|(applications?|recruitment)\s+(are\s+|is\s+|have\s+)?(now\s+)?closed|this (job|position|role) (has )?expired|"
    r"we are no longer (hiring|recruiting)|sorry,?\s+(this|the) (job|position|role)", re.I)
UNITS = {"HOUR": "hour", "YEAR": "year", "ANNUM": "year", "MONTH": "month", "WEEK": "week", "DAY": "day"}


@dataclass
class Posting:
    title: str
    url: str
    text: str = ""
    employer: str | None = None
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    posted: date | None = None
    valid_through: date | None = None
    employment: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_period: str = "unknown"
    job_id: str | None = None
    apply_url: str | None = None
    source: str = "page"  # json-ld | feed:<platform>
    open: bool | None = None  # None = could not tell
    closed_reason: str | None = None
    detail_url: str | None = None  # feeds that list first and give the full ad on request


def parse_date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, (int, float)):  # epoch milliseconds (Lever)
        try:
            return datetime.utcfromtimestamp(v / 1000).date()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def _text(v) -> str:
    return html_to_text(htmllib.unescape(v) if isinstance(v, str) and "&lt;" in v else v)


def _iter_ld(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _iter_ld(x)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield obj
        for v in obj.get("@graph", []) if isinstance(obj.get("@graph"), list) else []:
            yield from _iter_ld(v)


def _locations(jp: dict) -> tuple[list[str], bool]:
    locs, remote = [], str(jp.get("jobLocationType", "")).upper() == "TELECOMMUTE"
    for loc in jp.get("jobLocation") or []:
        pass
    raw = jp.get("jobLocation") or []
    for loc in raw if isinstance(raw, list) else [raw]:
        a = (loc or {}).get("address") or {}
        if isinstance(a, str):
            locs.append(a)
            continue
        parts = [a.get("addressLocality"), a.get("addressRegion")]
        country = a.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        s = ", ".join(str(p) for p in parts if p) or (str(country) if country else "")
        if s:
            locs.append(s)
    return locs, remote


def _salary(jp: dict) -> tuple[float | None, float | None, str]:
    bs = jp.get("baseSalary")
    if not isinstance(bs, dict):
        return None, None, "unknown"
    v = bs.get("value") if isinstance(bs.get("value"), dict) else bs
    try:
        lo = float(v.get("minValue", v.get("value")))
        hi = float(v.get("maxValue", lo))
    except (TypeError, ValueError):
        return None, None, "unknown"
    return lo, hi, UNITS.get(str(v.get("unitText", "")).upper(), "unknown")


def postings_from_html(html: str, url: str) -> list[Posting]:
    out = []
    for block in LD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for jp in _iter_ld(data):
            locs, remote = _locations(jp)
            lo, hi, per = _salary(jp)
            org = jp.get("hiringOrganization")
            ident = jp.get("identifier")
            emp = jp.get("employmentType")
            out.append(Posting(
                title=htmllib.unescape(str(jp.get("title") or "")).strip(), url=url, text=_text(jp.get("description") or ""),
                employer=(org.get("name") if isinstance(org, dict) else org) or None, locations=locs, remote=remote,
                posted=parse_date(jp.get("datePosted")), valid_through=parse_date(jp.get("validThrough")),
                employment=(emp[0] if isinstance(emp, list) and emp else emp) or None, salary_min=lo, salary_max=hi, salary_period=per,
                job_id=str(ident.get("value") if isinstance(ident, dict) else ident) if ident else None, apply_url=jp.get("url") or None, source="json-ld"))
    return out


def judge_open(p: Posting, page_text: str, status: int, today: date) -> Posting:
    """Fill in whether the posting is still open, from the page's own signals."""
    if status in (404, 410):
        p.open, p.closed_reason = False, f"The employer's page no longer exists (HTTP {status})."
    elif p.valid_through and p.valid_through < today:
        p.open, p.closed_reason = False, f"The employer's closing date ({p.valid_through.isoformat()}) has passed."
    else:
        m = CLOSED_RE.search(page_text or "")
        if m:
            p.open, p.closed_reason = False, f"The employer's page says the job is closed: “{m.group(0)}”."
        else:
            p.open = True
    return p


def workday_json_url(url: str) -> str | None:
    """A human Workday job link -> the public JSON the career site itself reads. (Best effort; Workday has no official public feed.)"""
    u = urlparse(url)
    if not (u.hostname or "").endswith(".myworkdayjobs.com"):
        return None
    parts = [x for x in u.path.split("/") if x]
    if "job" not in parts:
        return None
    i = parts.index("job")
    site = parts[i - 1] if i >= 1 else None
    if site and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", site):  # a locale, not the site name
        site = parts[i - 2] if i >= 2 else None
    tenant = u.hostname.split(".")[0]
    if not site:
        return None
    return f"{u.scheme}://{u.hostname}/wday/cxs/{tenant}/{site}/job/{'/'.join(parts[i + 1:])}"


def posting_from_workday(data: dict, url: str) -> Posting | None:
    info = data.get("jobPostingInfo") if isinstance(data, dict) else None
    if not info:
        return None
    return Posting(title=info.get("title", ""), url=info.get("externalUrl") or url, text=_text(info.get("jobDescription") or ""),
                   employer=(data.get("hiringOrganization") or {}).get("name"), locations=[info.get("location")] if info.get("location") else [],
                   posted=parse_date(info.get("startDate")), job_id=info.get("jobReqId"), apply_url=info.get("externalUrl") or url, source="feed:workday",
                   open=False if info.get("canApply") is False else True,
                   closed_reason="Workday says this job can no longer be applied for." if info.get("canApply") is False else None)
