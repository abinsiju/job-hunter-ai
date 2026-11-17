"""Quick draft in a real browser: paste an ad, get documents and an ATS report fast."""
import re

from playwright.sync_api import expect

AD = """Title: Service Desk Analyst (Part-time, 20 hrs/week)
Employer: Acme Pty Ltd
Location: Melbourne VIC

You will provide first line support to staff using Active Directory and Microsoft 365, resetting passwords, setting up laptops and printers, and troubleshooting faults.
Desirable: ITIL, PowerShell. Students welcome."""


def test_pasting_an_ad_gives_documents_and_an_ats_report_within_ten_seconds(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/quick")
    expect(p.get_by_role("heading", level=1)).to_have_text("Quick draft")
    btn = p.get_by_role("button", name=re.compile("Make my resume and cover letter"))
    expect(btn).to_be_disabled()
    p.locator("#qtext").fill(AD)
    btn.click()
    res = p.get_by_test_id("quick-result")
    expect(res.get_by_text(re.compile(r"Done in \d+\.\d s"))).to_be_visible(timeout=15000)
    secs = float(re.search(r"Done in (\d+\.\d) s", res.inner_text()).group(1))
    assert secs <= 10, secs
    expect(res.get_by_text("Your documents")).to_be_visible()
    expect(res.get_by_text("Resume (PDF)")).to_be_visible()
    ats = p.get_by_test_id("ats-card")
    expect(ats.get_by_text(re.compile(r"ATS match: \d+ / 100"))).to_be_visible()
    expect(ats.get_by_text("Readable by an ATS")).to_be_visible()
    expect(ats.get_by_text("cannot promise a rank")).to_be_visible()


def test_a_skill_not_in_your_profile_is_only_added_when_you_say_it_is_true(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/quick")
    p.locator("#qtext").fill(AD + " You will also use ServiceNow every day.")
    p.get_by_role("button", name=re.compile("Make my resume and cover letter")).click()
    ats = p.get_by_test_id("ats-card")
    chip = ats.get_by_role("button", name="ServiceNow")
    expect(chip).to_be_visible(timeout=15000)
    chip.click()
    expect(ats.get_by_text(re.compile("I really have"))).to_be_visible()
    ats.get_by_role("button", name="Working").last.click()  # the single-skill row (the first is "all of these")
    expect(p.get_by_test_id("ats-card").get_by_role("button", name="ServiceNow")).to_have_count(0, timeout=20000)  # now in the profile and on the resume


def test_an_ad_that_needs_citizenship_makes_no_documents(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/quick")
    p.locator("#qtext").fill("Title: Graduate Analyst\nEmployer: Dept of Things\nLocation: Canberra\n\nApplicants must be Australian citizens and hold a baseline security clearance. Support users with Active Directory daily.")
    p.get_by_role("button", name=re.compile("Make my resume and cover letter")).click()
    res = p.get_by_test_id("quick-result")
    expect(res.get_by_text("Not eligible")).to_be_visible(timeout=15000)
    expect(res.get_by_text(re.compile("Gate FAIL"))).to_be_visible()
    expect(res.get_by_text("Your documents")).to_have_count(0)
