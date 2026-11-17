"""Your confirmed answers, address, +61 and attachments on forms: rules (no browser) and the pasted questions end to end (simulated site)."""
import pytest

from app.apply.answers import SemanticMatcher, build_qa, pick_option
from app.apply.engine import decide
from app.apply.plan import phone_forms
from app.core.config import settings
from tests.sim_sites import Sim
from tests.test_apply_journey import files, journey, sim  # noqa: F401  (fixtures and helpers)

STD = {"start": "Right away", "notice": "None", "covid_vaccination": "Fully vaccinated", "english": "Fluent", "police_check": "Yes", "public_holidays": "Yes",
       "customer_service_experience": "Yes", "technical_support_experience": "Yes", "hourly_rate_min": 35, "hourly_rate_max": 40, "right_to_work_label": "Student visa",
       "it_support_years": 0.5}
PERSON = {"first_name": "Alex", "last_name": "Example", "full_name": "Alex Example", "email": "alex@example.test", "phone": "0400 000 000", "city": "Exampleton", "state": "VIC",
          "location": "Exampleton, VIC", "street": "1 Example Street", "postcode": "3000", "country": "Australia", "address_full": "1 Example Street, Exampleton VIC 3000",
          **phone_forms("0400 000 000")}


def plan(**kw):
    return {"person": dict(PERSON), "files": {"resume": "/r.pdf", "cover_letter": "/c.pdf"}, "answers": [], "qa": build_qa(STD, "student"), "cover_letter_text": "Dear Hiring Manager", **kw}


def D(type_="text", label="", options=(), **kw):
    return {"idx": 0, "type": type_, "label": label, "name": "", "id": "", "required": False, "options": list(options), **kw}


def radio(*texts):
    return [{"text": t, "ref": f"0-{i}"} for i, t in enumerate(texts)]


def test_phone_is_given_with_61_where_a_country_code_is_wanted():
    assert phone_forms("0400 000 000") == {"phone_intl": "+61 400 000 000", "phone_national": "400 000 000"}
    assert phone_forms("+61 400 000 000")["phone_intl"] == "+61 400 000 000" and phone_forms("not a number") == {"phone_intl": "", "phone_national": ""}
    assert decide(D("select", "Phone country code", ["New Zealand (+64)", "Australia (+61)"]), plan()).value == "Australia (+61)"
    assert decide(D("tel", "Mobile phone"), plan()).value == "0400 000 000"  # a plain box: as you wrote it
    assert decide(D("tel", "Mobile phone", intl=True), plan()).value == "+61 400 000 000"  # a box that wants an international number
    assert decide(D("tel", "Mobile phone"), plan(), 1, {"cc_set": True}).value == "400 000 000"  # beside a +61 box: no leading 0


def test_address_pieces_go_to_the_right_boxes_and_unit_numbers_are_never_guessed():
    p = plan()
    got = {l: (decide(D("text", l), p) or type("x", (), {"value": None})).value for l in ["Street address", "Address line 1", "Postcode", "Residential address", "Suburb", "Address line 2", "Unit number"]}
    assert got["Street address"] == got["Address line 1"] == "1 Example Street" and got["Postcode"] == "3000" and got["Residential address"] == "1 Example Street, Exampleton VIC 3000"
    assert got["Suburb"] == "Exampleton" and got["Address line 2"] is None and got["Unit number"] is None
    assert decide(D("select", "Country", ["New Zealand", "Australia"]), p).value == "Australia"
    assert decide(D("select", "State", ["Select", "Victoria"]), p).value == "Victoria"


def test_the_pasted_multiple_choice_questions_are_answered_from_your_confirmed_answers():
    p = plan()
    cases = [
        ("How soon can you start?", ["Right away", "I need 2 weeks", "I need 3 weeks", "I need 4 weeks", "Other"], "Right away"),
        ("Which of the following statements best describes your right to work in Australia?", ["Citizen/PR", "Full working rights", "Partial working rights", "Working holiday visa", "Student visa", "None of the above"], "Student visa"),
        ("Do you have technical support experience?", ["Yes", "No"], "Yes"),
        ("Which of the following statements best describes your Covid-19 vaccination status?", ["Fully vaccinated", "Partially vaccinated", "Not vaccinated"], "Fully vaccinated"),
        ("How would you rate your English language skills?", ["Basic", "Good", "Very good", "Fluent", "Native or bilingual"], "Fluent"),
        ("Do you have a current Police Check (National Police Certificate) for employment?", ["Yes", "No"], "Yes"),
        ("Do you have customer service experience?", ["Yes", "No"], "Yes"),
        ("Are you available to work public holidays?", ["Yes", "No"], "Yes"),
    ]
    for q, opts, want in cases:
        d = decide(D("radio", q, radio(*opts)), p)
        assert d and d.value == want, (q, d)
    d = decide(D("select", "How much notice are you required to give your current employer?", ["1 week", "2 weeks", "None", "More"]), p)
    assert d.value == "None"


def test_text_boxes_get_the_rate_range_notice_and_years_and_the_risky_ones_are_marked_for_review():
    p = plan()
    assert decide(D("text", "What is the base hourly rate are you seeking for this role?"), p).value == "$35 - $40 per hour"
    assert decide(D("number", "What's your expected hourly rate?"), p).value == "35"
    assert decide(D("text", "How much notice are you required to give your current employer?"), p).value.startswith("None")
    assert decide(D("number", "How many years' experience do you have as a Service Centre Analyst?"), p).value == "0.5"
    assert decide(D("text", "Which of the following statements best describes your right to work in Australia?"), p).value == "Student visa"
    for q in ("What is the base hourly rate are you seeking for this role?", "How many years' experience do you have as a Service Centre Analyst?"):
        assert decide(D("text", q), p).review is True


def test_a_choice_is_never_guessed_when_no_option_says_it():
    p = plan()
    assert decide(D("radio", "How soon can you start?", radio("In 6 months", "Never")), p) is None
    assert decide(D("radio", "Which of the following statements best describes your right to work in Australia?", radio("Citizen/PR", "Full working rights", "None of the above")), p) is None
    assert decide(D("radio", "Covid-19 vaccination status", radio("Partially vaccinated", "Not vaccinated")), p) is None
    # nothing confirmed -> nothing answered
    assert decide(D("radio", "How soon can you start?", radio("Right away", "Other")), plan(qa=[])) is None
    assert decide(D("text", "Expected salary"), plan(qa=[])) is None and decide(D("text", "Date of birth"), p) is None


def test_other_documents_are_not_attached_but_a_multi_file_box_gets_both_documents():
    p = plan()
    d = decide(D("file", "Attach your documents", multiple=True), p)
    assert d.value == ["/r.pdf", "/c.pdf"]
    assert decide(D("file", "Academic transcript", multiple=True), p) is None and decide(D("file", "Passport or ID"), p) is None
    assert decide(D("file", "Cover letter"), p).value == "/c.pdf" and decide(D("file", "Resume / Cover letter"), p).value == "/r.pdf"


def test_a_question_worded_differently_is_matched_by_meaning_but_only_to_your_confirmed_answers():
    vocab = ["start", "work", "notice", "hourly", "rate", "police", "vaccin", "english", "holiday", "customer", "support", "years", "right", "salary"]

    def embed(texts):  # a stand-in embedder: bag of words over a small vocabulary
        return [[float(w in t.lower()) + 0.001 for w in vocab] for t in texts]
    m = SemanticMatcher(embed, build_qa(STD, "student"))
    assert m.match("When would you be able to commence work and start?")["id"] == "start"
    assert m.match("What hourly rate do you expect, in AUD?")["id"] == "hourly_rate"
    assert m.match("Describe your favourite colour") is None
    assert SemanticMatcher(None, build_qa(STD)).match("How soon can you start?") is None  # no embedder (Ollama down): exact rules only

    def broken(texts):
        raise ConnectionError
    assert SemanticMatcher(broken, build_qa(STD)).match("How soon can you start?") is None


def test_option_picking_never_picks_a_negated_health_answer():
    e = {x["id"]: x for x in build_qa(STD)}["covid"]
    assert pick_option(["Not fully vaccinated", "Unvaccinated"], e) is None and pick_option(["Fully vaccinated (3 doses)"], e) == "Fully vaccinated (3 doses)"


# ───────────────────────── end to end on the simulated form with the pasted questions ─────────────────────────
def test_25_the_pasted_form_is_filled_with_address_plus61_and_every_confirmed_answer(sim, files, tmp_path):
    s = journey(sim, "/t25/apply", files, tmp_path, person=PERSON, qa=build_qa(STD, "student"))
    v, checked = s["_seen"]["values"], s["_seen"]["checked"]
    assert (v["street"], v["sub"], v["pc"], v["ctry"], v["cc"]) == ("1 Example Street", "Exampleton", "3000", "Australia", "Australia (+61)")
    assert v["phone"] == "400 000 000"  # national form beside the +61 box
    assert v["rate"] == "$35 - $40 per hour" and v["rate2"] == "35" and v["notice"].startswith("None") and v["yrs"] == "0.5"
    assert {"start=Right away", "rtw=Student visa", "tech=Yes", "covid=Fully vaccinated", "eng=Fluent", "police=Yes", "cs=Yes", "ph=Yes"} <= set(checked)
    assert s["state"] == "ready" and sim.submissions() == []
    reviewed = {r["label"] for r in s["needs_review"]}
    assert any("right to work" in r.lower() for r in reviewed) and any("police" in r.lower() for r in reviewed) and any("hourly" in r.lower() for r in reviewed)
    assert not [b for b in s["left_blank"] if b["required"]]  # nothing required was left for you
    assert (s["_out"] / "assist_audit.jsonl").read_text().count("1 Example Street") == 0  # the audit never holds your details


def test_26_an_international_phone_box_gets_plus61(sim, files, tmp_path):
    s = journey(sim, "/t26/apply", files, tmp_path, person=PERSON, qa=[])
    assert s["_seen"]["values"]["phone"] == "+61 400 000 000"


def test_27_a_multi_file_box_gets_the_resume_and_the_cover_letter(sim, files, tmp_path):
    s = journey(sim, "/t27/apply", files, tmp_path, person=PERSON, qa=[])
    assert [u["ok"] for u in s["uploads"]] == [True] and "resume.pdf" in s["uploads"][0]["file"] and "cover_letter.pdf" in s["uploads"][0]["file"]


def test_28_radio_groups_whose_question_sits_on_the_wrapper_are_read_and_answered(sim, files, tmp_path):
    s = journey(sim, "/t28/apply", files, tmp_path, person=PERSON, qa=build_qa(STD, "student"))
    assert {"QA1=Right away", "QA2=YES"} <= set(s["_seen"]["checked"]), s["_seen"]["checked"]
    assert s["_seen"]["values"]["adr"] == "1 Example Street, Exampleton VIC 3000"
    assert not any(b["label"] in ("Right away", "YES") for b in s["left_blank"])  # the group is labelled by its question, not by its first option


def test_after_the_form_is_filled_the_assistant_stops_touching_the_page(sim, files, tmp_path, monkeypatch):
    """Hand-over: no polling, no snapshots, no clicks. It only notices the window closing."""
    import app.apply.workflow as wf
    calls = {"snap": 0, "after": 0}
    orig = wf.Run.snapshot

    def counting(self):
        calls["snap"] += 1
        return orig(self)
    monkeypatch.setattr(wf.Run, "snapshot", counting)
    seen = {}

    def person_closes_the_window(page):
        seen["before"] = calls["snap"]
        page.context.browser.close()
    from app.apply.engine import run_assist
    from tests.test_apply_assist import make_plan
    s = run_assist(make_plan(sim.base + "/t1/apply", files, person=PERSON, qa=[]), tmp_path / "o", headless=True, keep_open=True, on_ready=person_closes_the_window)
    assert calls["snap"] == seen["before"], "the assistant touched the page after handing it over"
    assert s["state"] == "closed" and s["submit_clicked"] is False and sim.submissions() == []
    assert any(e["to"] == "APPLICATION_READY_FOR_APPROVAL" for e in s["timeline"])
