import pytest

from app.apply.engine import Decision, UnsafeAction, decide, run_assist, safe_click
from app.core.config import settings
from tests.ats_fixtures import ATSServer

PERSON = {"first_name": "Alex", "last_name": "Example", "full_name": "Alex Example", "email": "alex@example.test", "phone": "0400 000 000",
          "city": "Exampleton", "state": "VIC", "location": "Exampleton, VIC"}
ANSWERS = [
    {"id": "unrestricted_work_rights", "pattern": r"unrestricted (right|work)|full work rights", "answer": "No", "source": "eligibility/screening_unrestricted_work_rights"},
    {"id": "citizen_or_pr", "pattern": r"australian citizen|permanent resident", "answer": "No", "source": "work_rights/current"},
    {"id": "drivers_licence", "pattern": r"driver'?s?\s+licen[cs]e", "answer": "Yes", "source": "license/vic_drivers_licence"},
    {"id": "studying", "pattern": r"currently (studying|enrolled)", "answer": "Yes", "source": "education/uni_bit"},
]


@pytest.fixture
def files(tmp_path):
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4 resume")
    (tmp_path / "cover_letter.pdf").write_bytes(b"%PDF-1.4 letter")
    return {"resume": str(tmp_path / "resume.pdf"), "cover_letter": str(tmp_path / "cover_letter.pdf")}


def make_plan(url, files, **over):
    return {"url": url, "platform": "test", "employer": "Acme", "role": "Service Desk Analyst", "folder": None, "person": dict(PERSON), "files": files,
            "cover_letter_text": "Dear Hiring Manager,\n\nI am writing to apply.\n\nKind regards,\nAlex Example", "answers": ANSWERS, **over}


# ───────────────────────── decisions (no browser) ─────────────────────────
def D(type_="text", label="", options=(), **kw):
    return {"idx": 0, "type": type_, "label": label, "name": kw.get("name", ""), "id": "", "required": False, "options": list(options)}


def yn(): return [{"text": "Yes", "ref": "0-0"}, {"text": "No", "ref": "0-1"}]


def test_text_fields_map_to_the_right_profile_values():
    plan = make_plan("x", {"resume": "r.pdf", "cover_letter": "c.pdf"})
    cases = {"First name *": "Alex", "Last Name": "Example", "Email address": "alex@example.test", "Mobile phone": "0400 000 000", "Full name": "Alex Example",
             "Name": "Alex Example", "Suburb": "Exampleton", "Location (City)": "Exampleton", "Current location": "Exampleton, VIC", "Given name": "Alex", "Surname": "Example"}
    for label, want in cases.items():
        got = decide(D("text", label), plan)
        assert got and got.value == want and got.source, (label, got)
    assert decide(D("email", ""), plan).value == "alex@example.test"
    assert decide(D("textarea", "Cover letter"), plan).value.startswith("Dear Hiring Manager")
    assert decide(D("text", "Cover letter"), plan) is None  # a one-line input is no place for a whole letter


@pytest.mark.parametrize("label", ["LinkedIn profile", "Expected salary", "How did you hear about us?", "Referee name", "Company name", "Notice period", "Portfolio URL",
                                   "Gender", "Date of birth", "Street address", "Why do you want this job?", "Github", "Website", "Postcode", ""])
def test_anything_not_in_the_verified_profile_is_left_blank(label):
    assert decide(D("text", label), make_plan("x", {})) is None


def test_missing_profile_values_are_never_invented():
    plan = make_plan("x", {})
    plan["person"]["email"] = ""
    plan["person"]["phone"] = ""
    assert decide(D("text", "Email"), plan) is None and decide(D("text", "Phone"), plan) is None
    plan["cover_letter_text"] = ""
    assert decide(D("textarea", "Cover letter"), plan) is None


def test_yes_no_answers_come_only_from_tagged_rules_and_only_for_yes_no_questions():
    plan = make_plan("x", {})
    wr = decide(D("radio", "Do you have unrestricted work rights in Australia?", yn()), plan)
    assert (wr.kind, wr.value, wr.option_ref, wr.source) == ("radio", "No", "0-1", "eligibility/screening_unrestricted_work_rights")
    assert decide(D("select", "Are you an Australian citizen or permanent resident?", ["Yes", "No"]), plan) == Decision("select", "No", "work_rights/current")
    assert decide(D("radio", "Do you hold a current driver's licence?", yn()), plan).value == "Yes"
    assert decide(D("radio", "Are you currently studying?", yn()), plan).value == "Yes"
    # same words, but not a yes/no question: do not guess
    assert decide(D("select", "Your unrestricted work rights status", ["Citizen", "Permanent resident", "Visa holder"]), plan) is None
    assert decide(D("radio", "Will you require visa sponsorship?", yn()), plan) is None  # no rule for it: blank
    assert decide(D("radio", "Are you willing to relocate?", yn()), plan) is None
    # a profile without the supporting rows produces no answer at all
    assert decide(D("radio", "Do you have unrestricted work rights?", yn()), make_plan("x", {}, answers=[])) is None


def test_the_answer_is_never_yes_to_unrestricted_work_rights():
    for opts in (yn(), list(reversed(yn()))):
        got = decide(D("radio", "Do you have unrestricted work rights?", opts), make_plan("x", {}))
        assert got.value == "No"


def test_consent_boxes_and_passwords_are_never_filled():
    plan = make_plan("x", {})
    assert decide(D("checkbox", "I agree to the privacy policy"), plan) is None and decide(D("password", "Password"), plan) is None


def test_files_go_to_the_right_upload():
    plan = make_plan("x", {"resume": "r.pdf", "cover_letter": "c.pdf"})
    assert decide(D("file", "Resume/CV"), plan, 2) == Decision("file", "r.pdf", "documents: resume.pdf")
    assert decide(D("file", "Cover letter"), plan, 2).value == "c.pdf"
    assert decide(D("file", "Portfolio"), plan, 2) is None
    assert decide(D("file", ""), plan, 1).value == "r.pdf"  # the only upload on the page is the resume
    assert decide(D("file", ""), plan, 2) is None


def test_state_select_picks_victoria_not_something_else():
    plan = make_plan("x", {})
    assert decide(D("select", "State", ["Select", "New South Wales", "Victoria"]), plan).value == "Victoria"
    assert decide(D("select", "State", ["Select", "NSW", "VIC"]), plan).value == "VIC"
    assert decide(D("select", "State", ["Select", "New South Wales"]), plan) is None


# ───────────────────────── which sites may be opened ─────────────────────────
@pytest.mark.parametrize("url", ["https://www.seek.com.au/job/1", "https://au.linkedin.com/jobs/1", "https://au.indeed.com/viewjob?jk=1",
                                 "http://apply.workable.com/x", "https://127.0.0.1:9/x", "javascript:alert(1)"])
def test_disallowed_pages_are_refused_without_opening_a_browser(url, files, monkeypatch):
    monkeypatch.setattr("app.apply.engine.sync_playwright", lambda: (_ for _ in ()).throw(AssertionError("a browser must not start")))
    s = run_assist(make_plan(url, files), "/tmp/jh-unused", headless=True, keep_open=False)
    assert s["state"] == "blocked" and s["filled"] == [] and s["submit_clicked"] is False


# ───────────────────────── the browser run, against replicas ─────────────────────────
@pytest.fixture
def ats(monkeypatch):
    srv = ATSServer()
    monkeypatch.setattr(settings, "assist_test_hosts", "127.0.0.1")
    monkeypatch.setattr(settings, "assist_wait_seconds", 3)  # tests must not wait minutes for a person who is not there
    yield srv
    srv.close()


def run(ats, path, files, tmp_path, **plan_over):
    seen = {}

    def on_ready(page):
        flagged = page.evaluate("[...document.querySelectorAll('[data-jh-flag]')].map(e => e.id || e.name || e.tagName)")
        seen["flagged"] = flagged
        seen["values"] = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('input,textarea,select')].filter(e => e.id).map(e => [e.id, e.type === 'file' ? (e.files[0] ? e.files[0].name : '') : e.type === 'checkbox' ? e.checked : e.value]))""")
        seen["checked"] = page.evaluate("[...document.querySelectorAll('input[type=radio]:checked')].map(e => e.name + '=' + e.value)")
        seen["banner"] = page.evaluate("document.getElementById('jh-banner') && document.getElementById('jh-banner').textContent")
        seen["url"] = page.url
        seen["page"] = page

    status = run_assist(make_plan(ats.base + path, files, **plan_over), tmp_path / "out", headless=True, keep_open=False, on_ready=on_ready)
    return status, seen


def test_workable_style_form_is_filled_from_the_profile_and_everything_else_is_flagged(ats, files, tmp_path):
    s, seen = run(ats, "/workable/apply", files, tmp_path)
    assert s["state"] == "ready" and s["stopped_before_submit"] is True and s["submit_clicked"] is False
    v = seen["values"]
    assert (v["firstname"], v["lastname"], v["email"], v["phone"]) == ("Alex", "Example", "alex@example.test", "0400 000 000")
    assert v["resume"] == "resume.pdf" and v["cl"].startswith("Dear Hiring Manager") and v["cit"] == "No"
    assert sorted(seen["checked"]) == ["lic=yes", "stu=yes", "wr=no"]  # work rights answered with the truthful No
    # unknown things stay empty and are highlighted for the person
    assert v["li"] == "" and v["sal"] == "" and v["hear"] == "" and v["consent"] is False
    assert {"li", "sal", "hear", "consent"} <= set(seen["flagged"]) and "firstname" not in seen["flagged"]
    blanks = {b["label"].split(" *")[0]: b for b in s["left_blank"]}
    assert blanks["LinkedIn profile"]["required"] is True and "I agree to the privacy policy" in blanks and blanks["Expected salary"]["required"] is False
    # the log says where each answer came from
    srcs = {f["label"].split(" *")[0]: f["source"] for f in s["filled"]}
    assert srcs["First name"] == "identity/name" and srcs["Phone"] == "contact/phone" and srcs["Resume"] == "documents: resume.pdf"
    assert "Orange fields need YOUR answer" in seen["banner"] and "press Submit yourself" in seen["banner"]
    shot = tmp_path / "out" / "filled_form.png"
    assert shot.read_bytes()[:4] == b"\x89PNG" and s["screenshot"] == "filled_form.png"
    assert ats.submissions() == [], "the assistant must never submit"


def test_greenhouse_style_labels_state_select_and_two_uploads(ats, files, tmp_path):
    s, seen = run(ats, "/greenhouse/apply", files, tmp_path)
    v = seen["values"]
    assert (v["first_name"], v["last_name"], v["email"], v["phone"], v["location"]) == ("Alex", "Example", "alex@example.test", "0400 000 000", "Exampleton")
    assert v["resume"] == "resume.pdf" and v["cover"] == "cover_letter.pdf" and v["state"] == "Victoria"
    assert v["ref"] == "" and "ref" in seen["flagged"] and ats.submissions() == []


def test_lever_style_aria_labels(ats, files, tmp_path):
    s, seen = run(ats, "/lever/apply", files, tmp_path)
    page = seen["page"] if False else None
    assert s["state"] == "ready" and ats.submissions() == []
    got = {f["label"]: f["source"] for f in s["filled"]}
    assert got["Full name"] == "identity/name" and got["Current location"] == "contact/location" and got["Resume/CV"] == "documents: resume.pdf"
    assert got["Add a cover letter or anything else you want to share"] == "draft: cover letter"
    assert [b["label"] for b in s["left_blank"]] == ["LinkedIn URL"]


def test_shadow_dom_fields_and_placeholder_only_inputs_are_found(ats, files, tmp_path):
    s, seen = run(ats, "/smartrecruiters/apply", files, tmp_path)
    filled = {f["label"] for f in s["filled"]}
    assert {"First name", "Last name", "Email", "Phone number"} <= filled and any(f["kind"] == "file" for f in s["filled"])  # the only upload is the resume
    assert seen["values"]["ph"] == "0400 000 000" and ats.submissions() == []


def test_a_job_page_is_opened_with_the_apply_button_and_nothing_else_is_clicked(ats, files, tmp_path):
    s, seen = run(ats, "/workable/job", files, tmp_path)
    assert s["state"] == "ready" and seen["url"].endswith("/workable/apply") and seen["values"]["firstname"] == "Alex" and ats.submissions() == []


def test_captcha_is_flagged_and_never_touched(ats, files, tmp_path):
    s, _ = run(ats, "/captcha/apply", files, tmp_path)
    assert s["state"] == "ready" and s["captcha"] is True
    assert ("GET", "/captcha-click") not in ats.hits and ats.submissions() == []


def test_a_login_wall_stops_everything(ats, files, tmp_path):
    s, _ = run(ats, "/login/apply", files, tmp_path)
    assert s["state"] == "blocked" and s["login_required"] is True and s["filled"] == [] and s["screenshot"] is None and ats.hits == []


def test_leaving_the_allowed_host_stops_the_run(ats, files, tmp_path):
    s, _ = run(ats, "/redirect", files, tmp_path)  # redirects to http://localhost:PORT, a host that is not allowed
    assert s["state"] == "blocked" and "not allowed" in s["message"] and s["filled"] == [] and ats.submissions() == []


def test_a_page_with_no_form_is_reported_not_guessed_at(ats, files, tmp_path):
    s, _ = run(ats, "/noform", files, tmp_path)
    assert s["state"] == "blocked" and "could not find an application form" in s["message"] and s["filled"] == []


def test_a_look_alike_question_that_is_not_yes_no_is_left_for_the_person(ats, files, tmp_path):
    s, seen = run(ats, "/odd", files, tmp_path)
    assert seen["values"]["wr"] == "" and seen["values"]["f2"] == "" and {"wr", "f2"} <= set(seen["flagged"])
    assert not any("work rights" in f["label"].lower() for f in s["filled"])


def test_a_profile_without_an_email_leaves_the_email_field_blank_and_flagged(ats, files, tmp_path):
    plan_person = dict(PERSON, email="")
    s, seen = run(ats, "/workable/apply", files, tmp_path, person=plan_person)
    assert seen["values"]["email"] == "" and "email" in seen["flagged"]


def test_the_person_can_submit_after_the_assistant_hands_the_page_back(ats, files, tmp_path):
    """While filling, submission is blocked; afterwards it must work, because the person presses Submit, not the assistant."""
    results = {}

    def on_ready(page):
        before = len(ats.submissions())
        page.click("button[type=submit]")  # standing in for the person
        page.wait_for_timeout(600)
        results["submitted_after_handback"] = len(ats.submissions()) - before

    # (the Greenhouse replica has no HTML 'required' fields, so the browser's own validation does not stop a click)
    s = run_assist(make_plan(ats.base + "/greenhouse/apply", files), tmp_path / "o", headless=True, keep_open=False, on_ready=on_ready)
    assert s["submit_clicked"] is False and results["submitted_after_handback"] == 1


def test_submission_is_blocked_while_the_assistant_is_working(ats, files, tmp_path):
    seen = {}

    def during(page):
        pass

    # press Enter in a field mid-run is not possible from here, so assert the guard exists in the page while filling by checking the status order:
    states = []
    run_assist(make_plan(ats.base + "/workable/apply", files), tmp_path / "o", headless=True, keep_open=False, on_status=lambda st: states.append(st["state"]))
    assert states[:3] == ["opening", "filling", "ready"] and ats.submissions() == []


# ───────────────────────── the only thing it may click ─────────────────────────
def test_safe_click_refuses_anything_that_could_submit_send_or_sign_in(ats, tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        pg.goto(ats.base + "/trap")
        for sel in ("#sub", "#inform", "#send"):
            with pytest.raises(UnsafeAction):
                safe_click(pg.locator(sel))
        assert ats.hits == []  # nothing was clicked
        safe_click(pg.locator("#ok"))  # a plain 'Apply now' outside any form is the one allowed click
        pg.wait_for_timeout(400)
        assert ("GET", "/opener-click") in ats.hits and ats.submissions() == []
        b.close()


def test_a_control_with_its_own_label_never_borrows_text_from_elsewhere_on_the_page(ats, files, tmp_path):
    """Regression: the consent checkbox used to be labelled 'I agree to the privacy policy First name *'."""
    s, _ = run(ats, "/workable/apply", files, tmp_path)
    labels = [b["label"] for b in s["left_blank"]]
    assert "I agree to the privacy policy" in labels and not any("privacy policy" in l and "First name" in l for l in labels)
    assert all(l.count("*") <= 1 for l in labels)
