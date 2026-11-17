from app.apply.formgate import check_form
from tests.test_apply_assist import ats, files, run  # noqa: F401  (fixtures)


def test_a_stated_citizenship_or_clearance_requirement_fails_the_form():
    for text in ("Applicants must be Australian citizens.", "You must hold Australian citizenship to apply.", "Australian citizenship is required.",
                 "Baseline security clearance required for this role.", "You need to be a permanent resident.", "Citizens only."):
        g = check_form(text, [])
        assert g.status == "FAIL", text
        assert g.reasons and "cannot meet" in g.reasons[0]


def test_work_rights_questions_are_reviewed_not_failed():
    g = check_form("Tell us about yourself", ["First name", "Do you have unrestricted work rights in Australia?", "Will you require visa sponsorship?"])
    assert g.status == "REVIEW" and len(g.questions) == 2


def test_an_ordinary_form_passes():
    assert check_form("Service Desk Analyst. We value diversity. Students welcome.", ["First name", "Email", "Resume"]).status == "PASS"


def test_live_form_demanding_citizenship_stops_before_any_field_is_touched(ats, files, tmp_path):
    s, seen = run(ats, "/citizen/apply", files, tmp_path)
    assert s["state"] == "blocked" and "stopped before filling anything" in s["message"] and s["gate"]["status"] == "FAIL"
    assert s["filled"] == [] and s["screenshot"] is None and ats.hits == []


def test_live_form_with_work_rights_question_is_filled_truthfully_and_flagged_for_review(ats, files, tmp_path):
    s, seen = run(ats, "/workable/apply", files, tmp_path)
    assert s["state"] == "ready" and s["gate"]["status"] == "REVIEW" and any("citizen" in q.lower() for q in s["gate"]["questions"])
    assert "wr=no" in seen["checked"] and ats.submissions() == []
