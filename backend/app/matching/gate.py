import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.tracker.normalize import employers_match, norm_employer
from app.matching.profile import (CASUAL_LOADING, FULL_TIME_HOURS_PER_YEAR, MIN_WAGE_HOURLY, CandidateProfile,
                                  find_duplicate)
from app.matching.schema import JobExtract

DEGREE_RE = re.compile(r"bachelor|degree|tertiary|university", re.I)
# "Students welcome / currently studying / final-year" lines are a POSITIVE eligibility signal for a current student,
# not a qualification requirement: they are excluded from qualification scoring and can never deduct points.
INTERNSHIP_RE = re.compile(r"\b(intern|internship|work experience|traineeship|placement)\b", re.I)
STUDENT_SIGNAL_RE = re.compile(
    r"\bstudents?\b|\bstudying\b|currently enrolled|\benrolled\b|final[- ]year|\bundergraduates?\b|\bpursuing\b", re.I)


@dataclass
class GateResult:
    status: Literal["PASS", "REVIEW", "FAIL"]
    fail_reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def evaluate_gate(job: JobExtract, p: CandidateProfile, today: date, text_complete: bool = True) -> GateResult:
    fails: list[str] = []
    flags: list[str] = []
    wr = set(job.work_rights_requirements)

    if job.applicant_pays:
        fails.append("the applicant is asked to pay (program/placement/registration fee or tuition): not a real job offer")
    blocked = next((b for b in p.blocked_employers if employers_match(norm_employer(b), norm_employer(job.employer))), None)
    if blocked:
        fails.append(f"employer is on your blocked list ({blocked})")
    if job.placement_program and not job.applicant_pays:
        flags.append("internship run by a placement/matching agency: these programs often charge the candidate, check for fees before engaging")
    if "security_clearance" in wr:
        fails.append(f"requires security clearance ({job.clearance_level or 'level not stated'})")
    if wr & {"citizenship", "permanent_residency"}:
        what = " / ".join(sorted(wr & {"citizenship", "permanent_residency"}))
        fails.append(f"requires {what}")  # also for graduate programs: the candidate cannot meet it
    if "unrestricted_work_rights" in wr:
        flags.append("states unrestricted work rights: answer NO to the screening question (student visa)")

    for q in job.qualifications:
        if not q.mandatory:
            continue
        if STUDENT_SIGNAL_RE.search(q.text):  # "students welcome / currently studying": met by a current student, never a barrier
            if p.degree_status == "none":
                fails.append(f"requires current study, candidate is not studying: {q.text}")
            continue
        if DEGREE_RE.search(q.text):
            if p.degree_status == "none":
                fails.append(f"mandatory qualification not held: {q.text}")
            elif p.degree_status == "in_progress":
                flags.append(f"mandatory degree is still in progress (due June 2027): {q.text}")
        elif not any(c.lower() in q.text.lower() for c in p.certifications):
            fails.append(f"mandatory qualification not held: {q.text}")

    hrs = job.hours_per_week_max if job.hours_per_week_max is not None else job.hours_per_week_min
    if today < p.restricted_until:
        if job.employment_type == "full_time":
            note = "full-time, check timing"
            if job.fixed_term or job.employment_type in ("contract", "temporary"):
                note += " (temporary: only OK if it falls inside an official Example University break)"
            flags.append(note)
        elif hrs is not None and hrs > p.max_hours_per_week_in_semester:
            flags.append(f"{hrs:g} hrs/week exceeds the {p.max_hours_per_week_in_semester:g} hr/week semester limit: check timing")
    else:
        flags.append("course end passed: work rights depend on the 485 visa, which is not yet granted")

    if not text_complete:  # the work-rights / mandatory-requirement checks only saw a snippet of the ad
        flags.append("only a snippet of the ad was available: work-rights and mandatory requirements may be missing, read the full ad before applying")
    flags += pay_flags(job)
    dup = find_duplicate(job.employer, p.applied_employers)
    if dup:
        flags.append(f"possible duplicate: already applied to {dup}")
    if job.deadline and job.deadline < today:
        flags.append(f"deadline passed ({job.deadline})")

    status = "FAIL" if fails else ("REVIEW" if flags else "PASS")
    return GateResult(status, fails, flags)


def hourly_equivalent(job: JobExtract) -> float | None:
    """Lowest advertised pay as dollars/hour, or None if unknown."""
    lo = job.salary.min if job.salary.min is not None else job.salary.max
    if lo is None or job.salary.period == "unknown":
        return None
    return lo if job.salary.period == "hour" else lo / FULL_TIME_HOURS_PER_YEAR


def pay_flags(job: JobExtract) -> list[str]:
    """Flag unpaid roles and pay below the national minimum wage (awards may be higher; not checked)."""
    if job.unpaid:
        return ["unpaid role: check it is a lawful placement (e.g. a course requirement) before applying"]
    rate = hourly_equivalent(job)
    if rate is None:
        if job.employment_type == "internship" or INTERNSHIP_RE.search(job.title):
            return ["internship/placement with no pay stated: check that it is paid before applying"]
        return []
    floor = MIN_WAGE_HOURLY * (1 + CASUAL_LOADING if job.employment_type == "casual" else 1)
    if rate < floor - 0.005:
        kind = "casual minimum incl. 25% loading" if job.employment_type == "casual" else "national minimum wage"
        return [f"pay ${rate:.2f}/hr is below the {kind} (${floor:.2f}/hr): check the applicable award"]
    return []
