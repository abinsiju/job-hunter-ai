"""The application journey against simulated employer sites (tests/sim_sites.py). Nothing here touches a real website or submits anything real."""
import json
import re

import pytest

from app.apply.engine import run_assist
from app.apply.page_intel import classify, find_nav, pick_apply_action, pick_method
from app.apply.states import IllegalTransition, Machine, S, allowed
from app.apply.uploads import judge_upload, validate_file
from app.core.config import settings
from tests.sim_sites import Sim
from tests.test_apply_assist import make_plan


@pytest.fixture
def sim(monkeypatch):
    s = Sim()
    monkeypatch.setattr(settings, "assist_test_hosts", "127.0.0.1,localhost")
    monkeypatch.setattr(settings, "assist_wait_seconds", 4)
    yield s
    s.close()


@pytest.fixture
def files(tmp_path):
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4 resume")
    (tmp_path / "cover_letter.pdf").write_bytes(b"%PDF-1.4 letter")
    return {"resume": str(tmp_path / "resume.pdf"), "cover_letter": str(tmp_path / "cover_letter.pdf")}


def journey(sim, path, files, tmp_path, ready=None, **over):
    seen = {}

    def on_ready(page):
        seen["values"] = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('input,textarea,select')].filter(e => e.id).map(e =>
            [e.id, e.type === 'file' ? (e.files[0] ? e.files[0].name : '') : e.type === 'checkbox' ? e.checked : e.value]))""")
        seen["checked"] = page.evaluate("[...document.querySelectorAll('input[type=radio]:checked')].map(e => e.name + '=' + e.value)")
        seen["flagged"] = page.evaluate("[...document.querySelectorAll('[data-jh-flag]')].map(e => e.id || e.name || e.tagName)")
        seen["url"] = page.url
        seen["frames"] = [f.url for f in page.frames]
        if ready:
            ready(page)

    out = tmp_path / "out"
    s = run_assist(make_plan(sim.base + path, files, **over), out, headless=True, keep_open=False, on_ready=on_ready)
    s["_out"], s["_seen"] = out, seen
    return s


def states(s):
    return [e["to"] for e in s["timeline"]]


def filled(s):
    return {f["label"] for f in s["filled"]}


# ───────────────────────── pure logic ─────────────────────────
def test_state_machine_demands_evidence_and_refuses_illegal_jumps():
    m = Machine()
    with pytest.raises(IllegalTransition):
        m.move(S.JOB_DETAILS_VISIBLE, [])  # no evidence: no change
    assert m.move(S.JOB_DETAILS_VISIBLE, ["h1 visible"]) and m.state == S.JOB_DETAILS_VISIBLE
    with pytest.raises(IllegalTransition):
        m.move(S.APPLICATION_COMPLETED, ["a click happened"])  # completion only follows a confirmed submission
    assert not allowed(S.JOB_DETAILS_VISIBLE, S.APPLICATION_COMPLETED) and allowed(S.SUBMISSION_CONFIRMED, S.APPLICATION_COMPLETED)
    assert not allowed(S.APPLICATION_COMPLETED, S.APPLICATION_FORM_DETECTED)
    assert m.timeline()[-1]["evidence"] == ["h1 visible"]


def snap(**kw):
    base = {"url": "https://x.test/a", "title": "", "heading": "Service Desk Analyst", "text": "Service Desk Analyst " * 40, "textLength": 800, "buttons": [], "dialogs": [], "iframes": [],
            "loading": 0, "fieldCount": 0, "passwordFields": 0, "fileFields": 0, "step": None, "captcha": 0, "invalid": []}
    return {**base, **kw}


def btn(text, i=0, **kw):
    return {"i": i, "text": text, "tag": "button", "type": "", "href": "", "target": "", "disabled": False, "inForm": False, "formHasFields": False, "inDialog": False,
            "inMain": True, "inChrome": False, "inList": False, "context": "", **kw}


def test_classification_covers_the_main_stages():
    cases = [
        (snap(), S.JOB_DETAILS_VISIBLE), (snap(buttons=[btn("Apply now")]), S.APPLICATION_ACTION_REQUIRED),
        (snap(loading=2, textLength=20, text="Loading"), S.PAGE_LOADING), (snap(passwordFields=1, fieldCount=1, text="Sign in to apply"), S.LOGIN_REQUIRED),
        (snap(passwordFields=1, fieldCount=2, text="Create an account to apply"), S.ACCOUNT_CREATION_REQUIRED),
        (snap(fieldCount=2, text="Please verify your email. Enter the code we sent."), S.EMAIL_VERIFICATION_REQUIRED),
        (snap(fieldCount=5), S.APPLICATION_FORM_DETECTED), (snap(fieldCount=5, step={"n": 2, "of": 4}), S.APPLICATION_STEP_ACTIVE),
        (snap(fieldCount=1, fileFields=1), S.DOCUMENT_UPLOAD_REQUIRED),
        (snap(captcha=1, fieldCount=0, text="check"), S.CAPTCHA_OR_SECURITY_CHALLENGE), (snap(text="Your session has expired. Sign in again."), S.SESSION_EXPIRED),
        (snap(text="You have already applied for this job."), S.USER_INTERVENTION_REQUIRED),
        (snap(text="Thank you for applying. Your application has been submitted."), S.SUBMISSION_CONFIRMED),
        (snap(dialogs=[{"text": "Apply for this job", "fields": 3, "buttons": []}]), S.APPLICATION_MODAL_OPEN),
        (snap(dialogs=[{"text": "Apply for this job", "fields": 0, "buttons": ["Apply manually", "Apply with LinkedIn"]}]), S.APPLICATION_METHOD_SELECTION),
        (snap(text="Before you begin: what you'll need. " * 5, buttons=[btn("Start application")]), S.APPLICATION_INTRODUCTION),
    ]
    for sn, want in cases:
        assert classify(sn).state == want, (want, classify(sn))
    assert classify(snap(dialogs=[{"text": "We use cookies. Accept all", "fields": 0, "buttons": ["Accept all"]}])).state == S.JOB_DETAILS_VISIBLE  # a cookie banner is not the application


def test_the_right_apply_button_is_chosen_and_ambiguity_is_never_guessed():
    good = [btn("Apply for funding", 0, inChrome=True), btn("Apply", 1, inList=True), btn("Apply", 2, inList=True), btn("Apply now", 3)]
    assert pick_apply_action(snap(buttons=good)).button["i"] == 3
    two = [btn("Apply now", 0, href="https://a.test/1"), btn("Apply on company website", 1, href="https://b.test/2")]
    r = pick_apply_action(snap(buttons=two))
    assert r.kind == "ambiguous" and len(r.candidates) == 2
    assert pick_apply_action(snap(buttons=[btn("Save job"), btn("Sign in to apply"), btn("Apply with LinkedIn")])).kind == "none"
    assert pick_apply_action(snap(buttons=[btn("Apply now", inForm=True, formHasFields=True, type="submit")])).kind == "none"  # a submit button is never an opener


def test_method_choice_avoids_accounts_and_third_party_sign_in():
    dlg = [btn("Apply with LinkedIn", 0, inDialog=True), btn("Sign in", 1, inDialog=True), btn("Apply manually", 2, inDialog=True)]
    assert pick_method(snap(dialogs=[{"text": "", "fields": 0, "buttons": []}], buttons=dlg)).button["i"] == 2
    assert pick_method(snap(buttons=[btn("Apply with LinkedIn"), btn("Sign in with Google")])).kind == "none"


def test_next_is_only_pressed_in_a_wizard_and_never_on_the_last_step():
    b = [btn("Next", 0), btn("Submit application", 1)]
    n = find_nav(snap(buttons=b, step={"n": 1, "of": 3}))
    assert n.next["i"] == 0 and n.final["i"] == 1
    assert find_nav(snap(buttons=b, step={"n": 3, "of": 3})).next is None
    nn = find_nav(snap(buttons=[btn("Continue")]))
    assert nn.next is None and "final submit" in nn.why  # no step indicator: a Continue could be the final submit


def test_upload_judgement_needs_the_file_and_no_error(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    assert validate_file(str(f)) is None and "does not exist" in validate_file(str(tmp_path / "no.pdf"))
    (tmp_path / "a.exe").write_bytes(b"x")
    assert "not accepted" in validate_file(str(tmp_path / "a.exe"))
    assert judge_upload({"files": ["a.pdf"], "text": "a.pdf uploaded"}, str(f))[0]
    assert not judge_upload({"files": [], "text": ""}, str(f))[0]
    assert not judge_upload({"files": ["a.pdf"], "text": "Upload failed: server error"}, str(f))[0]


# ───────────────────────── the journey, scenario by scenario ─────────────────────────
def test_01_job_page_without_a_form_finds_apply_then_the_form(sim, files, tmp_path):
    s = journey(sim, "/t1/job", files, tmp_path)
    assert states(s)[:2] == ["APPLICATION_ACTION_REQUIRED", "APPLICATION_FORM_DETECTED"]
    assert s["timeline"][0]["evidence"] == ["a visible Apply control"]
    assert {"First name *", "Last name *", "Email *", "Resume *"} <= filled(s) and s["_seen"]["values"]["firstname"] == "Alex" and s["state"] == "ready"
    assert sim.submissions() == []


def test_02_modal_application_is_detected(sim, files, tmp_path):
    s = journey(sim, "/t2/job", files, tmp_path)
    assert "APPLICATION_MODAL_OPEN" in states(s) and s["_seen"]["values"]["email"] == "alex@example.test" and sim.submissions() == []


def test_03_new_tab_is_followed_and_tied_to_the_job_page(sim, files, tmp_path):
    s = journey(sim, "/t3/job", files, tmp_path)
    assert "NEW_TAB_OPENED" in states(s) and s["_seen"]["url"].endswith("/t3/apply")
    assert s["pages"][0].endswith("/t3/job") and s["pages"][-1].endswith("/t3/apply")
    assert any(e["event"] == "new_tab" and e["opener"].endswith("/t3/job") for e in map(json.loads, (s["_out"] / "assist_audit.jsonl").read_text().splitlines()))


def test_04_external_ats_on_another_host(sim, files, tmp_path):
    s = journey(sim, "/t4/job", files, tmp_path)
    assert "EXTERNAL_APPLICATION_REDIRECT" in states(s) and "localhost" in s["_seen"]["url"] and s["_seen"]["values"]["firstname"] == "Alex"


def test_05_login_required_pauses_and_never_touches_the_login(sim, files, tmp_path):
    s = journey(sim, "/t5/job", files, tmp_path)
    assert s["state"] == "blocked" and s["login_required"] is True and "LOGIN_REQUIRED" in states(s) and s["filled"] == []
    assert not sim.submissions()


def test_05b_it_carries_on_after_the_person_signs_in(sim, files, tmp_path):
    s = journey(sim, "/t5b/login", files, tmp_path, watch_seconds=0)
    assert "LOGIN_REQUIRED" in states(s) and s["state"] == "ready" and s["_seen"]["values"]["firstname"] == "Alex"


def test_06_a_slow_form_is_waited_for_not_given_up_on(sim, files, tmp_path):
    s = journey(sim, "/t6/apply", files, tmp_path)
    assert s["state"] == "ready" and s["_seen"]["values"]["lastname"] == "Example"


def test_07_multi_step_wizard_each_step_is_filled_and_the_final_submit_is_left_alone(sim, files, tmp_path):
    s = journey(sim, "/t7/apply", files, tmp_path)
    assert s["state"] == "ready" and s["stage"] == "APPLICATION_READY_FOR_APPROVAL" and s["stopped_before_submit"] is True
    steps = {f["step"] for f in s["filled"]}
    assert steps == {1, 2, 3} and {"Resume *"} <= filled(s)
    assert "wr=no" in s["_seen"]["checked"]  # work rights answered truthfully
    reasons = {b["label"]: b["reason"] for b in s["left_blank"]}
    assert any("salary" in r.lower() for r in reasons.values()) and any("declaration" in r.lower() for r in reasons.values())
    assert sim.submissions() == [] and s["submit_clicked"] is False
    assert [u["ok"] for u in s["uploads"]] == [True, True]


def test_08_an_answer_that_reveals_more_fields_causes_a_rescan(sim, files, tmp_path):
    plan = {"answers": [{"id": "lic", "pattern": r"driver'?s?\s+licen[cs]e", "answer": "Yes", "source": "license/vic"}]}
    s = journey(sim, "/t8/apply", files, tmp_path, **plan)
    assert "CONDITIONAL_FIELDS_PENDING" in states(s) and s["_seen"]["values"]["city"] == "Exampleton" and "lic=yes" in s["_seen"]["checked"]


def test_09_a_missing_required_answer_is_reported_and_the_step_is_not_advanced(sim, files, tmp_path):
    s = journey(sim, "/t9w/apply", files, tmp_path)
    assert s["stage"] == "USER_INTERVENTION_REQUIRED" and s["missing_required"] >= 1
    assert any("Referee" in b["label"] and b["required"] for b in s["left_blank"]) and "li" in s["_seen"]["flagged"]
    assert s["_seen"]["url"].endswith("/t9w/apply")  # did not press Next
    s2 = journey(sim, "/t9/apply", files, tmp_path / "b")
    assert any("LinkedIn" in b["label"] for b in s2["left_blank"]) and s2["missing_required"] >= 1


def test_10_a_failed_upload_is_never_reported_as_success(sim, files, tmp_path):
    s = journey(sim, "/t10/apply", files, tmp_path)
    assert s["uploads"] and s["uploads"][0]["ok"] is False and "Upload failed" in s["uploads"][0]["detail"]
    assert "Resume *" not in filled(s) and any("upload not confirmed" in b["reason"] for b in s["left_blank"])


def test_11_a_captcha_challenge_stops_automation_and_is_never_clicked(sim, files, tmp_path):
    s = journey(sim, "/t11/apply", files, tmp_path)
    assert s["state"] == "blocked" and "CAPTCHA_OR_SECURITY_CHALLENGE" in states(s) and s["captcha"] is True and s["filled"] == []
    assert not sim.visited("/captcha-click")


def test_12_an_expired_session_waits_then_resumes(sim, files, tmp_path):
    s = journey(sim, "/t12/apply", files, tmp_path)
    assert "SESSION_EXPIRED" in states(s) and s["state"] == "ready" and s["_seen"]["values"]["email"] == "alex@example.test"
    assert any(e["event"] == "waiting_for_person" for e in map(json.loads, (s["_out"] / "assist_audit.jsonl").read_text().splitlines()))


def test_13_an_already_applied_page_stops_everything(sim, files, tmp_path):
    s = journey(sim, "/t13/apply", files, tmp_path)
    assert s["state"] == "blocked" and "already applied" in s["message"] and s["filled"] == [] and not sim.submissions()


def test_14_the_final_submit_needs_the_person_and_is_never_pressed(sim, files, tmp_path):
    s = journey(sim, "/t7/apply", files, tmp_path)
    assert s["stage"] == "APPLICATION_READY_FOR_APPROVAL" and "press Submit yourself" in s["message"] and sim.submissions() == []


def test_15_a_confirmed_submission_is_recorded_as_evidence(sim, files, tmp_path):
    def person_submits(page):  # the PERSON presses Submit
        page.click("button[type=submit]")
    s = journey(sim, "/t15/apply", files, tmp_path, ready=person_submits, watch_seconds=12)
    assert s["state"] == "completed" and s["stage"] == "APPLICATION_COMPLETED"
    assert "submitted" in s["confirmation"]["text"].lower() and s["confirmation"]["url"].endswith("/t15/done")
    assert (s["_out"] / "confirmation.png").is_file() and [h for h in sim.hits if h[0] == "POST"] == [("POST", "/t15/done")]
    assert s["submit_clicked"] is False  # the app itself never clicked it


def test_16_instructions_on_a_page_are_ignored(sim, files, tmp_path):
    s = journey(sim, "/t16/job", files, tmp_path)
    assert s["state"] == "ready" and any("instructions" in n for n in s["notes"]) and s["_seen"]["values"]["firstname"] == "Alex"
    assert not sim.visited("/evil") and sim.submissions() == []
    audit = (s["_out"] / "assist_audit.jsonl").read_text()
    assert "prompt_injection_ignored" in audit and "evil@attacker" not in audit


def test_17_the_correct_apply_button_among_many(sim, files, tmp_path):
    s = journey(sim, "/t17/job", files, tmp_path)
    assert s["_seen"]["url"].endswith("/t1/apply") and not sim.visited("/evil")


def test_17b_equally_likely_apply_buttons_are_not_guessed(sim, files, tmp_path):
    s = journey(sim, "/t17b/job", files, tmp_path)
    assert s["state"] == "blocked" and len(s["candidates"]) == 2 and not sim.visited("/t17b/a") and not sim.visited("/t17b/b")


def test_18_an_application_in_an_iframe_is_filled(sim, files, tmp_path):
    s = journey(sim, "/t18/apply", files, tmp_path)
    assert s["state"] == "ready" and "First name *" in filled(s) and any(f.endswith("/t18/frame") for f in s["_seen"]["frames"])


def test_19_an_autocomplete_suggestion_is_selected_and_verified(sim, files, tmp_path):
    s = journey(sim, "/t19/apply", files, tmp_path)
    assert s["_seen"]["values"]["city"] == "Exampleton, VIC 3000" and "Suburb" in filled(s)


def test_20_leaving_the_page_during_autofill_pauses_and_recovers(sim, files, tmp_path):
    s = journey(sim, "/t20/apply", files, tmp_path)
    assert s["_seen"]["url"].endswith("/t20/other") and s["state"] == "ready" and s["_seen"]["values"]["email"] == "alex@example.test"
    assert "page_changed_during_fill" in (s["_out"] / "assist_audit.jsonl").read_text()


def test_21_an_inaccessible_custom_dropdown_is_reported_not_faked(sim, files, tmp_path):
    s = journey(sim, "/t21/apply", files, tmp_path)
    assert any(b["type"] == "custom-dropdown" and "Preferred location" in b["label"] and "cannot operate" in b["reason"] for b in s["left_blank"])


def test_22_repeated_sections_are_tracked_separately_and_existing_answers_survive(sim, files, tmp_path):
    s = journey(sim, "/t22/apply", files, tmp_path)
    labels = [b["label"] for b in s["left_blank"]]
    assert "Employer (#2)" in labels and "Job title" in labels and "Job title (#2)" in labels
    assert s["_seen"]["values"]["e1"] == "Acme Pty Ltd"  # what was already typed is never overwritten


def test_23_a_conditional_dropdown_is_filled_once_it_appears(sim, files, tmp_path):
    plan = {"answers": []}
    s = journey(sim, "/t23/apply", files, tmp_path, **plan)
    assert "Which country?" in {b["label"] for b in s["left_blank"]} and s["_seen"]["values"].get("state", "") == ""  # no verified answer for country: we do not pick one to reveal more questions


def test_24_visa_legal_and_motivation_questions_follow_their_rules(sim, files, tmp_path):
    s = journey(sim, "/t24/apply", files, tmp_path)
    left = {b["label"]: b for b in s["left_blank"]}
    assert "visa" in left["Will you require visa sponsorship? *"]["reason"] and "declaration" in left["Do you have a criminal conviction?"]["reason"]
    assert s["_seen"]["values"]["mo"].startswith("Dear Hiring Manager")  # motivation: the verified cover letter, flagged for review
    assert any(r["category"] == "MOTIVATION" for r in s["needs_review"])


def test_every_state_change_in_a_run_has_evidence(sim, files, tmp_path):
    s = journey(sim, "/t7/apply", files, tmp_path)
    assert s["timeline"] and all(e["evidence"] for e in s["timeline"])
    lines = [json.loads(x) for x in (s["_out"] / "assist_audit.jsonl").read_text().splitlines()]
    assert lines[0]["event"] == "start" and lines[-1]["event"] == "end"
    assert "alex@example.test" not in (s["_out"] / "assist_audit.jsonl").read_text()  # the audit holds labels and states, never your details
    assert (s["_out"] / "trace.zip").is_file()
