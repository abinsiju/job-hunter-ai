"""Where the third-party ad and the employer's ad disagree. Every discrepancy keeps both quotes so the user can judge for themselves."""
import re
from datetime import date, datetime

from app.matching.extract import EMPLOYMENT_PATTERNS, LICENCE_RE, WR_PATTERNS, YEARS_RE, detect_work_rights
from app.tracker.normalize import locations_compatible, norm_location
from app.verify.posting import Posting

MODE_RE = re.compile(r"\b(hybrid|fully remote|remote|work from home|wfh|on-?site|in[- ]office)\b", re.I)
DEGREE_RE = re.compile(r"(degree|bachelor)[^.\n]{0,60}(required|essential|must|mandatory)|(required|essential|must|mandatory)[^.\n]{0,60}(degree|bachelor)", re.I)
CLOSING_RE = re.compile(r"clos(?:es|ing|e)(?: date)?[:\s]+(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", re.I)
WR_LABEL = {"citizenship": "citizenship", "permanent_residency": "permanent residency", "security_clearance": "security clearance", "unrestricted_work_rights": "unrestricted work rights"}
NOT_MENTIONED = "(not mentioned)"
REVIEW_FIELDS = {"work_rights", "mandatory"}


def quote(text: str, pattern: str | re.Pattern, flags: int = re.I) -> str:
    rx = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
    m = rx.search(text or "")
    if not m:
        return NOT_MENTIONED
    a = max((text.rfind(c, 0, m.start()) for c in ".!?\n"), default=-1) + 1
    ends = [i for i in (text.find(c, m.end()) for c in ".!?\n") if i != -1]
    b = min(ends) + 1 if ends else len(text)
    return re.sub(r"\s+", " ", text[a:b]).strip()[:240]


def _d(field: str, label: str, third: str, employer: str) -> dict:
    return {"field": field, "severity": "review" if field in REVIEW_FIELDS else "note", "label": label, "third_party": third, "employer": employer}


def _parse_closing(text: str) -> date | None:
    m = CLOSING_RE.search(text or "")
    if not m:
        return None
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt).date()
        except ValueError:
            pass
    return None


def _years(text: str) -> float:
    vals = [float(x) for m in YEARS_RE.finditer(text or "") for x in m.groups() if x]
    return max(vals, default=0.0)


def _employment(text: str) -> str | None:
    for name, pat in EMPLOYMENT_PATTERNS:
        if re.search(pat, text or "", re.I):
            return name
    return None


def _same_money(a: float | None, b: float | None) -> bool:
    return a is None or b is None or abs(a - b) <= 0.05 * max(a, b)


def compare(third_text: str, employer: Posting, third_location: str | None = None, third_salary: tuple[float | None, float | None, str] | None = None) -> list[dict]:
    et = employer.text or ""
    out: list[dict] = []
    # work rights: any difference at all matters, in either direction
    tw, ew = detect_work_rights(third_text), detect_work_rights(et)
    for k in sorted(tw ^ ew):
        out.append(_d("work_rights", f"Work rights: {WR_LABEL[k]}", quote(third_text, WR_PATTERNS[k]) if k in tw else NOT_MENTIONED,
                      quote(et, WR_PATTERNS[k]) if k in ew else NOT_MENTIONED))
    # mandatory requirements
    for label, rx in (("Driver's licence required", LICENCE_RE), ("Degree required", DEGREE_RE)):
        a, b = bool(rx.search(third_text or "")), bool(rx.search(et))
        if a != b:
            out.append(_d("mandatory", label, quote(third_text, rx) if a else NOT_MENTIONED, quote(et, rx) if b else NOT_MENTIONED))
    ty, ey = _years(third_text), _years(et)
    if ty != ey:
        out.append(_d("mandatory", "Years of experience asked for", quote(third_text, YEARS_RE) if ty else NOT_MENTIONED, quote(et, YEARS_RE) if ey else NOT_MENTIONED))
    # location / hybrid
    tl = norm_location(third_location)
    if tl and employer.locations and not employer.remote and not any(locations_compatible(tl, norm_location(x)) for x in employer.locations if x):
        out.append(_d("location", "Location", third_location or "", ", ".join(employer.locations)))
    tm, em = {m.lower() for m in MODE_RE.findall(third_text or "")}, {m.lower() for m in MODE_RE.findall(et)}
    if tm and em and tm != em:
        out.append(_d("location", "Remote / hybrid / on-site", quote(third_text, MODE_RE), quote(et, MODE_RE)))
    # hours / employment type
    t_emp, e_emp = _employment(third_text), _employment(et) or (_employment(employer.employment.replace("_", " ")) if employer.employment else None)
    if t_emp and e_emp and t_emp != e_emp:
        out.append(_d("employment", "Employment type / hours", quote(third_text, dict(EMPLOYMENT_PATTERNS)[t_emp]), quote(et, dict(EMPLOYMENT_PATTERNS)[e_emp])
                      if _employment(et) else employer.employment or ""))
    # salary
    if third_salary and third_salary[2] != "unknown" and employer.salary_period != "unknown":
        if third_salary[2] != employer.salary_period or not (_same_money(third_salary[0], employer.salary_min) and _same_money(third_salary[1], employer.salary_max)):
            fmt = lambda lo, hi, per: f"{lo:g}" + (f"–{hi:g}" if hi and hi != lo else "") + f" per {per}" if lo is not None else NOT_MENTIONED
            out.append(_d("salary", "Salary", fmt(*third_salary[:2], third_salary[2]), fmt(employer.salary_min, employer.salary_max, employer.salary_period)))
    # closing date
    tc, ec = _parse_closing(third_text), employer.valid_through or _parse_closing(et)
    if tc and ec and tc != ec:
        out.append(_d("closing_date", "Closing date", tc.isoformat(), ec.isoformat()))
    return out
