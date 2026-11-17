from datetime import date

import pytest

from app.matching.gate import evaluate_gate
from app.matching.profile import find_duplicate, screening_answer
from app.matching.schema import JobExtract
from app.matching.scoring import WEIGHTS, rank, score_job
from app.matching.skills import SimilarityMatcher, norm
from tests.conftest import TODAY
from tests.fixtures.jobs import EXTRACT


@pytest.fixture
def matcher(ai):
    return SimilarityMatcher(ai)


async def run_all(profile, matcher):
    return {k: await score_job(j, profile, matcher, TODAY) for k, j in EXTRACT.items()}


def test_weights_sum_to_100():
    assert sum(WEIGHTS.values()) == 100 and list(WEIGHTS.values()) == [30, 20, 15, 15, 10, 10]


async def test_aps_role_fails_gate_even_with_decent_score(profile, matcher):
    r = await score_job(EXTRACT["aps_role"], profile, matcher, TODAY)
    assert r.gate.status == "FAIL"
    assert any("citizenship" in x for x in r.gate.fail_reasons) and any("clearance" in x for x in r.gate.fail_reasons)
    assert r.score > 40  # it matches well on skills/role; the gate, not the score, rejects it


async def test_high_score_never_overrides_gate(profile, matcher):
    res = await run_all(profile, matcher)
    ranked = rank(list(res.values()))
    assert res["aps_role"] not in ranked
    assert len(ranked) == 4  # APS excluded


async def test_ranking_is_sensible(profile, matcher):
    res = await run_all(profile, matcher)
    ranked = [r.job_title for r in rank(list(res.values()))]
    assert ranked[0] == "IT Support Officer"  # part-time, west Melbourne, matches skills
    assert "IT Support Officer APS4" not in ranked
    assert all(r.gate.flags for r in rank(list(res.values())) if r.gate.status == "REVIEW")
    assert res["it_support_pt"].gate.status == "PASS"


async def test_fulltime_before_june_2027_is_flagged_not_failed(profile, matcher):
    for k in ("service_desk_ft", "junior_dev"):
        g = (await score_job(EXTRACT[k], profile, matcher, TODAY)).gate
        assert g.status == "REVIEW" and any("full-time, check timing" in f for f in g.flags)


async def test_graduate_program_without_pr_requirement_is_allowed_with_flags(profile, matcher):
    g = (await score_job(EXTRACT["grad_program"], profile, matcher, TODAY)).gate
    assert g.status == "REVIEW" and any("unrestricted work rights" in f for f in g.flags)


async def test_breakdown_has_points_matched_and_missing(profile, matcher):
    r = await score_job(EXTRACT["junior_dev"], profile, matcher, TODAY)
    assert set(r.breakdown) == set(WEIGHTS)
    for name, c in r.breakdown.items():
        assert 0 <= c.points <= WEIGHTS[name] == c.max_points
    assert abs(sum(c.points for c in r.breakdown.values()) - r.score) < 0.11
    t = r.breakdown["technical_skills"]
    assert any(m.startswith("Python") for m in t.matched) and any("Git" in m for m in t.missing)
    assert any("unconfirmed" in m for m in t.matched)


async def test_nice_to_have_counts_one_third(profile, matcher):
    base = dict(title="Dev", employment_type="part_time", location="Exampleton")
    a = await score_job(JobExtract(**base, required_skills=["Python", "Rust"]), profile, matcher, TODAY)
    b = await score_job(JobExtract(**base, required_skills=["Python"], nice_to_have_skills=["Rust"]), profile, matcher, TODAY)
    assert a.breakdown["technical_skills"].points == 15.0  # 1 of 2 required
    assert b.breakdown["technical_skills"].points == 22.5  # 1 / (1 + 1/3)


def test_alias_synonyms():
    assert norm("M365") == norm("Office 365") == norm("Microsoft 365")


async def test_embedding_matches_non_alias_synonyms(matcher):
    hit, sim = await matcher.match_skill("PC troubleshooting", ["Windows desktop", "Python"])
    assert hit == "Windows desktop" and sim >= 0.65
    miss, _ = await matcher.match_skill("Forklift", ["Python", "SQL"])
    assert miss is None


async def test_scoring_does_not_call_llm(profile, matcher):
    await score_job(EXTRACT["junior_dev"], profile, matcher, TODAY)  # FakeAI.chat raises if called


def test_location_ranking(profile):
    from app.matching.scoring import _location
    def pts(loc):
        return _location(JobExtract(title="x", location=loc, employment_type="part_time"), profile, TODAY).points
    assert pts("Exampleton VIC") == pts("Melbourne CBD") > pts("Box Hill, Melbourne") > pts("Sydney NSW") > pts("Canberra ACT") > pts("Perth WA")


def test_citizenship_or_pr_fails_even_for_graduate_programs(profile):
    for req in (["permanent_residency"], ["citizenship"], ["security_clearance"]):
        for et in ("full_time", "graduate_program"):
            j = JobExtract(title="x", work_rights_requirements=req, employment_type=et)
            g = evaluate_gate(j, profile, TODAY)
            assert g.status == "FAIL", (req, et)


def test_mandatory_unheld_certification_fails(profile):
    from app.matching.schema import Qualification
    j = JobExtract(title="x", qualifications=[Qualification(text="Cisco CCNA", mandatory=True)])
    assert evaluate_gate(j, profile, TODAY).status == "FAIL"


def test_part_time_over_limit_flagged(profile):
    j = JobExtract(title="x", employment_type="part_time", hours_per_week_max=30)
    assert any("exceeds" in f for f in evaluate_gate(j, profile, TODAY).flags)


def test_duplicates_and_screening(profile):
    assert find_duplicate("Example Studios Pty Ltd", profile.applied_employers) == "Example Studios"
    assert find_duplicate("Westside Logistics", profile.applied_employers) is None
    assert screening_answer("Do you have unrestricted work rights in Australia?") == "NO"
    assert screening_answer("Favourite colour?") is None


def test_expired_deadline_flagged(profile):
    j = JobExtract(title="x", deadline=date(2026, 1, 1))
    assert any("deadline passed" in f for f in evaluate_gate(j, profile, TODAY).flags)


def test_studying_requirement_is_met_and_not_flagged(profile):
    from app.matching.schema import Qualification
    j = JobExtract(title="x", qualifications=[Qualification(text="Currently studying a degree", mandatory=True)])
    assert evaluate_gate(j, profile, TODAY).status == "PASS"


from app.matching.gate import GateResult, pay_flags
from app.matching.schema import Salary
from app.matching.scoring import MatchResult


def _mr(title, score, status):
    return MatchResult(title, score, GateResult(status, [], ["f"] if status == "REVIEW" else []), {})


def test_review_job_never_outranks_pass_job():
    out = rank([_mr("review-99", 99.0, "REVIEW"), _mr("pass-40", 40.0, "PASS"), _mr("pass-60", 60.0, "PASS"),
                _mr("review-80", 80.0, "REVIEW"), _mr("fail-100", 100.0, "FAIL")])
    assert [r.job_title for r in out] == ["pass-60", "pass-40", "review-99", "review-80"]


async def test_primary_role_scores_above_secondary_role(profile, matcher):
    base = dict(employment_type="part_time", location="Exampleton")
    prim = await score_job(JobExtract(title="Service Desk Analyst", **base), profile, matcher, TODAY)
    sec = await score_job(JobExtract(title="Junior Web Developer", **base), profile, matcher, TODAY)
    none = await score_job(JobExtract(title="Warehouse Operator", **base), profile, matcher, TODAY)
    assert prim.breakdown["role_relevance"].points == 15.0
    assert sec.breakdown["role_relevance"].points == 9.0  # 60%
    assert none.breakdown["role_relevance"].points == 0.0


def test_target_role_lists_match_handover(profile):
    assert len(profile.primary_roles) >= 13 and "application support" in profile.primary_roles
    assert "soc trainee" in profile.secondary_roles and "ui/ux designer" in profile.secondary_roles


def test_pay_flags_minimum_wage_unpaid_and_casual_loading():
    def f(**kw):
        return pay_flags(JobExtract(title="x", **kw))
    assert any("unpaid" in x for x in f(unpaid=True))
    assert any("below the national minimum" in x for x in f(salary=Salary(min=22, period="hour"), employment_type="part_time"))
    assert f(salary=Salary(min=26.44, period="hour"), employment_type="part_time") == []
    assert any("casual minimum" in x for x in f(salary=Salary(min=28, period="hour"), employment_type="casual"))  # < 33.05
    assert f(salary=Salary(min=34, period="hour"), employment_type="casual") == []
    assert any("below" in x for x in f(salary=Salary(min=45000, period="year"), employment_type="full_time"))  # ~$22.77/hr
    assert f(salary=Salary(min=65000, period="year"), employment_type="full_time") == []
    assert f() == []  # no salary stated: nothing to flag, scored neutral


def test_pay_flag_makes_gate_review(profile):
    j = JobExtract(title="x", employment_type="part_time", hours_per_week_max=20, salary=Salary(min=20, period="hour"))
    g = evaluate_gate(j, profile, TODAY)
    assert g.status == "REVIEW" and any("minimum" in x for x in g.flags)


# ---- "students welcome / currently studying" is a positive signal and must never deduct points
from app.matching.schema import Qualification

STUDENT_LINES = [
    "Students in an IT degree are welcome",
    "Currently studying a degree",
    "Final year students welcome",
    "Undergraduate students encouraged to apply",
    "Currently enrolled in a Bachelor of Information Technology",
    "Open to students pursuing a tertiary qualification",
]


def _job(*quals):
    return JobExtract(title="IT Support Officer", employment_type="part_time", location="Werribee VIC", hours_per_week_max=20,
                      qualifications=list(quals))


@pytest.mark.parametrize("line", STUDENT_LINES)
@pytest.mark.parametrize("mandatory", [False, True])
async def test_student_friendly_line_never_deducts(profile, matcher, line, mandatory):
    base = await score_job(_job(), profile, matcher, TODAY)
    with_line = await score_job(_job(Qualification(text=line, mandatory=mandatory)), profile, matcher, TODAY)
    q = with_line.breakdown["qualification"]
    assert q.points == q.max_points == 15 and with_line.score == base.score  # identical to an ad without the line
    assert any("student-friendly" in n for n in q.notes) and q.missing == []
    assert with_line.gate.status == base.gate.status and with_line.gate.flags == base.gate.flags


async def test_student_line_does_not_dilute_or_replace_a_real_requirement(profile, matcher):
    real = Qualification(text="Bachelor's degree in IT", mandatory=False)
    alone = await score_job(_job(real), profile, matcher, TODAY)
    mixed = await score_job(_job(real, Qualification(text="Students welcome")), profile, matcher, TODAY)
    assert alone.breakdown["qualification"].points == 10.5  # in-progress degree = 70% credit, as before
    assert mixed.breakdown["qualification"].points == 10.5  # the student line neither adds nor removes anything
    assert any("student-friendly" in n for n in mixed.breakdown["qualification"].notes)


async def test_real_degree_requirement_still_scored_and_unmet_certificate_still_deducts(profile, matcher):
    assert (await score_job(_job(Qualification(text="Bachelor's degree in IT")), profile, matcher, TODAY)).breakdown["qualification"].points == 10.5
    cert = await score_job(_job(Qualification(text="Cisco CCNA certification")), profile, matcher, TODAY)
    assert cert.breakdown["qualification"].points == 0.0


async def test_the_original_quirk_ad_now_scores_full_qualification_points(profile, matcher):
    # the sample ad that lost 4.5 points: "Students in an IT degree are welcome" was extracted as a qualification
    job = EXTRACT["it_support_pt"].model_copy(update={"qualifications": [Qualification(text="Students in an IT degree are welcome")]})
    r = await score_job(job, profile, matcher, TODAY)
    assert r.breakdown["qualification"].points == 15.0


def test_mandatory_student_line_is_not_a_gate_barrier_for_a_student_but_is_for_a_non_student(profile):
    j = JobExtract(title="x", qualifications=[Qualification(text="Final year students welcome", mandatory=True)])
    assert evaluate_gate(j, profile, TODAY).status == "PASS"
    profile.degree_status = "none"
    assert evaluate_gate(j, profile, TODAY).status == "FAIL"
