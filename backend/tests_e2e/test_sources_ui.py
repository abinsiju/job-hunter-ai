"""The Sources page in a real browser: honest labels, import, and no overclaiming."""
import re

from playwright.sync_api import expect

AD = ("Service Desk Analyst. Provide first line support to staff across our Melbourne offices, resolving hardware and software issues, "
      "resetting passwords and logging tickets in our service desk system. Part time, 20 hours per week.")


def test_sources_page_labels_real_connections_and_search_links_honestly(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/sources")
    expect(p.get_by_role("heading", level=1)).to_have_text("Sources")
    for name in ("seek", "indeed", "jora"):
        card = p.get_by_test_id(f"source-{name}")
        expect(card.get_by_text("Search link only")).to_be_visible()
        expect(card.get_by_text("Not a data connection.")).to_be_visible()
        expect(card.get_by_text("Browser search")).to_be_visible()
        expect(card.get_by_text("Manual import", exact=True)).to_be_visible()
    feeds = p.get_by_test_id("source-employer_feeds")
    expect(feeds.get_by_text("Authorised feed", exact=True).first).to_be_visible()
    expect(feeds.get_by_text("Real data connection.")).to_be_visible()
    expect(p.get_by_text("never automates Indeed Apply").first).to_be_visible()


def test_search_gives_links_for_the_board_sources_and_says_nothing_was_fetched(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/sources")
    p.get_by_role("button", name="Junior IT Technician").click()
    p.get_by_role("button", name="Seek", exact=False).first.click()  # choose SEEK only
    p.get_by_role("button", name=re.compile("^Search$")).click()
    res = p.get_by_test_id("result-seek")
    expect(res.get_by_text("Search link only: nothing fetched")).to_be_visible(timeout=60000)
    link = res.get_by_role("link", name=re.compile("Open this search on SEEK"))
    href = link.get_attribute("href")
    assert href.startswith("https://www.seek.com.au/jobs?") and "junior+it+technician".lower() in href.lower().replace("%20", "+") and link.get_attribute("target") == "_blank"
    assert p.get_by_test_id("result-indeed").count() == 0  # only the chosen source


def test_importing_a_pasted_job_works_and_a_wrong_link_is_refused(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/sources")
    card = p.get_by_test_id("import-card")
    card.locator("#itext").fill(AD)
    card.locator("#iurl").fill("https://au.indeed.com/viewjob?jk=0123456789abcdef")  # an Indeed link under SEEK
    card.get_by_role("button", name="Import this job").click()
    expect(card.get_by_text("not a SEEK address")).to_be_visible()
    card.locator("#iurl").fill("https://www.seek.com.au/job/81234567")
    card.locator("#iemp").fill("Acme Pty Ltd")
    card.locator("#iloc").fill("Melbourne VIC")
    card.get_by_role("button", name="Import this job").click()
    expect(card.get_by_text("Imported.")).to_be_visible()
    expect(p.get_by_test_id("source-seek").get_by_text("Jobs discovered")).to_be_visible()
