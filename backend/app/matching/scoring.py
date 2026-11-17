import re
from dataclasses import dataclass, field
from datetime import date

from app.matching.gate import DEGREE_RE, STUDENT_SIGNAL_RE, GateResult, evaluate_gate
from app.matching.profile import SECONDARY_ROLE_FACTOR, WEST_AND_CBD, CandidateProfile
from app.matching.schema import JobExtract
from app.matching.skills import SimilarityMatcher, norm

WEIGHTS = {
    "technical_skills": 30,
    "experience": 20,
    "qualification": 15,
    "role_relevance": 15,
    "location_preferences": 10,
    "other_eligibility": 10,
}
NICE_WEIGHT = 1 / 3  # a nice-to-have skill counts one third of a required skill
ROLE_LOW, ROLE_HIGH = 0.60, 0.80  # role similarity band mapped to 0..1 (calibrated, see DECISIONS.md)
IN_PROGRESS_DEGREE_CREDIT = 0.7
HOURS_PER_YEAR = 38 * 52


@dataclass
class CategoryScore:
    points: float
    max_points: int
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    job_title: str
    score: float
    gate: GateResult
    breakdown: dict[str, CategoryScore]

    @property
    def eligible(self) -> bool:
        return self.gate.status != "FAIL"


async def _technical(job, p, m: SimilarityMatcher) -> CategoryScore:
    cat = CategoryScore(0, WEIGHTS["technical_skills"])
    if not job.required_skills and not job.nice_to_have_skills:
        cat.points = cat.max_points * 0.5
        cat.notes.append("no skills extracted from the ad: neutral 50%")
        return cat
    have = list(p.skills)
    num = den = 0.0
    for group, weight in ((job.required_skills, 1.0), (job.nice_to_have_skills, NICE_WEIGHT)):
        for s in group:
            den += weight
            hit, sim = await m.match_skill(s, have)
            label = f"{s}{' (nice-to-have)' if weight < 1 else ''}"
            if hit:
                num += weight
                unconf = " [unconfirmed skill]" if p.skills[hit] != "verified" else ""
                cat.matched.append(f"{label} ~ {hit} ({sim:.2f}){unconf}")
            else:
                cat.missing.append(label)
    cat.points = cat.max_points * num / den
    return cat


def _experience(job, p) -> CategoryScore:
    cat = CategoryScore(0, WEIGHTS["experience"])
    need = job.min_years_experience
    if not need:
        cat.points = cat.max_points
        cat.notes.append("no minimum experience stated")
        return cat
    ratio = min(1.0, p.relevant_years / need)
    cat.points = cat.max_points * ratio
    (cat.matched if ratio >= 1 else cat.missing).append(f"{need:g} yrs required, {p.relevant_years:g} yrs relevant")
    return cat


def _qualification(job, p) -> CategoryScore:
    cat = CategoryScore(0, WEIGHTS["qualification"])
    # Student-friendly lines are a positive signal, never a requirement to score: they cannot deduct points.
    signals = [q for q in job.qualifications if STUDENT_SIGNAL_RE.search(q.text)]
    quals = [q for q in job.qualifications if q not in signals]
    for q in signals:
        cat.notes.append(f"student-friendly (positive signal, not scored): {q.text}")
    if not quals:
        cat.points = cat.max_points
        cat.notes.append("no scored qualification requirements")
        return cat
    credits = []
    for q in quals:
        if DEGREE_RE.search(q.text):
            c = {"completed": 1.0, "in_progress": IN_PROGRESS_DEGREE_CREDIT, "none": 0.0}[p.degree_status]
        else:
            c = 1.0 if any(norm(x) in norm(q.text) for x in p.certifications) else 0.0
        credits.append(c)
        (cat.matched if c >= 1 else cat.missing).append(q.text if c in (0.0, 1.0) else f"{q.text} (degree in progress, {c:.0%} credit)")
    cat.points = cat.max_points * sum(credits) / len(credits)
    return cat


async def _role(job, p, m: SimilarityMatcher) -> CategoryScore:
    cat = CategoryScore(0, WEIGHTS["role_relevance"])
    band = lambda sim: max(0.0, min(1.0, (sim - ROLE_LOW) / (ROLE_HIGH - ROLE_LOW)))
    pt, ps = await m.best_target(job.title, p.primary_roles)
    st, ss = await m.best_target(job.title, p.secondary_roles)
    primary, secondary = band(ps), band(ss) * SECONDARY_ROLE_FACTOR
    if primary >= secondary:
        frac, label, target, sim = primary, "primary", pt, ps
    else:
        frac, label, target, sim = secondary, "secondary", st, ss
    cat.points = cat.max_points * frac
    if frac > 0:
        cat.matched.append(f"'{job.title}' ~ {label} target '{target}' ({sim:.2f})")
        if label == "secondary":
            cat.notes.append(f"secondary roles earn {SECONDARY_ROLE_FACTOR:.0%} of role points")
    else:
        cat.missing.append(f"'{job.title}' matches no target role (best '{pt}' {ps:.2f})")
    return cat


def _location(job, p, today: date) -> CategoryScore:
    cat = CategoryScore(0, WEIGHTS["location_preferences"])
    loc = (job.location or "").lower()
    if any(k in loc for k in WEST_AND_CBD):
        tier, label = 1.0, "west Melbourne / CBD"
    elif "melbourne" in loc or re.search(r"\bvic(toria)?\b", loc):
        tier, label = 0.8, "Melbourne (other)"
    elif job.remote and not loc:
        tier, label = 0.8, "remote"
    elif "sydney" in loc or "nsw" in loc:
        tier, label = 0.5, "Sydney"
    elif "canberra" in loc or re.search(r"\bact\b", loc):
        tier, label = 0.4, "Canberra"
    elif not loc:
        tier, label = 0.5, "location not stated"
    else:
        tier, label = 0.2, "other location"
    loc_pts = 5 * tier
    restricted = today < p.restricted_until
    emp = {"part_time": 5, "casual": 5, "contract": 4, "temporary": 4, "internship": 4, "graduate_program": 4,
           "full_time": 2 if restricted else 5, "unknown": 3}[job.employment_type]
    cat.points = loc_pts + emp
    cat.notes += [f"location tier: {label} ({loc_pts:g}/5)", f"employment type {job.employment_type} ({emp}/5)"]
    return cat


def _other(job, p, today: date) -> CategoryScore:
    cat = CategoryScore(0, WEIGHTS["other_eligibility"])
    pts = 0.0
    # licence 3
    if job.requires_drivers_licence and not p.has_licence_and_car:
        cat.missing.append("driver's licence / car required")
    else:
        pts += 3
        cat.matched.append("licence/car" if job.requires_drivers_licence else "no licence requirement")
    # availability 3
    hrs = job.hours_per_week_max if job.hours_per_week_max is not None else job.hours_per_week_min
    if hrs is None:
        pts += 2
        cat.notes.append("hours not stated (2/3)")
    elif hrs <= p.max_hours_per_week_in_semester or today >= p.restricted_until:
        pts += 3
    else:
        pts += 1
        cat.missing.append(f"{hrs:g} hrs/week above semester limit")
    # salary 2: candidate's minimum is unknown, so neutral
    pts += 1
    cat.notes.append("salary scored neutral (no minimum rate set)")
    # deadline 2
    if job.deadline is None:
        pts += 1.5
    else:
        days = (job.deadline - today).days
        if days < 0:
            cat.missing.append("deadline passed")
        elif days <= 3:
            pts += 1
            cat.notes.append(f"closes in {days} day(s)")
        else:
            pts += 2
    cat.points = pts
    return cat


async def score_job(job: JobExtract, p: CandidateProfile, m: SimilarityMatcher, today: date, text_complete: bool = True) -> MatchResult:
    """Deterministic score. The LLM is only used upstream to extract `job`; embeddings only judge similarity."""
    b = {
        "technical_skills": await _technical(job, p, m),
        "experience": _experience(job, p),
        "qualification": _qualification(job, p),
        "role_relevance": await _role(job, p, m),
        "location_preferences": _location(job, p, today),
        "other_eligibility": _other(job, p, today),
    }
    for c in b.values():
        c.points = round(c.points, 1)
    total = round(sum(c.points for c in b.values()), 1)
    return MatchResult(job.title, total, evaluate_gate(job, p, today, text_complete), b)


TIER = {"PASS": 0, "REVIEW": 1}


def rank(results: list[MatchResult]) -> list[MatchResult]:
    """Gate-failed jobs are excluded. Remaining jobs sort by gate tier first (PASS before REVIEW), then by score,
    so a REVIEW job can never outrank a PASS job."""
    return sorted((r for r in results if r.eligible), key=lambda r: (TIER[r.gate.status], -r.score))
