"""Autofill in your own browser: the 30-minute code, what it can and cannot open, the answers it returns, and the extension's own code."""
import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from app.core.config import settings
from app.core.security import create_access_token, create_scoped_token, decode_access_token
from app.main import app
from tests.sim_sites import Sim
from tests.test_apply_email import api, engine, make_app  # noqa: F401  (fixtures and helper)

EXT = Path(__file__).resolve().parents[2] / "extension"


def C(idx, type_="text", label="", options=(), **kw):
    return {"idx": idx, "type": type_, "label": label, "name": "", "id": "", "required": False, "options": list(options), **kw}


def radio(*texts):
    return [{"text": t, "ref": f"0-{i}"} for i, t in enumerate(texts)]


def code_for(api, aid):
    r = api.post(f"/v1/applications/{aid}/autofill-token")
    assert r.status_code == 200, r.text
    return r.json()


def auth(code):
    return {"Authorization": "Bearer " + code["code"].split(":", 2)[2]}


# ───────────── the code ─────────────
def test_the_code_is_for_one_application_and_expires(api):
    aid = make_app(api)
    j = code_for(api, aid)
    assert re.fullmatch(r"jh1:\d{2,5}:\S+", j["code"]) and j["expires_in_minutes"] == 30 and j["extension_path"].endswith("extension")
    anon = TestClient(app)
    assert anon.post(f"/v1/applications/{aid}/autofill-token").status_code == 401
    assert api.post(f"/v1/applications/{make_app(api, docs=False, employer='NoDocs Co', title='Other role')}/autofill-token").json()["detail"]["code"] == "no_documents"
    old = create_scoped_token("autofill", aid, minutes=-1)
    assert anon.post("/autofill/plan", json={"url": "https://x.example/a", "controls": []}, headers={"Authorization": "Bearer " + old}).status_code == 401


def test_the_code_opens_nothing_else_and_a_login_cannot_be_used_as_a_code(api):
    aid = make_app(api)
    tok = auth(code_for(api, aid))
    anon = TestClient(app)
    for method, path in (("get", "/v1/listings"), ("get", "/v1/applications"), ("get", "/v1/system"), ("get", f"/v1/applications/{aid}"), ("post", f"/v1/applications/{aid}/assist")):
        assert getattr(anon, method)(path, headers=tok).status_code == 401, path  # a scoped code is not a login
    login = {"Authorization": "Bearer " + create_access_token("admin")}
    assert anon.post("/autofill/plan", json={"url": "https://x.example/a", "controls": []}, headers=login).status_code == 401  # and a login is not a code
    assert decode_access_token(tok["Authorization"].split()[1]) is None
    assert anon.post("/autofill/plan", json={"url": "https://x.example/a", "controls": []}).status_code == 401


# ───────────── the answers ─────────────
def plan(api, aid, controls, url="https://careers.acme.example/apply", text=""):
    return api.post("/autofill/plan", json={"url": url, "controls": controls, "page_text": text}, headers=auth(code_for(api, aid)))


def test_the_plan_fills_what_your_profile_supports_and_leaves_the_rest(api):
    aid = make_app(api)
    controls = [C(0, "text", "First name", required=True), C(1, "email", "Email address"), C(2, "tel", "Mobile phone", intl=True), C(3, "file", "Resume", required=True),
                C(4, "file", "Cover letter"), C(5, "file", "Academic transcript"), C(6, "textarea", "Expected salary"), C(7, "select", "Gender", ["Select", "Male", "Female"]),
                C(8, "checkbox", "I agree to the privacy policy"), C(9, "radio", "Will you now or in future require visa sponsorship?", radio("Yes", "No")),
                C(10, "text", "Why do you want to work here?")]
    r = plan(api, aid, controls)
    assert r.status_code == 200, r.text
    j = r.json()
    acts = {a["idx"]: a for a in j["actions"]}
    needs = {n["idx"]: n for n in j["needs"]}
    assert acts[0]["value"] == "Alex" and acts[1]["kind"] == "text" and "@" in acts[1]["value"] and acts[2]["value"].startswith("+61 ")
    assert acts[3]["files"] == ["resume"] and acts[4]["files"] == ["cover_letter"] and 5 in needs and "attach" in needs[5]["reason"].lower()
    assert 6 in needs and 7 in needs and 8 in needs and 9 in needs and "never inferred" in needs[9]["reason"]  # salary, demographic, consent, visa: yours
    assert j["gate"]["status"] in ("PASS", "REVIEW")


def test_confirmed_answers_apply_and_are_marked_for_review(api):
    aid = make_app(api)
    controls = [C(0, "radio", "How soon can you start?", radio("Right away", "I need 2 weeks")), C(1, "textarea", "What is your expected hourly rate?"),
                C(2, "radio", "Which statement best describes your right to work in Australia?", radio("Citizen/PR", "Full working rights", "Student visa"))]
    j = plan(api, aid, controls).json()
    acts = {a["idx"]: a for a in j["actions"]}
    assert acts[0]["value"] == "Right away" and acts[0]["option_ref"] == "0-0" and acts[1]["value"] == "$35 - $40 per hour" and acts[2]["value"] == "Student visa"
    assert {r["category"] for r in j["review"]} >= {"WORK_RIGHTS", "SALARY"} or len(j["review"]) >= 2
    assert all(a["review"] for a in j["actions"] if a["idx"] in (1, 2))


def test_job_boards_and_private_addresses_are_refused(api):
    aid = make_app(api)
    for url in ("https://www.seek.com.au/job/1/apply", "https://au.indeed.com/viewjob?jk=0123456789abcdef", "https://au.jora.com/job/x", "http://192.168.1.5/apply"):
        r = plan(api, aid, [C(0, "text", "First name")], url=url)
        assert r.status_code == 403, url


def test_a_form_that_demands_citizenship_stops_everything(api):
    aid = make_app(api)
    j = plan(api, aid, [C(0, "text", "First name")], text="Applicants must be Australian citizens.").json()
    assert j["stop"] and j["actions"] == [] and j["gate"]["status"] == "FAIL"


def test_documents_are_served_only_for_this_application_and_only_these_two(api):
    aid = make_app(api)
    tok = auth(code_for(api, aid))
    anon = TestClient(app)
    r = anon.get("/autofill/file/resume", headers=tok)
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"
    assert anon.get("/autofill/file/cover_letter", headers=tok).status_code == 200
    for bad in ("../../etc/passwd", "filled_form.png", "resume.pdf", "transcript"):
        assert anon.get(f"/autofill/file/{bad}", headers=tok).status_code == 404
    assert anon.get("/autofill/file/resume").status_code == 401


def test_oversized_or_malformed_requests_are_rejected(api):
    aid = make_app(api)
    tok = auth(code_for(api, aid))
    anon = TestClient(app)
    assert anon.post("/autofill/plan", json={"url": "https://x.example/a", "controls": [C(i) for i in range(301)]}, headers=tok).status_code == 422
    assert anon.post("/autofill/plan", json={"controls": []}, headers=tok).status_code == 422


# ───────────── the extension's own files ─────────────
def test_the_extension_is_minimal_and_in_step_with_the_assistants_scanner():
    m = json.loads((EXT / "manifest.json").read_text())
    assert m["manifest_version"] == 3 and set(m["permissions"]) == {"activeTab", "scripting", "storage"} and m["host_permissions"] == ["http://127.0.0.1/*"]
    assert "content_scripts" not in m and "<all_urls>" not in json.dumps(m)  # it runs only when you press Fill on a page you chose
    from app.apply.engine import SCAN_JS
    assert (EXT / "scan.js").read_text().endswith("(" + SCAN_JS + ");\n"), "extension/scan.js is out of date: run scripts/build_extension.py"
    src = (EXT / "apply.js").read_text() + (EXT / "popup.js").read_text() + (EXT / "background.js").read_text()
    assert ".submit(" not in src and "requestSubmit" not in src and not re.search(r"\.click\(\)", src.replace("r.click()", ""))  # the only click it ever makes is on an answer radio
    assert "g-recaptcha" not in src and "turnstile" not in src.lower() and "webdriver" not in src


# ───────────── the extension's fill logic, run for real in a page (the bridge talks to the real API) ─────────────
@pytest.fixture
def sim(monkeypatch):
    s = Sim()
    monkeypatch.setattr(settings, "assist_test_hosts", "127.0.0.1")
    yield s
    s.close()


def fill_in_a_real_page(api, aid, url, tmp_path):
    tok = auth(code_for(api, aid))
    anon = TestClient(app)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_context().new_page()
        calls = {"plan": 0, "file": 0}

        def jh_plan(body):
            calls["plan"] += 1
            return json.dumps(anon.post("/autofill/plan", content=body, headers={**tok, "Content-Type": "application/json"}).json())

        def jh_file(kind):
            calls["file"] += 1
            r = anon.get(f"/autofill/file/{kind}", headers=tok)
            import base64
            return json.dumps({"name": "Resume.pdf" if kind == "resume" else "Cover Letter.pdf", "b64": base64.b64encode(r.content).decode()})
        pg.expose_function("jhPlan", jh_plan)
        pg.expose_function("jhFile", jh_file)
        pg.goto(url)
        pg.add_script_tag(path=str(EXT / "scan.js"))
        pg.evaluate("""window.__jhBridge = { plan: (b) => window.jhPlan(JSON.stringify(b)).then(JSON.parse), file: (k) => window.jhFile(k).then(JSON.parse) }""")
        pg.add_script_tag(path=str(EXT / "apply.js"))
        result = pg.evaluate("window.__jhRun()")
        dom = pg.evaluate("""() => Object.fromEntries([...document.querySelectorAll('input,textarea,select')].filter(e => e.id).map(e => [e.id, e.type === 'file' ? [...e.files].map(f => f.name) : e.type === 'checkbox' ? e.checked : e.value]))""")
        checked = pg.evaluate("[...document.querySelectorAll('input[type=radio]:checked')].map(e => e.name + '=' + e.value)")
        flagged = pg.evaluate("[...document.querySelectorAll('[style*=\"outline\"]')].length")
        banner = pg.evaluate("!!document.body.innerText.match(/press Submit yourself/)")
        b.close()
    return result, dom, checked, flagged, banner, calls, anon


def test_the_extension_fills_the_real_pasted_questions_in_a_page_and_never_submits(api, sim, tmp_path):
    aid = make_app(api)
    result, dom, checked, flagged, banner, calls, _ = fill_in_a_real_page(api, aid, sim.base + "/t25/apply", tmp_path)
    assert result["filled"] >= 12 and dom["firstname"] == "Alex" and dom["email"] and dom["resume"] == ["Resume.pdf"] and dom["cl"] == ["Cover Letter.pdf"]
    assert dom["cc"] == "Australia (+61)" and re.fullmatch(r"\d{3} \d{3} \d{3}", dom["phone"]) and dom["ctry"] == "Australia"
    assert {"start=Right away", "rtw=Student visa", "tech=Yes", "covid=Fully vaccinated", "eng=Fluent", "police=Yes", "cs=Yes", "ph=Yes"} <= set(checked)
    assert dom["rate"] == "$35 - $40 per hour" and dom["rate2"] == "35" and dom["yrs"] == "0.5"
    assert banner and result["review"] >= 3 and calls["file"] == 2 and sim.submissions() == [] and sim.visited("/captcha-click") is False


def test_the_extension_rescans_after_an_answer_reveals_more_fields(api, sim, tmp_path):
    aid = make_app(api)
    result, dom, checked, *_ = fill_in_a_real_page(api, aid, sim.base + "/t8/apply", tmp_path)
    assert dom["firstname"] == "Alex" and "lic=yes" in checked and dom["city"] and sim.submissions() == []


def test_a_form_in_a_page_that_stops_the_run_is_reported_not_filled(api, sim, tmp_path):
    aid = make_app(api)
    result, dom, checked, flagged, banner, calls, _ = fill_in_a_real_page(api, aid, sim.base + "/t11/apply", tmp_path)
    assert result is None or "filled" not in result or result["filled"] == 0  # a challenge page has no form: nothing to fill, nothing clicked
    assert not sim.visited("/captcha-click")
