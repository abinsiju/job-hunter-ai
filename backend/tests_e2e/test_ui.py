import re

import pytest
from playwright.sync_api import expect

from tests_e2e.conftest import PASSWORD, Ui

AD = """IT Support Officer (Part-time, 20 hrs/week) - Westside Logistics, Werribee VIC
We are a growing logistics company looking for a part-time IT Support Officer to look after our 40 staff.
You will support staff with PCs, printers and Microsoft 365 accounts, help with user accounts in Active Directory,
and assist with basic network issues. Experience with SharePoint is a plus. No minimum experience required; students
in an IT degree are welcome. A current driver's licence is required as you will occasionally visit our depot.
Pay: $32 per hour. Applications close 30 November 2026."""

ROUTES = ["/", "/jobs", "/jobs/1", "/jobs/2", "/jobs/4", "/studio", "/tracker", "/tracker/1", "/profile", "/settings"]


def stat(page, label):
    return page.locator(".stat", has_text=label).locator(".stat-value")


# ───────────────────────────── sign in, sign out, guards ─────────────────────────────
def test_signed_out_visitors_are_sent_to_login_and_back_after_signing_in(ui):
    p = ui.page
    p.goto(ui.web + "/jobs")
    expect(p).to_have_url(re.compile(r"/login\?next=%2Fjobs"))
    p.fill("#p", PASSWORD)  # fill the page the guard sent us to (a fresh /login would drop ?next=)
    p.click("button[type=submit]")
    expect(p).to_have_url(ui.web + "/jobs")
    expect(p.get_by_role("heading", name="Jobs", level=1)).to_be_visible()


def test_wrong_password_shows_a_clear_error_and_does_not_sign_in(ui):
    p = ui.page
    p.goto(ui.web + "/login")
    expect(p.get_by_role("button", name="Sign in")).to_be_disabled()  # nothing typed yet
    p.fill("#p", "definitely-wrong-password")
    p.click("button[type=submit]")
    expect(p.locator(".login-err")).to_contain_text("not right")
    expect(p).to_have_url(re.compile(r"/login"))


def test_sign_out_ends_the_session(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/settings")
    p.get_by_role("button", name="Sign out").click()
    expect(p).to_have_url(re.compile(r"/login"))
    p.goto(ui.web + "/tracker")
    expect(p).to_have_url(re.compile(r"/login"))


def test_session_cookie_is_httponly_and_invisible_to_page_scripts(ui):
    p = ui.signed_in()
    cookie = next(c for c in ui.ctx.cookies() if c["name"] == "jh_session")
    assert cookie["httpOnly"] is True and cookie["sameSite"] == "Lax"
    assert "jh_session" not in p.evaluate("document.cookie")
    assert not p.evaluate("JSON.stringify(Object.assign({}, localStorage, sessionStorage))").count(cookie["value"][:20])


def test_api_proxy_refuses_unauthenticated_and_cross_site_requests(ui, browser):
    anon = browser.new_context()
    assert anon.request.get(ui.web + "/api/v1/overview").status == 401
    assert anon.request.get(ui.web + "/api/v1/applications").status == 401
    anon.close()
    ui.signed_in()
    r = ui.ctx.request.post(ui.web + "/api/v1/blocklist", data={"employer": "Evil Co"}, headers={"Origin": "http://evil.example"})
    assert r.status == 403
    assert ui.ctx.request.get(ui.web + "/api/v1/blocklist").json() == []  # nothing was added
    assert ui.ctx.request.post(ui.web + "/api/v1/blocklist", data={"employer": "Fine Co"}, headers={"Origin": ui.web}).status == 201


# ───────────────────────────── Today ─────────────────────────────
def test_today_shows_the_owner_the_numbers_and_what_needs_attention(ui):
    p = ui.signed_in()
    expect(p.get_by_role("heading", level=1)).to_have_text(re.compile(r"^(Hello|Good (morning|afternoon|evening)), Alex$"))
    expect(stat(p, "Worth a look")).to_have_text("4")
    expect(stat(p, "Waiting on you")).to_have_text("2")
    expect(stat(p, "Applied")).to_have_text("3")
    expect(stat(p, "Interviews")).to_have_text("0")
    expect(p.get_by_text("Review and approve")).to_be_visible()
    expect(p.get_by_text("Applied? Record it")).to_be_visible()
    rows = p.locator(".list a.row[href^='/jobs/']")
    expect(rows.first).to_contain_text("Service Desk Analyst")  # the only clear match comes first
    rows.first.click()
    expect(p).to_have_url(ui.web + "/jobs/1")


# ───────────────────────────── Jobs ─────────────────────────────
def test_jobs_tabs_search_and_excluded_roles_are_kept_out_of_the_main_list(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs")
    expect(p.locator(".list a.row")).to_have_count(4)
    expect(p.get_by_text("IT Support Officer APS4")).to_have_count(0)  # citizenship role never appears in the main list
    p.get_by_role("button", name=re.compile(r"Not eligible\s*1")).click()
    expect(p.locator(".list a.row")).to_have_count(1)
    expect(p.locator(".list a.row")).to_contain_text("APS4")
    p.get_by_role("button", name=re.compile(r"Worth a look")).click()
    p.get_by_label("Search title, employer or place").fill("werribee")
    expect(p.locator(".list a.row")).to_have_count(1)
    expect(p.locator(".list a.row")).to_contain_text("Westside Logistics")
    p.get_by_label("Search title, employer or place").fill("zzzz")
    expect(p.get_by_text("No matches")).to_be_visible()


def test_a_job_can_be_hidden_and_restored(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs")
    row = p.locator(".list a.row", has_text="Globex")
    row.get_by_role("button", name="Not interested").click()
    expect(p.get_by_text("Hidden. You can find it under Hidden.")).to_be_visible()
    expect(p.locator(".list a.row", has_text="Globex")).to_have_count(0)
    p.get_by_role("button", name=re.compile(r"Hidden\s*1")).click()
    p.get_by_role("button", name="Restore").click()
    p.get_by_role("button", name=re.compile(r"Worth a look")).click()
    expect(p.locator(".list a.row", has_text="Globex")).to_have_count(1)


def test_find_jobs_explains_missing_keys_instead_of_failing(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs")
    p.get_by_role("button", name="Find jobs").first.click()
    dlg = p.get_by_role("dialog", name="Find jobs")
    expect(dlg).to_contain_text("Adzuna keys are not set yet")
    expect(dlg.get_by_role("button", name=re.compile("Search and score"))).to_be_disabled()
    dlg.get_by_role("button", name="Employer feeds").click()
    expect(dlg).to_contain_text("First Focus")
    expect(dlg.get_by_role("button", name=re.compile("Fetch and score"))).to_be_enabled()
    p.keyboard.press("Escape")
    expect(dlg).to_have_count(0)


# ───────────────────────────── Job detail ─────────────────────────────
def test_job_detail_explains_the_score_and_offers_the_right_action(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs/1")
    expect(p.get_by_role("heading", level=1)).to_have_text("Service Desk Analyst")
    expect(p.get_by_text("Matches your profile and your rules")).to_be_visible()
    expect(p.get_by_text("88 out of 100")).to_be_visible()
    p.get_by_role("button", name="Show what matched and what is missing").click()
    expect(p.locator(".chip.bad", has_text="Azure")).to_be_visible()
    expect(p.locator(".chip.good", has_text="Microsoft 365")).to_be_visible()
    expect(p.get_by_role("button", name=re.compile("Apply: fill the form for me"))).to_be_visible()  # full ad: can draft directly
    # a snippet-only listing must send you to paste the full ad
    p.goto(ui.web + "/jobs/2")
    expect(p.get_by_text("Only a snippet of the ad was available")).to_be_visible()
    link = p.get_by_role("link", name=re.compile("Draft from the full ad"))
    assert link.get_attribute("href") == "/studio?listing=2"
    assert p.get_by_role("link", name="Open the original ad").get_attribute("href") == "https://example.test/ad/2"
    assert p.get_by_role("link", name="Open the original ad").get_attribute("target") == "_blank"
    expect(p.get_by_role("button", name=re.compile("Apply: fill the form"))).to_have_count(0)


def test_excluded_job_shows_why_and_cannot_be_drafted(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs/4")
    expect(p.get_by_text("Not eligible for you")).to_be_visible()
    expect(p.get_by_role("alert").filter(has_text="requires citizenship")).to_be_visible()
    expect(p.get_by_role("button", name=re.compile("Draft"))).to_have_count(0)
    expect(p.get_by_role("link", name=re.compile("Draft from the full ad"))).to_have_count(0)


def test_blocking_an_employer_needs_confirmation_and_shows_in_settings(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs/3")
    p.get_by_role("button", name="Block employer").click()
    expect(p.get_by_role("dialog", name="Block Globex?")).to_be_visible()
    p.get_by_role("button", name="Cancel").click()
    p.get_by_role("button", name="Block employer").click()
    p.get_by_role("button", name="Block Globex").click()
    expect(p.get_by_text("Globex is on your blocklist.")).to_be_visible()
    p.goto(ui.web + "/settings")
    expect(p.locator(".list .row", has_text="Globex")).to_be_visible()
    p.locator(".list .row", has_text="Globex").get_by_role("button", name="Remove").click()
    expect(p.get_by_text("No blocked employers.")).to_be_visible()


# ───────────────────────────── Tracker ─────────────────────────────
def test_tracker_board_has_the_right_cards_in_the_right_columns(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/tracker")
    col = lambda name: p.locator(f"section.col[aria-label='{name}']")
    expect(col("Draft")).to_contain_text("Delta Corp")
    expect(col("Approved")).to_contain_text("Echo Ltd")
    for who in ("Foxtrot Pty Ltd", "Example Studios", "Example Recruiters"):
        expect(col("Applied")).to_contain_text(who)
    expect(col("Interview").locator(".kcard")).to_have_count(0)
    p.get_by_role("button", name="List").click()
    expect(p.locator(".list a.row")).to_have_count(5)
    p.get_by_role("button", name=re.compile(r"Needs action\s*2")).click()
    expect(p.locator(".list a.row")).to_have_count(2)


def test_full_application_lifecycle_requires_proof_and_records_history(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/tracker")
    p.locator(".kcard", has_text="Delta Corp").click()
    expect(p.get_by_role("heading", level=1)).to_have_text("Delta Corp")
    expect(p.get_by_text("This app never submits anything")).to_be_visible()
    p.get_by_role("button", name="Approve the documents").click()
    expect(p.get_by_text("Marked approved")).to_be_visible()
    p.get_by_role("button", name="I've applied").click()
    dlg = p.get_by_role("dialog", name="Record your application")
    save = dlg.get_by_role("button", name="Record as applied")
    expect(save).to_be_disabled()
    dlg.get_by_label("Confirmation or reference").fill("ok")
    expect(save).to_be_disabled()  # too short to be real proof
    dlg.get_by_label("Confirmation or reference").fill("Seek confirmation email, ref 8841")
    expect(save).to_be_enabled()
    save.click()
    expect(p.get_by_text("Recorded. Good luck!")).to_be_visible()
    expect(p.locator(".kv-block")).to_contain_text("Seek confirmation email, ref 8841")
    for step in ("Draft", "Approved", "Applied"):  # the labels the timeline shows
        expect(p.locator(".tl-title", has_text=step)).to_have_count(1)
    p.get_by_label("Add a note").fill("Recruiter will call Thursday")
    p.get_by_role("button", name="Add", exact=True).click()
    expect(p.get_by_text("Recruiter will call Thursday")).to_be_visible()
    p.get_by_role("button", name="I got an interview").click()
    expect(p.locator(".pill", has_text="Interview").first).to_be_visible()
    p.get_by_role("button", name="Withdraw").click()
    p.get_by_role("dialog").get_by_role("button", name="Withdraw").click()
    expect(p.get_by_text("This application is closed.")).to_be_visible()


def test_log_application_creates_a_draft_and_a_duplicate_is_refused(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/tracker")
    p.get_by_role("button", name="Log application").first.click()
    dlg = p.get_by_role("dialog", name="Log an application")
    expect(dlg.get_by_role("button", name="Save")).to_be_disabled()
    dlg.get_by_label("Employer").fill("Initech")
    dlg.get_by_label("Role").fill("IT Officer")
    dlg.get_by_role("button", name="Save").click()
    expect(p).to_have_url(re.compile(r"/tracker/\d+$"))
    expect(p.get_by_role("heading", level=1)).to_have_text("Initech")
    p.goto(ui.web + "/tracker")
    p.get_by_role("button", name="Log application").first.click()
    dlg = p.get_by_role("dialog", name="Log an application")
    dlg.get_by_label("Employer").fill("Foxtrot")
    dlg.get_by_label("Role").fill("IT Officer (Part-time)")
    dlg.get_by_role("button", name="Save").click()
    expect(dlg.get_by_role("alert")).to_contain_text("Duplicate applications are blocked")


def test_applications_from_before_the_tracker_are_labelled_honestly(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/tracker")
    p.locator(".kcard", has_text="Example Studios").click()
    expect(p.get_by_text("Before the tracker").first).to_be_visible()
    expect(p.get_by_text("Imported from your earlier list")).to_be_visible()
    expect(p.get_by_text("No confirmation is on file")).to_be_visible()


# ───────────────────────────── Draft studio (uses the real local model) ─────────────────────────────
def test_draft_studio_scores_an_ad_and_produces_downloadable_documents(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/studio")
    btn = p.get_by_role("button", name="Score & draft documents")
    expect(btn).to_be_disabled()
    p.get_by_label("Job ad").fill(AD)
    p.get_by_label("Link to the ad (helps catch duplicates)").fill("https://example.test/westside")
    expect(btn).to_be_enabled()
    btn.click()
    expect(p.get_by_text("Draft saved to your tracker.")).to_be_visible(timeout=180000)
    expect(p.get_by_text("Westside Logistics").first).to_be_visible()
    for label in ("Resume (PDF)", "Cover letter (PDF)", "Resume (Word)", "Cover letter (Word)"):
        expect(p.get_by_text(label)).to_be_visible()
    p.get_by_role("button", name="Preview").first.click()
    frame = p.locator("iframe.preview")
    src = frame.get_attribute("src")
    assert "/api/v1/applications/" in src and "/documents/" in src and ".pdf" in src
    pdf = ui.ctx.request.get(ui.web + src.split("#")[0])
    assert pdf.status == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.body()[:5] == b"%PDF-"
    assert "inline" in pdf.headers["content-disposition"]
    p.keyboard.press("Escape")
    word = p.locator("a[download]").nth(2)
    docx = ui.ctx.request.get(ui.web + word.get_attribute("href"))
    assert docx.status == 200 and docx.body()[:2] == b"PK" and "attachment" in docx.headers["content-disposition"]
    p.get_by_role("link", name="Open in tracker").click()
    expect(p).to_have_url(re.compile(r"/tracker/\d+$"))
    expect(p.get_by_role("heading", level=1)).to_have_text("Westside Logistics")
    # Next keeps visited pages mounted but hidden, so only look at what is actually visible
    expect(p.locator("text=Resume (PDF) >> visible=true")).to_have_count(1)
    expect(p.get_by_role("button", name="Approve the documents")).to_be_visible()


def test_studio_prefills_the_link_when_opened_from_a_job(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/studio?listing=2")
    expect(p.get_by_label("Link to the ad (helps catch duplicates)")).to_have_value("https://example.test/ad/2")
    expect(p.get_by_label("Where did you find it?")).to_have_value("adzuna")
    expect(p.get_by_text("Drafting for")).to_contain_text("IT Support Officer")


# ───────────────────────────── Assistant ─────────────────────────────
def test_assistant_answers_from_the_data_and_cannot_be_talked_into_actions(ui):
    p = ui.signed_in()
    p.wait_for_load_state("networkidle")  # the ⌘K shortcut is attached after hydration
    p.keyboard.press("Meta+k")
    dlg = p.get_by_role("dialog", name="Ask")
    expect(dlg).to_be_visible()
    dlg.get_by_role("button", name="What is waiting for my approval?").click()
    expect(dlg).to_contain_text("1 application waiting for your approval.")
    expect(dlg).to_contain_text("Delta Corp")
    box = dlg.get_by_label("Your question")
    box.fill("have I applied to Example Studios?")
    box.press("Enter")
    expect(dlg).to_contain_text("Yes. 1 record for Example Studios.")
    box.fill("why did job 5 score that way")
    box.press("Enter")
    expect(dlg).to_contain_text("IT Support Intern at Study and Work scored 80.5")
    box.fill("apply to the Westside job for me")
    box.press("Enter")
    expect(dlg).to_contain_text("I never apply, send or change anything")  # it only reads and explains
    assert ui.ctx.request.get(ui.web + "/api/v1/applications").json()["counts"]["applied"] == 3  # nothing was applied for
    p.keyboard.press("Escape")
    expect(dlg).to_have_count(0)


# ───────────────────────────── Profile and Settings ─────────────────────────────
def test_profile_shows_verified_facts_and_keeps_private_ones_private(ui, engine):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.db.models import ProfileFact
    with Session(engine) as db:  # the test must not depend on what is (or is not yet) confirmed in a real profile
        f = db.scalar(select(ProfileFact).where(ProfileFact.category == "contact", ProfileFact.key == "full_address"))
        f.status, f.last_confirmed, f.value = "needs_confirmation", None, {"note": "not yet recorded"}
        db.commit()
    p = ui.signed_in()
    p.goto(ui.web + "/profile")
    expect(p.get_by_role("heading", level=1)).to_have_text("Profile")
    expect(p.get_by_text("Used in documents").first).to_be_visible()
    expect(p.locator(".chip", has_text="Python")).to_be_visible()
    expect(p.locator(".pill", has_text="Forms only").first).to_be_visible()
    expect(p.locator(".pill", has_text="Private").first).to_be_visible()
    expect(p.locator(".pill", has_text="Needs confirmation").first).to_be_visible()  # an unconfirmed fact is labelled as such


def test_settings_reports_services_and_manages_the_blocklist(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/settings")
    expect(p.locator(".row", has_text="Database").locator(".status-dot")).not_to_have_class(re.compile("off"))
    expect(p.locator(".row", has_text="Queue").locator(".status-dot")).not_to_have_class(re.compile("off"))
    expect(p.get_by_text("Add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env")).to_be_visible()
    expect(p.locator(".card", has_text="Your safety rules")).to_have_count(0)  # the heading is outside the card
    expect(p.locator(".card").get_by_text("Nothing is ever submitted for you.")).to_be_visible()
    p.get_by_label("Employer to block").fill("Pay To Play Co")
    p.get_by_role("button", name="Block", exact=True).click()
    expect(p.locator(".list .row", has_text="Pay To Play Co")).to_be_visible()
    p.locator(".list .row", has_text="Pay To Play Co").get_by_role("button", name="Remove").click()
    expect(p.locator(".list .row", has_text="Pay To Play Co")).to_have_count(0)


# ───────────────────────────── layout, accessibility, runtime errors ─────────────────────────────
@pytest.mark.parametrize("route", ROUTES)
def test_phone_layout_has_a_tab_bar_and_never_scrolls_sideways(phone, route):
    p = phone.signed_in()
    p.goto(phone.web + route)
    p.wait_for_load_state("networkidle")
    expect(p.locator(".tabbar")).to_be_visible()
    expect(p.locator(".sidebar")).to_be_hidden()
    # Compare with the REAL device width (390). A phone browser silently widens window.innerWidth to fit overflowing content
    # (that is why iPhones zoom pages out), so measuring against innerWidth can never fail.
    width = 390
    assert p.evaluate("document.documentElement.scrollWidth") <= width + 1, f"{route} is wider than the phone screen"
    pokes = p.evaluate("""(w) => [...document.querySelectorAll("h1, .card, .list, .search, .stat, .callout, .segmented, .filters, .kcard, .row")]
        .filter((e) => e.offsetParent !== null && !e.closest(".board") && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().right > w - 12)
        .map((e) => e.className + " right=" + Math.round(e.getBoundingClientRect().right))""", width)
    assert pokes == [], f"{route}: elements run past the page margin on a phone: {pokes}"


def test_desktop_layout_has_a_sidebar_and_no_tab_bar(ui):
    p = ui.signed_in()
    expect(p.locator(".sidebar")).to_be_visible()
    expect(p.locator(".tabbar")).to_be_hidden()
    expect(p.locator(".sidebar a[aria-current='page']")).to_have_text("Today")


NAMELESS = """() => { const bad = [];
  for (const el of document.querySelectorAll("button, a[href], input:not([type=hidden]), select, textarea, [role=switch]")) {
    const name = (el.getAttribute("aria-label") || "").trim() || (el.innerText || "").trim() || (el.labels && el.labels[0] ? el.labels[0].innerText.trim() : "") || (el.getAttribute("title") || "").trim();
    if (!name) bad.push(el.outerHTML.slice(0, 140)); }
  return bad; }"""


@pytest.mark.parametrize("route", ROUTES)
def test_every_page_has_one_h1_and_every_control_has_an_accessible_name(ui, route):
    p = ui.signed_in()
    p.goto(ui.web + route)
    p.wait_for_load_state("networkidle")
    p.wait_for_timeout(600)
    assert p.locator("h1").count() == 1
    assert p.evaluate(NAMELESS) == []
    assert p.evaluate("document.documentElement.lang") == "en-AU"


def test_touring_every_page_raises_no_console_errors(ui):
    p = ui.signed_in()
    for route in ROUTES:
        p.goto(ui.web + route)
        p.wait_for_load_state("networkidle")
    p.keyboard.press("Meta+k")
    p.keyboard.press("Escape")
    assert ui.errors == [], ui.errors


# ───────────────────────────── installable app (PWA) ─────────────────────────────
def test_pwa_assets_are_served_to_signed_out_visitors_and_are_valid(ui, browser):
    anon = browser.new_context()
    req = anon.request
    m = req.get(ui.web + "/manifest.webmanifest", max_redirects=0)
    assert m.status == 200
    manifest = m.json()
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/" and manifest["name"] == "Job Hunter"
    purposes = {(i["sizes"], i["purpose"]) for i in manifest["icons"]}
    assert ("192x192", "any") in purposes and ("512x512", "any") in purposes and ("512x512", "maskable") in purposes
    for icon in manifest["icons"]:
        r = req.get(ui.web + icon["src"], max_redirects=0)
        assert r.status == 200 and r.headers["content-type"] == "image/png" and r.body()[:4] == b"\x89PNG"
    for path in ("/sw.js", "/offline.html", "/icons/apple-touch-icon.png"):
        assert req.get(ui.web + path, max_redirects=0).status == 200, path
    sw = req.get(ui.web + "/sw.js").text()
    assert "/api" not in sw and "cache.put" not in sw  # the worker never stores API data or pages
    anon.close()


def test_pages_declare_the_install_and_theme_metadata(ui):
    p = ui.signed_in()
    p.wait_for_load_state("networkidle")
    assert p.locator("link[rel=manifest]").get_attribute("href") == "/manifest.webmanifest"
    assert "apple-touch-icon" in p.locator("link[rel=apple-touch-icon]").get_attribute("href")
    assert "viewport-fit=cover" in p.locator("meta[name=viewport]").get_attribute("content")
    themes = p.locator("meta[name=theme-color]")
    assert {themes.nth(i).get_attribute("media") for i in range(themes.count())} == {"(prefers-color-scheme: light)", "(prefers-color-scheme: dark)"}
    assert p.locator("meta[name=mobile-web-app-capable], meta[name=apple-mobile-web-app-capable]").count() >= 1
    assert p.title() == "Job Hunter"
