import json
import logging
import re

from pydantic import ValidationError

from app.ai.base import AIProvider
from app.matching.schema import JobExtract

log = logging.getLogger("jobhunter.extract")

FIELD_HELP = {
    "required_skills": "Technologies, tools, platforms and technical skills the advertisement asks for or says you will use, as short "
                       "names, e.g. 'Microsoft 365', 'Active Directory', 'Azure', 'VMware', 'ITIL'. Include every one that is named. "
                       "No soft skills.",
    "nice_to_have_skills": "Technologies or skills described as desirable, a plus, advantageous or highly regarded.",
    "qualifications": "ONLY formal credentials the ad names: degrees, diplomas, certificates, licences, registrations, accreditations "
                      "(e.g. 'Bachelor of Information Technology', 'Cisco CCNA', 'Working With Children Check'). NEVER skills, experience, "
                      "duties, personality traits or soft skills. If the ad names no formal credential, return an empty list.",
}


def _extraction_schema() -> dict:
    """The schema sent to the model. EVERY field is required: with only `title` required, a small model answering a
    long ad returned a minimal object and silently skipped required_skills (0 skills on 8 of 8 real ads)."""
    s = JobExtract.model_json_schema()
    for k, d in FIELD_HELP.items():
        s["properties"][k]["description"] = d
    s["required"] = list(s["properties"])
    return s


SCHEMA = _extraction_schema()

SYSTEM = """You extract facts from an Australian job advertisement into JSON. You do NOT score or judge fit.
Use ONLY what the advertisement states. If something is not stated, use null / [] / false / "unknown". Never guess.
JSON keys: title, employer, required_skills[], nice_to_have_skills[], min_years_experience (number|null),
qualifications[{text, mandatory}], work_rights_requirements[] (any of: citizenship, permanent_residency,
security_clearance, unrestricted_work_rights), clearance_level (string|null),
employment_type (full_time|part_time|casual|contract|temporary|internship|graduate_program|unknown),
fixed_term (bool), hours_per_week_min, hours_per_week_max (numbers|null), location (string|null), remote (bool),
requires_drivers_licence (bool), unpaid (bool), salary {min, max, period: hour|year|unknown}, deadline (YYYY-MM-DD|null).
Skills are short names (e.g. "Microsoft 365", "Active Directory"), not sentences.
The advertisement may be long: read all of it. Always fill required_skills with every named technology, tool or platform;
an empty list is only correct if the ad names none."""


EXTRACT_TEMPERATURE = 0.0  # structured outputs (schema as `format`); plain format=json 500'd at temperature 0

WR_SYNONYMS = {
    "citizenship": "citizenship", "citizen": "citizenship", "australian citizen": "citizenship", "australian citizenship": "citizenship",
    "permanent_residency": "permanent_residency", "permanent residency": "permanent_residency", "permanent resident": "permanent_residency", "pr": "permanent_residency",
    "security_clearance": "security_clearance", "security clearance": "security_clearance", "clearance": "security_clearance",
    "unrestricted_work_rights": "unrestricted_work_rights", "unrestricted work rights": "unrestricted_work_rights", "full work rights": "unrestricted_work_rights",
}
# Deterministic detection of the gate-critical fields: the ad text, not a small model, decides.
WR_PATTERNS = {
    "citizenship": r"\b(australian )?citizen(s|ship)?\b",
    "permanent_residency": r"permanent residen(t|cy)|\bPR (holder|status)s?\b|\bor PR\b|\bPR or\b",
    "security_clearance": r"security clearance|baseline (vetting|clearance)|negative vetting|positive vetting|\bNV[12]\b|\bAGSVA\b",
    "unrestricted_work_rights": r"unrestricted work rights|full work rights",
}
LICENCE_RE = re.compile(r"(driver'?s?|driving)\s+licen[cs]e.{0,40}(required|essential|must)|(required|essential|must).{0,40}(driver'?s?|driving)\s+licen[cs]e|must (hold|have).{0,30}licen[cs]e", re.I | re.S)


def detect_work_rights(text: str) -> set[str]:
    return {k for k, pat in WR_PATTERNS.items() if re.search(pat, text, re.I if k != "permanent_residency" else 0) or (k == "permanent_residency" and re.search(r"permanent residen(t|cy)", text, re.I))}


EMPLOYMENT_PATTERNS = [  # first match wins; only used when the model said "unknown"
    ("graduate_program", r"graduate (program|programme|role|intake)|grad program"),
    ("part_time", r"part[- ]time"), ("casual", r"\bcasual\b"), ("full_time", r"full[- ]time"),
    ("internship", r"\bintern(ship)?\b"), ("contract", r"\bcontract\b|fixed[- ]term"),
]
YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+|-\s*\d+(?:\.\d+)?)?\s*(?:years?|yrs?)\b[^.\n]{0,40}(?:experience|exp)|(?:experience)[^.\n]{0,30}?(\d+)\+?\s*years?", re.I)


UNPAID_RE = re.compile(r"\bunpaid\b|no remuneration|for experience only|voluntary (role|position)", re.I)


def _fallbacks(job: JobExtract, text: str) -> None:
    if UNPAID_RE.search(text):
        job.unpaid = True
    if job.employment_type == "unknown":
        for kind, pat in EMPLOYMENT_PATTERNS:
            if re.search(pat, text, re.I):
                job.employment_type = kind
                break
    if job.min_years_experience is None and (m := YEARS_RE.search(text)):
        job.min_years_experience = float(m.group(1) or m.group(2))


def _clean_work_rights(raw: dict) -> dict:
    kept, dropped = [], []
    for v in raw.get("work_rights_requirements") or []:
        m = WR_SYNONYMS.get(str(v).strip().lower())
        (kept if m else dropped).append(m or v)
    if dropped:
        log.warning("dropped unrecognised work-rights values", extra={"values": dropped})
    return {**raw, "work_rights_requirements": list(dict.fromkeys(kept))}


QUALIFIER_RE = re.compile(
    r"^(?:(?:strong|deep|solid|good|proven|extensive|advanced|working|hands[- ]on|demonstrated|excellent)\s+)*"
    r"(?:grasp|knowledge|understanding|experience|proficiency|skills?|expertise|familiarity|exposure)\s+(?:of|with|in|using|on|around)\s+", re.I)
SOFT_RE = re.compile(r"\b(communicat\w*|problem[- ]solving|team ?work|teamwork|collaborat\w*|leadership|customer service|attention to detail|"
                     r"time management|interpersonal|organi[sz]ation\w*|initiative|self[- ]motivated|work ethic|triage|multi-?task\w*|"
                     r"analytical|passion\w*|motivat\w*|client[- ]?(?:friendly|facing|focused)|incident, request)\b", re.I)
MAX_SKILL_WORDS = 6

# Pay-to-get-a-job schemes: decided by the ad text (like work rights), never by the model.
FEE_RES = [re.compile(x, re.I) for x in (
    r"\b(?:program(?:me)?|placement|registration|application|participation|enrol(?:l)?ment|administration|matching|joining|training|course|internship) fees?\b",
    r"\b(?:pay|paying|payment of|contribute)\s+(?:a |an |the |your )?(?:\$\s?\d[\d,]*|fees?|upfront|deposit|tuition)\b",
    r"\byou (?:will|must|need to|are required to|have to|'ll) (?:pay|contribute|invest)\b",
    r"\btuition\b", r"\bupfront (?:cost|payment|fee)s?\b", r"\b(?:cost|price) of the (?:program(?:me)?|course|placement|internship)\b",
    r"\bfee[- ]paying\b", r"\binvest(?:ment)? (?:of|in) \$")]
NEGATION_RE = re.compile(r"\b(no|not|without|free|never|zero|nothing)\b[^.\n]{0,25}$", re.I)
PLACEMENT_PROGRAM_RE = re.compile(
    r"\b(?:matched|matching|connecting|places?|placed|placing)\s+(?:\d[\d,]*\s+)?(?:\w+\s+){0,3}(?:interns|trainees|graduates|candidates|emerging talent)\b|"
    r"\b(?:internship|placement|work experience) (?:program(?:me)?|provider|agency)\b", re.I)


def detect_applicant_pays(text: str) -> bool:
    """True if the ad says the applicant pays. A match is ignored when it is negated ('no fee', 'free of charge')."""
    for rx in FEE_RES:
        for m in rx.finditer(text):
            if not NEGATION_RE.search(text[max(0, m.start() - 40):m.start()] + m.group(0)[:0]):
                return True
    return False
CREDENTIAL_RE = re.compile(
    r"\b(degrees?|bachelor\w*|master'?s?|diploma|certificates?|certification|certified|cert\s?(?:i{1,3}|iv)|licen[cs]es?|registration|"
    r"accreditation|ccna|ccnp|ccie|comptia|a\+|network\+|security\+|itil\s?(?:v?\d|foundation)|az-\d{3}|ms-\d{3}|sc-\d{3}|"
    r"working with children|police check|white card|first aid)\b", re.I)
DRIVERS_LICENCE_RE = re.compile(r"driver'?s?|driving|car licen[cs]e", re.I)  # handled by requires_drivers_licence
MANDATORY_CUE_RE = re.compile(r"\b(must|essential|required|requirement|mandatory|necessary|need to (?:have|hold))\b", re.I)
GENERIC_END_RE = re.compile(r"\b(knowledge|experience|skills?|ability|understanding|expertise)$", re.I)  # vague, not a named technology


def clean_qualifications(quals: list) -> list:
    """Keep only formal credentials. Skill/experience statements are not qualifications (they must never reach the gate
    as 'mandatory qualification not held'), and 'mandatory' needs the text itself to say so."""
    out = []
    for q in quals:
        if not CREDENTIAL_RE.search(q.text) or DRIVERS_LICENCE_RE.search(q.text):
            continue
        out.append(q.model_copy(update={"mandatory": bool(q.mandatory and MANDATORY_CUE_RE.search(q.text))}))
    return out


def clean_skills(skills: list[str]) -> list[str]:
    """Strip 'Strong grasp of ...' qualifiers; drop soft skills and sentence-like entries; dedupe (case-insensitive)."""
    out, seen = [], set()
    for raw in skills or []:
        s = QUALIFIER_RE.sub("", raw.strip()).strip(" .;,-")
        if s.count("(") != s.count(")"):  # a truncated fragment such as "backup tech (V"
            continue
        if not s or SOFT_RE.search(s) or GENERIC_END_RE.search(s) or len(s.split()) > MAX_SKILL_WORDS or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
    return out


async def extract_job(ai: AIProvider, description: str, retries: int = 1) -> JobExtract:
    last: Exception | None = None
    for attempt in range(retries + 1):
        raw = await ai.chat(SYSTEM, description, json_schema=SCHEMA, temperature=EXTRACT_TEMPERATURE)
        try:
            job = JobExtract.model_validate(_clean_work_rights(json.loads(raw)))
            # The ad text is the authority for gate-critical work rights. Model claims without textual evidence are
            # ignored (a hallucinated "citizenship required" would wrongly exclude a job); misses are added.
            found = detect_work_rights(description)
            model_said = set(job.work_rights_requirements)
            if model_said != found:
                log.warning("work-rights disagreement", extra={"model": sorted(model_said), "text": sorted(found)})
            job.work_rights_requirements = sorted(found)
            job.required_skills, job.nice_to_have_skills = clean_skills(job.required_skills), clean_skills(job.nice_to_have_skills)
            job.qualifications = clean_qualifications(job.qualifications)
            job.applicant_pays = detect_applicant_pays(description)  # the ad text decides, not the model
            job.placement_program = bool(PLACEMENT_PROGRAM_RE.search(description)) and (
                job.employment_type == "internship" or bool(re.search(r"\bintern(ship)?s?\b", job.title, re.I)))
            _fallbacks(job, description)
            if LICENCE_RE.search(description):
                job.requires_drivers_licence = True
            return job
        except (json.JSONDecodeError, ValidationError) as e:
            last = e
            log.warning("extraction invalid", extra={"attempt": attempt, "error": type(e).__name__})
    raise ValueError(f"Could not extract a valid JobExtract: {type(last).__name__}")
