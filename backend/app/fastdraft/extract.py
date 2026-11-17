"""Read a job ad in milliseconds, with rules and no language model (the model took ~22 s of a ~24 s draft). Gate-critical facts (work rights, fees,
unpaid, licence) come from the same text rules the full pipeline treats as the authority."""
import re

from app.fastdraft.vocab import CAPABILITIES, IT_TERMS
from app.matching.extract import (EMPLOYMENT_PATTERNS, LICENCE_RE, PLACEMENT_PROGRAM_RE, UNPAID_RE, YEARS_RE, _fallbacks, detect_applicant_pays, detect_work_rights)
from app.matching.schema import JobExtract, Qualification

CITIES = ["Melbourne", "Sydney", "Canberra", "Brisbane", "Adelaide", "Perth", "Hobart", "Darwin", "Geelong", "Wollongong", "Newcastle", "Gold Coast"]
NICE_RE = re.compile(r"^\W*(desirable|nice to have|preferred|bonus|advantageous|an advantage|highly regarded|beneficial|would be (great|a plus))", re.I | re.M)
HEADER_RE = re.compile(r"^(title|job title|position|role|employer|company|organisation|location|employment( type)?|salary|hours)\s*[:\-]\s*(.+)$", re.I)
CLEAR_RE = re.compile(r"\b(baseline|nv[12]|negative vetting|positive vetting|top secret|protected)\b[^.\n]{0,20}(clearance|vetting)?", re.I)
ROLE_WORDS_LOWER = {"support", "service", "help", "it", "melbourne", "sydney", "canberra", "brisbane", "adelaide", "perth"}
DEGREE_RE = re.compile(r"[^.\n]*\b(bachelor|degree|diploma|certificate (iii|iv|3|4)|cert (iii|iv))\b[^.\n]*", re.I)


def _headers(text: str) -> dict:
    out = {}
    for line in text.splitlines()[:12]:
        m = HEADER_RE.match(line.strip())
        if m:
            out.setdefault(m.group(1).lower().split()[0], m.group(3).strip())
    return out


GENERIC_LINE_RE = re.compile(r"^(full )?job (description|details|summary|overview)|^(pay|salary|benefits?|job type|location|work location|shift|schedule|overview|about( us| the role)?|"
                             r"description|apply( now)?|posted|closing|reference|summary|responsibilities|requirements|employee discount|free \w+)\b|^[\W\d$]+$|^\$", re.I)
ROLE_WORD_RE = re.compile(r"\b(analyst|officer|technician|engineer|administrator|coordinator|specialist|consultant|manager|support|assistant|developer|architect|intern|graduate|helpdesk|help desk|service desk)\b", re.I)


def _title(text: str, h: dict) -> str:
    t = h.get("title") or h.get("job") or h.get("position") or h.get("role")
    if not t:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not HEADER_RE.match(ln.strip())]
        real = [ln for ln in lines[:25] if len(ln) <= 90 and not GENERIC_LINE_RE.match(ln)]
        t = next((ln for ln in real if ROLE_WORD_RE.search(ln)), None) or next(iter(real), None) or "Role"
    t = re.split(r"\s[-–—|@]\s", t)[0]
    t = re.sub(r"\(.*?\)", " ", t)
    return re.sub(r"\s+", " ", t).strip(" :-")[:120] or "Role"


def _employer(text: str, h: dict) -> str | None:
    e = h.get("employer") or h.get("company") or h.get("organisation")
    if e:
        return e[:120]
    for m in re.finditer(r"\b(?i:join|at)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})(?=[\s,.!])", text[:600]):
        cand = m.group(1)
        if cand.lower() in ("our", "the", "a", "us", "this", "your") or skill_hits(cand) or cand.lower() in ROLE_WORDS_LOWER:
            continue  # "supporting staff at Windows"... is a technology, not an employer
        return cand
    return None


def _location(text: str, h: dict) -> str | None:
    if h.get("location"):
        return h["location"][:120]
    for c in CITIES:
        if re.search(rf"\b{c}\b", text[:1500], re.I):
            return c
    return None


def skill_hits(text: str, extra: list[str] | None = None) -> list[tuple[str, str, bool]]:
    """(canonical name, the words the ad used, is it before any 'desirable' heading)."""
    m = NICE_RE.search(text)
    cut = m.start() if m else len(text)
    vocab = {**IT_TERMS, **{n: [re.escape(n.lower())] for n in (extra or [])}}
    out = []
    for canon, pats in vocab.items():
        hits = [(h.start(), h.group(0)) for p in pats for h in re.finditer(p, text, re.I)]
        if hits:
            pos, surface = min(hits, key=lambda x: (x[0], -len(x[1])))
            out.append((canon, surface.strip() if len(surface.strip()) > 2 else canon, pos < cut))
    return out


def find_skills(text: str, extra: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(required, nice_to_have) skill phrases AS THE AD WRITES THEM. Everything after a 'desirable' heading is nice-to-have."""
    hits = skill_hits(text, extra)
    dedupe = lambda xs: list(dict.fromkeys(x for x in xs if x))
    required = dedupe(sur for _, sur, req in hits if req)
    return required, [n for n in dedupe(sur for _, sur, req in hits if not req) if n not in required]


def ad_capabilities(text: str) -> list[str]:
    """Everyday capabilities the ad mentions (service desk, troubleshooting, printers…): ATS keywords, never claims."""
    return list(dict.fromkeys(sur for c, sur, _ in skill_hits(text) if c in CAPABILITIES))


def extract_fast(text: str, verified_skill_names: list[str] | None = None, title: str = "", employer: str = "") -> JobExtract:
    h = _headers(text)
    if title.strip():
        h["title"] = title.strip()[:120]  # what you typed beats a guess
    if employer.strip():
        h["employer"] = employer.strip()[:120]
    title = _title(text, h)
    keep = [(c, sur, req) for c, sur, req in skill_hits(text, verified_skill_names)
            if c not in CAPABILITIES and sur.lower() not in title.lower() and title.lower() not in sur.lower()]  # named technologies only; never a word from the title
    required = list(dict.fromkeys(sur for _, sur, req in keep if req))
    nice = [n for n in dict.fromkeys(sur for _, sur, req in keep if not req) if n not in required]
    hours = re.search(r"(\d{1,2})(?:\s*(?:-|to)\s*(\d{1,2}))?\s*(?:hours|hrs)\s*(?:per|a|/|each)\s*week", text, re.I)
    quals = [Qualification(text=re.sub(r"\s+", " ", m.group(0)).strip()[:200], mandatory=bool(re.search(r"\b(must|essential|required|mandatory)\b", m.group(0), re.I)))
             for m in DEGREE_RE.finditer(text)][:3]
    clear = CLEAR_RE.search(text)
    job = JobExtract(title=_title(text, h), employer=_employer(text, h), required_skills=required, nice_to_have_skills=nice, qualifications=quals,
                     work_rights_requirements=sorted(detect_work_rights(text)), clearance_level=(clear.group(0).strip() if clear and "security_clearance" in detect_work_rights(text) else None),
                     fixed_term=bool(re.search(r"fixed[- ]term|maternity (leave )?cover|temporary", text, re.I)), location=_location(text, h),
                     hours_per_week_min=float(hours.group(1)) if hours else None, hours_per_week_max=float(hours.group(2) or hours.group(1)) if hours else None,
                     remote=bool(re.search(r"\b(remote|work from home|wfh)\b", text, re.I)), requires_drivers_licence=bool(LICENCE_RE.search(text)),
                     applicant_pays=detect_applicant_pays(text), unpaid=bool(UNPAID_RE.search(text)))
    job.placement_program = bool(PLACEMENT_PROGRAM_RE.search(text)) and bool(re.search(r"\bintern(ship)?s?\b", job.title, re.I))
    for kind, pat in EMPLOYMENT_PATTERNS:
        if re.search(pat, h.get("employment", "") + " " + text[:1500], re.I):
            job.employment_type = kind
            break
    _fallbacks(job, text)
    return job
