"""Source check card, Closed tab, Gmail draft card and assisted-fill card, driven through the real dashboard."""
import re
from datetime import datetime, timezone

import pytest
from playwright.sync_api import expect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Application, Listing
from tests.ats_fixtures import ATSServer
from tests_e2e.conftest import listing


@pytest.fixture
def verified(engine, fresh):
    with Session(engine) as db:
        listing(db, title="Systems Support Officer", employer="Initech", location="Melbourne", url="https://example.test/ad/10", gate_status="REVIEW", score=77.0,
                description_complete=True, confidence="VERIFIED", employer_url="https://boards.greenhouse.io/initech/jobs/1", application_route="employer_ats",
                route_url="https://boards.greenhouse.io/initech/jobs/1", open_status="open", verified_at=datetime.now(timezone.utc), flags=[],
                verification_notes=["greenhouse/initech: board found, 3 postings", "matched on title + text"],
                discrepancies=[{"field": "work_rights", "severity": "review", "label": "Work rights: citizenship", "third_party": "(not mentioned)",
                                "employer": "Applicants must be Australian citizens."},
                               {"field": "salary", "severity": "note", "label": "Salary", "third_party": "60000–70000 per year", "employer": "30 per hour"}])
        listing(db, title="Network Technician", employer="Hooli", location="Melbourne", url="https://example.test/ad/11", gate_status="PASS", score=85.0, flags=[],
                open_status="closed", closed_reason="The employer's closing date (2026-10-01) has passed.", confidence="VERIFIED", description_complete=True)
        ids = {l.title: l.id for l in db.scalars(select(Listing))}
    return ids


def test_job_page_shows_confidence_next_to_work_rights_and_both_quotes(ui, verified):
    p = ui.signed_in()
    p.goto(ui.web + f"/jobs/{verified['Systems Support Officer']}")
    line = p.get_by_test_id("work-rights-line")
    expect(line).to_contain_text("Work rights")
    expect(line.get_by_text("Verified with employer")).to_be_visible()
    card = p.get_by_test_id("verification-card")
    expect(card.get_by_text("The two ads disagree: Work rights: citizenship.")).to_be_visible()
    expect(card.get_by_text("(not mentioned)")).to_be_visible()
    expect(card.get_by_text("Applicants must be Australian citizens.")).to_be_visible()
    expect(card.get_by_text("Smaller differences")).to_be_visible()
    expect(card.get_by_text("Employer's application form")).to_be_visible()
    # a job with nothing wrong shows only a one-line source check
    p.goto(ui.web + "/jobs/2")
    expect(p.get_by_test_id("verification-card").get_by_role("button", name="Details")).to_be_visible()
    expect(p.locator("#emp-url")).to_have_count(0)
    # the snippet-only listing says so, next to the same line
    p.goto(ui.web + "/jobs/2")
    expect(p.get_by_test_id("work-rights-line").get_by_text("Not checked")).to_be_visible()


def test_closed_jobs_leave_the_main_list_and_live_in_their_own_tab(ui, verified):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs")
    expect(p.get_by_text("Systems Support Officer")).to_be_visible()
    expect(p.get_by_text("Network Technician")).to_have_count(0)
    p.get_by_role("button", name=re.compile(r"^Closed")).click()
    expect(p.get_by_text("Network Technician")).to_be_visible()
    p.get_by_text("Network Technician").click()
    expect(p.get_by_text("Nothing will be drafted for it.")).to_be_visible()
    expect(p.get_by_role("button", name=re.compile("Apply: fill the form"))).to_have_count(0)


def test_pasting_a_job_board_link_is_refused_politely(ui, verified):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs/2")
    p.get_by_role("button", name="Details").click()
    p.locator("#emp-url").fill("https://www.seek.com.au/job/123")
    p.get_by_role("button", name="Check", exact=True).click()
    p.get_by_text("What was checked").click()
    expect(p.get_by_text(re.compile("could not read the employer link"))).to_be_visible()


@pytest.fixture
def approved_app(engine, fresh, tmp_path):
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4 resume")
    (tmp_path / "cover_letter.pdf").write_bytes(b"%PDF-1.4 letter")
    with Session(engine) as db:
        a = db.scalar(select(Application).where(Application.employer == "Echo Ltd"))
        a.doc_paths = {"resume.pdf": str(tmp_path / "resume.pdf"), "cover_letter.pdf": str(tmp_path / "cover_letter.pdf")}
        a.draft_meta = {"cover_letter": {"greeting": "Dear Hiring Manager,", "paragraphs": ["I am writing to apply."], "signoff": "Kind regards,", "subject": "x"},
                        "apply_emails": [{"address": "careers@echo.test", "near_apply_cue": True}], "apply_links": []}
        db.commit()
        return a.id


def test_gmail_draft_is_created_never_sent_and_the_card_says_so(ui, approved_app, fresh):
    gmail = fresh["gmail"]
    before = len(gmail.drafts)
    p = ui.signed_in()
    p.goto(ui.web + f"/tracker/{approved_app}")
    card = p.get_by_test_id("email-card")
    expect(card.locator("#to")).to_have_value("careers@echo.test")
    expect(card.get_by_text("Application – Service Desk Officer – ")).to_be_visible()
    card.get_by_role("button", name="Create Gmail draft").click()
    expect(card.get_by_text("It has not been sent")).to_be_visible()
    assert len(gmail.drafts) == before + 1
    _, flags, msg = gmail.drafts[-1]
    assert "\\Draft" in flags and msg["To"] == "careers@echo.test" and len(list(msg.iter_attachments())) == 2
    assert set(gmail.commands) <= {"LOGIN", "LIST", "APPEND", "LOGOUT", "CAPABILITY"}


def test_assisted_fill_stops_before_submit_and_shows_what_was_left_blank(ui, approved_app):
    ats = ATSServer()
    try:
        p = ui.signed_in()
        p.goto(ui.web + f"/tracker/{approved_app}")
        card = p.get_by_test_id("assist-card")
        card.locator("#aurl").fill(ats.base + "/workable/apply")
        card.get_by_role("button", name="Open and fill the application").click()
        expect(card.get_by_text("Yours now: automation has stopped")).to_be_visible(timeout=90000)
        expect(card.get_by_text(re.compile(r"Needs you \("))).to_be_visible()
        expect(card.get_by_text("Work-rights questions to read")).to_be_visible()
        expect(card.get_by_role("link", name=re.compile("filled form screenshot"))).to_be_visible()
        card.get_by_role("button", name="Close the window").click()
        assert ats.submissions() == []  # nothing was ever submitted
    finally:
        ats.close()


def test_assisted_fill_refuses_job_boards(ui, approved_app):
    p = ui.signed_in()
    p.goto(ui.web + f"/tracker/{approved_app}")
    card = p.get_by_test_id("assist-card")
    card.locator("#aurl").fill("https://www.seek.com.au/job/1")
    card.get_by_role("button", name="Open and fill the application").click()
    expect(card.get_by_text(re.compile("never touches Seek"))).to_be_visible()


def test_settings_shows_gmail_is_connected_for_drafts_only(ui, fresh):
    p = ui.signed_in()
    p.goto(ui.web + "/settings")
    expect(p.get_by_text("Gmail drafts")).to_be_visible()
    expect(p.get_by_text(re.compile("nothing is ever sent for you"))).to_be_visible()


def test_the_tracker_offers_an_autofill_code_for_your_own_browser(ui, approved_app):
    p = ui.signed_in()
    p.goto(ui.web + f"/tracker/{approved_app}")
    card = p.get_by_test_id("autofill-card")
    expect(card.get_by_text("Fill it in your own browser")).to_be_visible()
    card.get_by_role("button", name="Make a 30-minute autofill code").click()
    expect(card.locator("#afc")).to_have_value(re.compile(r"^jh1:\d+:\S+"))
    expect(card.get_by_text("chrome://extensions")).to_be_visible()
    expect(card.get_by_text("only for Echo Ltd")).to_be_visible()
