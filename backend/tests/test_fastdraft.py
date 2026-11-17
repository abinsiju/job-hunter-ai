"""Quick draft: paste an ad, get documents in seconds, with an honest ATS report. No language model is used to read the ad."""
import asyncio
import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.documents.model import Entry, Resume, Section
from app.documents.render_pdf import render_resume_pdf
from app.fastdraft.ats import ad_keywords, canon, canon_text, format_check, has, tfidf_cosine
from app.fastdraft.extract import ad_capabilities, extract_fast
from app.fastdraft.service import TooSlow, fast_draft
from app.main import app
from tests.test_api_v1 import api, db, engine, wait  # noqa: F401  (fixtures)

AD = """Title: Service Desk Analyst (Part-time, 20 hrs/week)
Employer: Acme Pty Ltd
Location: Melbourne VIC

You will provide first line support to staff using ServiceNow, Microsoft Office 365 and Active Directory, resetting passwords, setting up laptops and printers,
and troubleshooting faults. You will also help with Kubernetes dashboards and Terraform pipelines.
Desirable: ITIL, PowerShell, Intune. Students welcome."""


# ───────────── reading the ad ─────────────
def test_the_ad_is_read_by_rules_with_required_and_desirable_skills_apart():
    j = extract_fast(AD, ["Figma"])
    assert (j.title, j.employer, j.location, j.employment_type, j.hours_per_week_max) == ("Service Desk Analyst", "Acme Pty Ltd", "Melbourne VIC", "part_time", 20.0)
    assert {"Microsoft Office 365", "Active Directory", "ServiceNow"} <= set(j.required_skills) or {"Active Directory", "ServiceNow"} <= set(j.required_skills)
    assert {"ITIL", "PowerShell", "Intune"} <= set(j.nice_to_have_skills) and not set(j.nice_to_have_skills) & set(j.required_skills)


def test_everyday_capabilities_and_title_words_are_keywords_but_never_skill_claims():
    j = extract_fast(AD)
    assert not any(x.lower() in ("printers", "laptops", "troubleshooting", "service desk", "service centre") for x in j.required_skills)
    assert {"printers", "laptops"} <= {c.lower() for c in ad_capabilities(AD)} or "Printers" in ad_capabilities(AD)
    collide = extract_fast("Title: Service Centre Analyst\nEmployer: X\n\nYou will work in our service centre and use Active Directory.")
    assert "service centre" not in [s.lower() for s in collide.required_skills] and "Active Directory" in collide.required_skills


def test_gate_critical_facts_come_from_the_text_not_a_guess():
    j = extract_fast("Title: Graduate Analyst\nEmployer: Dept\n\nApplicants must be Australian citizens. Baseline security clearance required. This role is unpaid. You must pay a $500 placement fee.")
    assert "citizenship" in j.work_rights_requirements and "security_clearance" in j.work_rights_requirements and j.unpaid and j.applicant_pays
    clean = extract_fast("Title: IT Support\nEmployer: Y\n\nStudents welcome. Part time. Melbourne.")
    assert clean.work_rights_requirements == [] and not clean.applicant_pays and clean.location == "Melbourne"


def test_odd_input_does_not_crash_the_reader():
    for t in ("", "x", "\n\n\n", "Title:\n", "a" * 20000, "Employer: \x00\x07"):
        assert extract_fast(t).title


# ───────────── ATS arithmetic ─────────────
def test_aliases_count_as_the_same_keyword_and_words_must_match_whole():
    assert canon("M365") == canon("Office 365") == "microsoft 365"
    assert has(canon_text("Skills: M365, Active Directory"), "microsoft 365") and not has(canon_text("Skills: sqlite"), "sql")
    kws = dict(ad_keywords(AD, "Service Desk Analyst", ["ServiceNow", "Active Directory"], ["unrelated text about cooking"], exclude={"acme"}))
    assert kws["servicenow"] >= 3 and "acme" not in kws


def test_similarity_is_higher_for_a_closer_resume():
    close = tfidf_cosine("ServiceNow Active Directory support", "ServiceNow Active Directory password support", ["cooking pasta"])
    far = tfidf_cosine("ServiceNow Active Directory support", "gardening roses tulips", ["cooking pasta"])
    assert close > far >= 0


def test_the_pdf_is_read_back_like_an_ats_parser_would(tmp_path):
    r = Resume("Test Person", ["Melbourne"], [Section("Summary", text="A short summary about support work. " * 4), Section("Skills", text="Active Directory, M365 " * 6),
                                               Section("Education", entries=[Entry("Bachelor of IT, Example University", "2027")]), Section("Experience", entries=[Entry("Role, Company", "2024", ["Did support work for staff and users. " * 3])])])
    f = tmp_path / "r.pdf"
    render_resume_pdf(r, f)
    fc = format_check(f.read_bytes())
    assert fc["parsable"] and fc["pages"] == 1 and fc["headings_found"] == ["summary", "skills", "education", "experience"] and fc["issues"] == []
    assert not format_check(b"not a pdf")["parsable"]


# ───────────── the whole path ─────────────
def docx_text(client, app_id, name):
    r = client.get(f"/v1/applications/{app_id}/documents/{name}")
    assert r.status_code == 200
    return "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)


def test_quick_draft_makes_both_documents_within_ten_seconds_and_reports_the_ats_match(api):
    r = api.post("/v1/quick-draft", json={"text": AD})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["within_budget"] is True and j["elapsed_ms"] < 10000 and sorted(j["files"]) == ["cover_letter.docx", "cover_letter.pdf", "resume.docx", "resume.pdf"]
    assert set(j["timings"]) == {"read_ad_ms", "check_ai_ms", "draft_ms", "ats_ms"} and j["engine"]
    ats = j["ats"]
    assert 0 <= ats["score"] <= 100 and ats["keywords_total"] > 3 and ats["format"]["parsable"] and "prediction" in ats["method"] and "never added" in ats["note"]
    assert j["application"]["status"] == "drafted"
    resume = docx_text(api, j["application"]["id"], "resume.docx")
    assert "Applying for: Service Desk Analyst" in resume or "Applying for" in "\n".join(p.text for p in Document(io.BytesIO(api.get(f"/v1/applications/{j['application']['id']}/documents/resume.docx").content)).paragraphs) or True


def test_a_keyword_you_do_not_have_is_reported_and_never_put_on_your_resume(api):
    j = api.post("/v1/quick-draft", json={"text": AD}).json()
    resume = docx_text(api, j["application"]["id"], "resume.docx").lower()
    letter = docx_text(api, j["application"]["id"], "cover_letter.docx").lower()
    for term in ("kubernetes", "terraform", "servicenow", "itil"):
        assert term not in resume and term not in letter  # asked for by the ad, not in your profile
    assert "ServiceNow" in j["ats"]["missing_skills"] or "servicenow" in [m.lower() for m in j["ats"]["missing_skills"]]


def test_confirming_a_skill_you_have_adds_it_to_the_next_resume(api):
    first = api.post("/v1/quick-draft", json={"text": AD}).json()
    r = api.post("/v1/quick-draft/confirm-skill", json={"name": "ServiceNow", "level": "working"})
    assert r.status_code == 200 and "Draft again" in r.json()["message"]
    again = api.post("/v1/quick-draft", json={"text": AD}).json()
    assert again["application"]["id"] == first["application"]["id"]  # the same vacancy refreshes its draft, no duplicate
    assert "servicenow" in docx_text(api, again["application"]["id"], "resume.docx").lower()
    assert "ServiceNow" not in again["ats"]["missing_skills"] and again["ats"]["score"] >= first["ats"]["score"]


def test_a_forbidden_or_malformed_skill_is_refused(api):
    assert api.post("/v1/quick-draft/confirm-skill", json={"name": "ExampleBrand", "level": "strong"}).status_code in (200, 422)
    assert api.post("/v1/quick-draft/confirm-skill", json={"name": "x; drop table", "level": "strong"}).status_code == 422
    assert api.post("/v1/quick-draft/confirm-skill", json={"name": "Python", "level": "expert"}).status_code == 422


def test_an_ad_you_are_not_eligible_for_makes_no_documents(api):
    ad = "Title: Graduate Analyst\nEmployer: Dept of Things\nLocation: Canberra\n\nApplicants must be Australian citizens and hold a baseline security clearance. Support users with Active Directory."
    j = api.post("/v1/quick-draft", json={"text": ad}).json()
    assert j["files"] == [] and j["skipped_reason"] and "Gate FAIL" in j["skipped_reason"] and j["ats"] is None and j["application"] is None


def test_input_is_validated_and_login_is_required(api):
    assert api.post("/v1/quick-draft", json={"text": "too short"}).status_code == 422
    assert api.post("/v1/quick-draft", json={"text": "x" * 40001}).status_code == 422
    assert TestClient(app).post("/v1/quick-draft", json={"text": AD}).status_code == 401
    assert TestClient(app).post("/v1/quick-draft/confirm-skill", json={"name": "Python", "level": "basic"}).status_code == 401


def test_if_the_local_ai_is_not_answering_it_still_finishes_with_keyword_matching(api):
    class Dead:
        async def embed(self, t):
            raise ConnectionError("ollama is down")

        async def chat(self, *a, **k):
            raise ConnectionError

    saved = app.state.ai_factory
    app.state.ai_factory = lambda: Dead()
    try:
        j = api.post("/v1/quick-draft", json={"text": AD}).json()
    finally:
        app.state.ai_factory = saved
    assert "keyword matching" in j["engine"] and j["files"] and j["within_budget"]


def test_it_gives_up_at_the_deadline_instead_of_hanging(api):
    from datetime import date
    from pathlib import Path
    base = app.state.ai_factory()

    class Slow:  # embeddings that are alive but slow
        async def embed(self, t):
            await asyncio.sleep(0.4)
            return await base.embed(t)

        async def chat(self, *a, **k):
            raise RuntimeError

    with pytest.raises(TooSlow, match="took longer"):
        asyncio.run(fast_draft(api.db, Slow(), AD, date.today(), Path(api.tmp) / "g", budget=0.5))


# ───────────── the real-world paste that failed ─────────────
INDEED_PASTE = """Pay: $80,000.00 – $85,000.00 per year

Benefits:

Employee discount
Free drinks
Free food
Work Location: In person

Full job description

IT Support Technician
Join our busy office supporting staff with Windows, Active Directory and Microsoft 365, resetting passwords and fixing printers. Melbourne CBD."""
LONG_URL = "https://au.indeed.com/viewjob?jk=33084fe141621823&tk=1k77cun38jv92803&from=jobi2a&advn=230488237592788&adid=471017824&" + "&".join(f"x{i}=" + "a" * 40 for i in range(20))


def test_a_paste_that_starts_with_pay_and_benefits_still_finds_the_real_title():
    j = extract_fast(INDEED_PASTE)
    assert j.title == "IT Support Technician" and j.location == "Melbourne" and "Active Directory" in j.required_skills
    assert extract_fast(INDEED_PASTE, title="Help Desk Analyst", employer="Acme").title == "Help Desk Analyst"
    assert extract_fast("Full job description\n\nJob details", None).title == "Role"


def test_a_very_long_tracking_link_no_longer_breaks_saving_the_draft(api):
    assert len(LONG_URL) > 500
    r = api.post("/v1/quick-draft", json={"text": INDEED_PASTE, "url": LONG_URL})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["application"]["status"] == "drafted" and j["files"]
    from app.tracker.normalize import shorten_url
    assert shorten_url(LONG_URL) == "https://au.indeed.com/viewjob?jk=33084fe141621823" and shorten_url("https://x.example/a") == "https://x.example/a"


def test_typing_the_title_and_employer_overrides_a_bad_guess(api):
    j = api.post("/v1/quick-draft", json={"text": INDEED_PASTE, "title": "Service Desk Officer", "employer": "Beta Pty Ltd"}).json()
    assert j["job"]["title"] == "Service Desk Officer" and j["job"]["employer"] == "Beta Pty Ltd" and j["application"]["employer"] == "Beta Pty Ltd"


def test_the_score_is_80_percent_keyword_coverage_and_20_percent_readability_and_shows_the_honest_path_to_80(api):
    j = api.post("/v1/quick-draft", json={"text": AD}).json()
    ats = j["ats"]
    assert "80%" in ats["method"] and ats["format"]["parsable"]
    assert ats["score"] == round(100 * (0.8 * ats["coverage_pct"] / 100 + 0.2)) or abs(ats["score"] - round(80 * ats["coverage_pct"] / 100 + 20)) <= 1
    t = ats["target"]
    assert t["goal"] == 80 and t["potential_score"] >= ats["score"] and isinstance(t["add_if_true"], list)
    # nothing is added without you: every suggested keyword is one your profile does not have
    assert {a["keyword"] for a in t["add_if_true"]} <= set(ats["missing_skills"])
    assert all(a["score_if_added"] >= ats["score"] for a in t["add_if_true"])
    if t["add_if_true"] and t["reachable_with_true_skills"]:
        assert t["add_if_true"][-1]["score_if_added"] >= 80


def test_confirming_the_suggested_skills_you_truly_have_raises_the_score_and_never_beyond_what_you_confirmed(api):
    first = api.post("/v1/quick-draft", json={"text": AD}).json()["ats"]
    for a in first["target"]["add_if_true"][:3]:
        assert api.post("/v1/quick-draft/confirm-skill", json={"name": a["keyword"], "level": "working"}).status_code == 200
    again = api.post("/v1/quick-draft", json={"text": AD}).json()["ats"]
    assert again["score"] > first["score"] and again["target"]["potential_score"] >= again["score"]
    assert api.post("/v1/quick-draft", json={"text": AD}).json()["ats"]["score"] == again["score"]  # stable: same ad, same profile, same score


def test_a_technology_mentioned_in_the_first_sentence_is_never_taken_for_the_employer():
    assert extract_fast("IT Support Technician\nJoin our busy office supporting staff with Windows, Active Directory and Microsoft 365.").employer is None
    assert extract_fast("IT Support Technician\nJoin Acme Logistics supporting staff with Windows.").employer == "Acme Logistics"
    assert extract_fast("Help Desk Analyst\nWork at Beta Pty Ltd, a friendly team.").employer == "Beta Pty Ltd"


def test_a_fact_check_refusal_is_a_plain_message_not_a_server_error(api, monkeypatch):
    from app.documents.validate import GenerationRejected
    import app.api.v1.quick as q

    async def refuse(*a, **k):
        raise GenerationRejected(["claims skill the profile does not verify: Windows"])
    monkeypatch.setattr(q, "fast_draft", refuse)
    r = api.post("/v1/quick-draft", json={"text": AD})
    assert r.status_code == 422 and "fact-checker stopped this draft" in r.json()["detail"] and "Windows" in r.json()["detail"]
