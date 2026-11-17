"""The 20 principles of UX and animation, checked in a real browser. Each test names the principle it proves."""
import json
import re

import pytest
from playwright.sync_api import expect

AD = "Service Desk Analyst at Acme. You will provide first line support using Active Directory and Microsoft 365, resetting passwords and fixing printers. Part time."
PAGES = ["/", "/jobs", "/quick", "/tracker", "/sources", "/settings"]
INTERACTIVE = "button, a.btn, .icon-btn, .chip, .seg, input:not([type=hidden]), select, textarea, .tab, .nav-item, summary"


def css(page, selector, prop):
    return page.evaluate("([s, p]) => getComputedStyle(document.querySelector(s))[p]", [selector, prop])


# ───────────── 5, 6, 9, 11: the motion tokens exist and are used ─────────────
def test_p05_p06_p09_p11_motion_and_depth_come_from_one_set_of_tokens(ui):
    p = ui.signed_in()
    root = lambda v: p.evaluate("(v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()", v)
    assert "cubic-bezier" in root("--ease-spring") and "cubic-bezier" in root("--ease-out") and "cubic-bezier" in root("--ease-inout")  # 6 slow in & slow out
    ease = lambda v: [float(x) for x in re.search(r"cubic-bezier\(([^)]*)\)", root(v)).group(1).split(",")]
    assert ease("--ease-spring")[1] > 1.0  # 5 follow through: the curve (x1, y1, x2, y2) rises PAST 1 (y1 = 1.56) and settles
    ms = lambda v: (float(root(v)[:-2]) if root(v).endswith("ms") else float(root(v)[:-1]) * 1000)  # the browser may print 140ms as .14s
    for d in ("--dur-press", "--dur-fast", "--dur", "--dur-slow"):  # 9 snappy: nothing slower than a third of a second
        assert ms(d) <= 340, d
    assert ms("--dur-press") <= 100 and root("--hit") == "44px"
    for e in ("--elev-1", "--elev-2", "--elev-3"):  # 11 solid: three consistent depths from one light (the browser prints rgba as #rrggbbaa)
        assert re.search(r"rgba|#[0-9a-f]{6,8}", root(e)) and root(e).count(",") >= 1, e
    btn = css(p, ".btn", "transitionTimingFunction")
    assert "cubic-bezier" in btn and "linear" not in btn  # 6: real buttons use the curves, not linear or the default


# ───────────── 1, 2, 4: squash and stretch, anticipation, the poses of a button ─────────────
def test_p01_p02_p04_a_button_lifts_squashes_and_springs_back(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs")
    btn = p.locator(".btn:visible").first
    rest = btn.evaluate("e => getComputedStyle(e).transform")
    btn.hover()
    p.wait_for_timeout(250)
    lifted = btn.evaluate("e => getComputedStyle(e).transform")
    assert lifted != rest and "matrix" in lifted  # 2 anticipation: it lifts before it is pressed
    box = btn.bounding_box()
    p.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    p.mouse.down()
    p.wait_for_timeout(200)
    m = [float(x) for x in re.search(r"matrix\(([^)]*)\)", btn.evaluate("e => getComputedStyle(e).transform")).group(1).split(",")]
    p.mouse.up()
    assert m[0] < m[3] < 1.0  # 1 squash: narrower than it is tall, both under 1 (the poses: rest, lifted, pressed)
    assert "cubic-bezier(0.34, 1.56" in btn.evaluate("e => getComputedStyle(e).transitionTimingFunction")  # 5: the release is the spring


# ───────────── 5, 7: follow through and arcs exist as real keyframes ─────────────
def test_p05_p07_the_sheet_overshoots_and_the_toast_swings_along_an_arc(ui):
    p = ui.signed_in()
    kf = p.evaluate("""() => { const out = {}; for (const ss of document.styleSheets) { let rules; try { rules = ss.cssRules } catch { continue }
        for (const r of rules) if (r.type === CSSRule.KEYFRAMES_RULE) out[r.name] = [...r.cssRules].map(k => ({ at: k.keyText, t: k.style.transform })); } return out }""")
    sheet = {k["at"]: k["t"] for k in kf["sheet-in"]}
    assert "translateY(-" in sheet["55%"] and sheet["100%"] == "none"  # 5: it rises PAST its place (-7px), then settles
    toast = [k["t"] for k in kf["toast-arc"]]
    assert all("translate(" in t or t == "none" for t in toast) and any(re.search(r"translate\(-?\d+px, -?\d+px\)", t) and "rotate" in t for t in toast)  # 7: sideways AND up at once, with a turn
    assert {"pop-in", "shake", "stage-in", "netbar"} <= set(kf)


# ───────────── 3, 8, 10: staging, secondary action, exaggerated feedback ─────────────
def test_p03_p08_p10_results_arrive_in_order_success_pops_and_trouble_shakes(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/sources")
    p.get_by_role("button", name=re.compile("^More filters")).click()
    first = p.locator("#more-filters.stage > *").first
    assert first.evaluate("e => getComputedStyle(e).animationName") == "stage-in"  # 3 staging: children enter one after another
    assert p.locator("#more-filters.stage > *").nth(2).evaluate("e => getComputedStyle(e).animationDelay") == "0.12s"
    card = p.get_by_test_id("import-card")
    card.locator("#itext").fill(AD)
    card.locator("#iurl").fill("https://au.indeed.com/viewjob?jk=0123456789abcdef")  # an Indeed link under SEEK
    card.get_by_role("button", name="Import this job").click()
    bad = card.locator(".callout.bad")
    expect(bad).to_be_visible()
    assert bad.evaluate("e => getComputedStyle(e).animationName") == "shake"  # 10 exaggerated: trouble cannot be missed
    card.locator("#iurl").fill("https://www.seek.com.au/job/81234567")
    card.get_by_role("button", name="Import this job").click()
    good = card.locator(".callout.good")
    expect(good).to_be_visible()
    assert good.evaluate("e => getComputedStyle(e).animationName") == "pop-in"  # 8 secondary action: success pops


# ───────────── 13, 14: Fitts's law and bigger hit areas ─────────────
HIT_JS = """(sel) => {
  const bad = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none' || r.bottom < 0 || r.top > innerHeight || el.closest('[hidden]')) continue;
    if (el.closest('.sidebar') && innerWidth < 900) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;  // a disabled control takes no input, so it needs no hit area
    if (r.left + r.width / 2 > innerWidth || r.left + r.width / 2 < 0) continue;  // scrolled out of a horizontal strip: reachable by swiping
    { const t0 = document.elementFromPoint(Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)), Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2))); if (t0 && t0.closest('.tabbar, .topbar') && !el.closest('.tabbar, .topbar')) continue; }  // scrolled under a fixed bar: it is not reachable there, and the bar is itself checked
    const cx = Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)), cy = r.top + r.height / 2;
    { const c = document.elementFromPoint(cx, Math.min(innerHeight - 1, Math.max(0, cy))); if (!c || !(c === el || el.contains(c) || c.closest(sel) === el)) continue; }  // clipped by a scrolling strip or covered: not on screen right now
    const hits = (y) => { if (y < 0 || y >= innerHeight) return true; const t = document.elementFromPoint(cx, y); if (t && t.closest('nextjs-portal')) return true; /* the dev-only Next.js badge, not part of the app */ if (t && t.closest('.tabbar, .topbar') && !el.closest('.tabbar, .topbar')) return true; /* scrolled under a fixed bar: reachable by scrolling */ return !!t && (t === el || el.contains(t) || t.closest(sel) === el || el.closest('label') === t.closest('label') && !!t.closest('label')); };
    if (!(hits(cy - 21) && hits(cy + 21))) bad.push((el.tagName + '.' + (el.className || '') + ' ' + (el.innerText || el.getAttribute('aria-label') || '')).slice(0, 60));
  }
  return bad;
}"""


@pytest.mark.parametrize("path", PAGES)
def test_p13_p14_every_control_can_be_hit_in_a_44px_area_on_a_phone(phone, path):
    p = phone.signed_in()
    p.goto(phone.web + path)
    p.wait_for_timeout(600)
    assert p.evaluate(HIT_JS, INTERACTIVE) == [], path


def test_p13_the_main_action_is_big_and_sits_at_the_thumb(phone):
    p = phone.signed_in()
    p.goto(phone.web + "/quick")
    box = p.get_by_role("button", name=re.compile("Make my resume")).bounding_box()
    assert box["height"] >= 48 and box["width"] >= 200  # Fitts: the thing you do most is the biggest target on the screen
    tabs = p.locator(".tabbar .tab")
    assert tabs.count() == 5 and all(tabs.nth(i).bounding_box()["height"] >= 49 for i in range(5))  # 16 Miller: five tabs, each a full 49px


# ───────────── 15, 16: Hick's law and Miller's law ─────────────
def test_p15_p16_fewer_choices_up_front_and_chunks_of_about_seven(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/sources")
    card = p.get_by_test_id("search-card")
    expect(card.locator("#kw")).to_be_visible()
    expect(card.locator("#city")).to_be_visible()
    expect(card.locator("#days")).to_be_visible()
    assert card.locator("#more-filters").count() == 0 and card.locator("#smin").count() == 0  # 15 Hick: seven rarely-used fields wait behind one link
    role_chips = card.locator(".chip", has_text=re.compile("Officer|Analyst|Support|Technician|Engineer|Administrator"))
    assert role_chips.count() == 6  # 16 Miller: six shortcuts, not ten
    card.get_by_role("button", name=re.compile("More roles")).click()
    assert role_chips.count() == 10
    card.get_by_role("button", name=re.compile("^More filters")).click()
    expect(card.locator("#smin")).to_be_visible()
    assert p.locator(".sidebar .nav-item").count() >= 7


# ───────────── 17: the Doherty threshold ─────────────
def test_p17_feedback_appears_within_100ms_and_a_slow_request_shows_a_bar(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/quick")
    p.locator("#qtext").fill(AD)
    p.evaluate("""() => { window.__t = {}; const s = performance.now(); document.addEventListener('click', () => { window.__t.click = performance.now(); }, true);
        new MutationObserver(() => { if (!window.__t.busy && document.querySelector('.btn .spinner')) window.__t.busy = performance.now(); }).observe(document.body, { subtree: true, childList: true }); }""")
    p.route("**/api/v1/quick-draft", lambda route: (p.wait_for_timeout(500), route.continue_()))
    p.get_by_role("button", name=re.compile("Make my resume")).click()
    p.wait_for_function("window.__t.busy", timeout=3000)
    t = p.evaluate("window.__t")
    assert t["busy"] - t["click"] <= 100, t  # the button answers inside 100 ms, long before the work is done
    expect(p.get_by_test_id("netbar")).to_have_attribute("data-on", "true", timeout=2000)  # >150 ms: the top bar says "working"
    expect(p.get_by_test_id("quick-result")).to_be_visible(timeout=20000)
    expect(p.get_by_test_id("netbar")).to_have_attribute("data-on", "false", timeout=3000)


# ───────────── 18: Postel's law ─────────────
def test_p18_a_link_without_https_and_a_salary_written_80k_both_work(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/quick")
    p.locator("#qtext").fill(AD)
    p.locator("#qurl").fill('  "careers.acme.example/job/1" ')
    with p.expect_request("**/api/v1/quick-draft") as req:
        p.get_by_role("button", name=re.compile("Make my resume")).click()
    assert json.loads(req.value.post_data)["url"] == "https://careers.acme.example/job/1"
    p.goto(ui.web + "/sources")
    p.get_by_role("button", name=re.compile("^More filters")).click()
    p.locator("#smin").fill("$80k")
    p.locator("#smax").fill("95,000")
    p.locator("#dist").fill("20 km")
    with p.expect_request("**/api/v1/jobs") as req:
        p.get_by_role("button", name=re.compile("^Search$")).click()
    c = json.loads(req.value.post_data)["params"]
    assert (c["salary_min"], c["salary_max"], c["distance_km"]) == (80000, 95000, 20)
    card = p.get_by_test_id("import-card")
    card.locator("#itext").fill(AD)
    card.locator("#iurl").fill("seek.com.au/job/81234567")
    with p.expect_request("**/api/v1/sources/seek/import") as req:
        card.get_by_role("button", name="Import this job").click()
    assert json.loads(req.value.post_data)["url"] == "https://seek.com.au/job/81234567"


# ───────────── 19: Jakob's law ─────────────
def test_p19_it_behaves_like_the_apps_you_already_know(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/quick")
    p.locator("#qtext").fill(AD)
    with p.expect_request("**/api/v1/quick-draft"):
        p.locator("#qtext").press("Control+Enter")  # ⌘/Ctrl+Enter sends
    p.keyboard.press("Control+k")
    sheet = p.locator(".sheet")
    expect(sheet).to_be_visible()
    p.keyboard.press("Escape")  # Escape closes
    expect(sheet).to_have_count(0)
    p.goto(ui.web + "/jobs")
    p.keyboard.press("Tab")
    p.keyboard.press("Tab")
    ring = p.evaluate("(() => { const e = document.activeElement; const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.boxShadow] })()")
    assert ring[0] != "none" or ring[2] != "none"  # the focus ring is always visible


# ───────────── 20: frequency of use ─────────────
def test_p20_what_you_use_most_is_first_and_remembered(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/jobs")
    order = [t.strip() for t in p.locator(".sidebar .nav-item").all_inner_texts()][:4]
    assert order == ["Today", "Jobs", "Quick draft", "Tracker"]
    p.get_by_role("button", name=re.compile("^Not eligible")).click()
    p.goto(ui.web + "/sources")
    p.locator("#city").select_option("Sydney")
    p.get_by_role("button", name="Seek", exact=False).first.click()
    p.reload()
    p.wait_for_timeout(600)
    expect(p.locator("#city")).to_have_value("Sydney")
    p.goto(ui.web + "/jobs")
    p.wait_for_timeout(600)
    expect(p.get_by_role("button", name=re.compile("^Not eligible"))).to_have_attribute("aria-pressed", "true")


# ───────────── 12: overall appeal (consistency) and reduced motion ─────────────
def test_p12_numbers_do_not_jiggle_and_depth_is_consistent(ui):
    p = ui.signed_in()
    p.goto(ui.web + "/")
    p.goto(ui.web + "/jobs")
    p.wait_for_selector(".score-num", timeout=20000)
    assert "tabular-nums" in css(p, ".score-num", "fontVariantNumeric")  # 12: numbers keep their width while they count up
    assert css(p, ".card, .list", "boxShadow") != "none"


def test_motion_stops_for_people_who_ask_for_less(browser, fresh):
    from tests_e2e.conftest import Ui
    u = Ui(browser, fresh["web"])
    u.ctx.close()
    ctx = browser.new_context(reduced_motion="reduce", viewport={"width": 1200, "height": 800})
    ctx.set_default_timeout(60000)
    page = ctx.new_page()
    page.goto(fresh["web"] + "/login")
    from tests_e2e.conftest import PASSWORD
    page.fill("#p", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(fresh["web"] + "/")
    page.goto(fresh["web"] + "/jobs")
    page.wait_for_selector(".sidebar")
    page.wait_for_timeout(1200)  # let the page finish loading so the shortcut is listening
    assert float(page.evaluate("getComputedStyle(document.querySelector('.btn')).transitionDuration").replace("s", "").split(",")[0]) <= 0.001
    page.keyboard.press("Control+k")
    assert page.locator(".sheet").evaluate("e => getComputedStyle(e).animationName") == "none"
    ctx.close()
