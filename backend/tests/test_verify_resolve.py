from datetime import date, datetime

import httpx
import pytest

from app.db.models import Listing
from app.verify import verify as V
from app.verify.compare import compare
from app.verify.fetch import PoliteFetcher
from app.verify.posting import Posting
from app.verify.resolve import Target, choose, detect_route, match_one

TODAY = date(2026, 11, 10)
SNIPPET = "Provide first line support to staff across our Melbourne offices, resolving hardware and software issues and logging tickets in our service desk system. Our team"
FULL = (SNIPPET + " is friendly and supportive. You will set up laptops, reset passwords and escalate faults. Hours: part time, 20 hours per week. "
        "Applicants must be Australian citizens. Please apply through our careers page. Applications close 30 November 2026. " * 2)
CLEAN = FULL.replace("Applicants must be Australian citizens. ", "")


def gh_board(jobs):
    return {"jobs": jobs}


def job(title="IT Support Officer", loc="Melbourne, VIC", content=FULL, jid=7001, published="2026-11-02T00:00:00Z"):
    return {"id": jid, "title": title, "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{jid}", "first_published": published,
            "updated_at": published, "location": {"name": loc}, "content": content}


def fetcher(routes):
    def h(req: httpx.Request):
        u = str(req.url).split("?")[0]
        for k, v in routes.items():
            if u.endswith(k):
                if isinstance(v, httpx.Response):
                    return v
                return httpx.Response(200, json=v)
        return httpx.Response(404, json={})
    async def nosleep(s): pass
    return PoliteFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(h)), delay=0, sleep=nosleep)


def listing(**kw):
    base = dict(source="adzuna", source_id="5544332211", url="https://www.adzuna.com.au/land/ad/5544332211", title="IT Support Officer", employer="Acme Pty Ltd",
                location="Melbourne, VIC", description=SNIPPET, description_complete=False, posted_at=date(2026, 11, 3), salary_period="unknown")
    base.update(kw)
    return Listing(**base)


async def test_hidden_citizens_only_line_is_found_only_in_the_employer_ad():
    f = fetcher({"boards-api.greenhouse.io/v1/boards/acme/jobs": gh_board([job()]), "boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme Pty Ltd"}})
    l = listing()
    r = await V.verify_listing(f, l, TODAY)
    assert r.confidence == V.VERIFIED and r.open_status == "open" and r.employer_url.endswith("/jobs/7001")
    [d] = [d for d in r.discrepancies if d["field"] == "work_rights"]
    assert d["severity"] == "review" and d["third_party"] == "(not mentioned)" and "Australian citizens" in d["employer"]
    assert r.review_needed and "Australian citizens" in r.employer_text
    assert r.application_route == "employer_ats" and r.route_url.endswith("/jobs/7001")
    V.apply_result(l, r)
    assert l.confidence == "VERIFIED" and l.employer_description and l.description == SNIPPET and l.employer_url


async def test_no_employer_version_keeps_snippet_only_and_never_claims_more():
    f = fetcher({})
    r = await V.verify_listing(f, listing(), TODAY)
    assert (r.confidence, r.employer_text, r.discrepancies, r.open_status) == (V.SNIPPET_ONLY, None, [], "unknown")
    r = await V.verify_listing(f, listing(description=FULL, description_complete=True), TODAY)
    assert r.confidence == V.THIRD_PARTY_FULL and r.employer_text is None


async def test_same_title_in_another_city_is_a_different_vacancy():
    f = fetcher({"boards-api.greenhouse.io/v1/boards/acme/jobs": gh_board([job(loc="Perth, WA")]), "boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme"}})
    r = await V.verify_listing(f, listing(), TODAY)
    assert r.confidence == V.SNIPPET_ONLY and r.employer_url is None and any("no posting" in n for n in r.notes)


async def test_two_equally_good_postings_are_not_guessed_between():
    f = fetcher({"boards-api.greenhouse.io/v1/boards/acme/jobs": gh_board([job(jid=1111), job(jid=2222)]), "boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme"}})
    r = await V.verify_listing(f, listing(), TODAY)
    assert r.confidence == V.SNIPPET_ONLY and any("fit equally well" in n for n in r.notes)


async def test_a_matching_job_id_is_enough_when_the_text_differs():
    f = fetcher({"boards-api.greenhouse.io/v1/boards/acme/jobs": gh_board([job(content="x " * 150, jid=5544332211, published="2020-01-01T00:00:00Z")]),
                 "boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme"}})
    r = await V.verify_listing(f, listing(), TODAY)
    assert r.confidence == V.VERIFIED


async def test_pasted_employer_link_closed_by_404_and_by_closing_date():
    f = fetcher({"careers.acme.test/jobs/1": httpx.Response(404, text="gone", headers={"content-type": "text/html"})})
    r = await V.verify_listing(f, listing(), TODAY, employer_url="https://careers.acme.test/jobs/1")
    assert r.open_status == "closed" and "no longer exists" in r.closed_reason and r.confidence == V.SNIPPET_ONLY
    ld = ('<html><script type="application/ld+json">{"@type":"JobPosting","title":"IT Support Officer","description":"' + FULL.replace('"', "") +
          '","validThrough":"2026-10-01","hiringOrganization":{"name":"Acme"},"datePosted":"2026-09-01"}</script></html>')
    f = fetcher({"careers.acme.test/jobs/2": httpx.Response(200, text=ld, headers={"content-type": "text/html"}),
                 "careers.acme.test/robots.txt": httpx.Response(404)})
    r = await V.verify_listing(f, listing(), TODAY, employer_url="https://careers.acme.test/jobs/2")
    assert r.open_status == "closed" and "2026-10-01" in r.closed_reason and r.confidence == V.VERIFIED


async def test_job_boards_are_never_followed():
    f = fetcher({})
    r = await V.verify_listing(f, listing(), TODAY, employer_url="https://www.seek.com.au/job/123")
    assert r.employer_url is None and any("could not read the employer link" in n for n in r.notes) and r.confidence == V.SNIPPET_ONLY


async def test_listing_read_from_the_employers_own_board_is_verified():
    l = listing(source="workable:first-focus", url="https://apply.workable.com/first-focus/j/ABC123/", description=FULL, description_complete=True, employer="First Focus")
    r = await V.verify_listing(fetcher({}), l, TODAY)
    assert r.application_route == "employer_ats" and r.route_url == l.url
    assert r.confidence == V.VERIFIED and r.discrepancies == []


def test_matching_rules_title_employer_evidence():
    t = Target("IT Support Officer", "Acme Pty Ltd", "Melbourne", SNIPPET, posted=date(2026, 11, 3))
    ok = Posting("IT Support Officer", "u", FULL, employer="Acme", locations=["Melbourne, Victoria"])
    assert match_one(t, ok).evidence == ["text"]
    assert match_one(t, Posting("Senior Network Engineer", "u", FULL, employer="Acme")) is None
    assert match_one(t, Posting("IT Support Officer", "u", FULL, employer="Other Corp")) is None
    assert match_one(t, Posting("IT Support Officer", "u", "unrelated words " * 50, employer="Acme", locations=["Melbourne"])) is None  # no evidence
    assert match_one(t, Posting("IT Support Officer", "u", "unrelated words " * 50, employer="Acme", posted=date(2026, 11, 5))).evidence == ["date"]
    assert match_one(t, Posting("IT Support Officer", "u", "unrelated words " * 50, employer="Acme", posted=date(2026, 11, 20))) is None
    m, why = choose(t, [(ok, "acme")])
    assert m and why.startswith("matched on")


def test_comparison_covers_every_listed_field_with_quotes():
    third = "Full time role in Melbourne. Salary $60000 to $70000 per year. Must have a driver's licence. Applications close 20 November 2026. Hybrid working."
    emp = Posting("t", "u", "This is a part time role in Melbourne. Position pays $30 per hour. Degree is required. Fully remote working. Permanent residency essential. Applications close 30 November 2026.",
                  locations=["Sydney, NSW"], salary_min=30, salary_max=30, salary_period="hour")
    fields = {d["field"]: d for d in compare(third, emp, "Melbourne", (60000, 70000, "year"))}
    assert set(fields) >= {"work_rights", "mandatory", "location", "employment", "salary", "closing_date"}
    assert fields["work_rights"]["severity"] == "review" and "Permanent residency" in fields["work_rights"]["employer"]
    assert fields["salary"]["severity"] == "note" and "per year" in fields["salary"]["third_party"] and "per hour" in fields["salary"]["employer"]
    assert fields["closing_date"]["third_party"] == "2026-11-20" and fields["closing_date"]["employer"] == "2026-11-30"


def test_identical_ads_have_no_discrepancies():
    assert compare(CLEAN, Posting("t", "u", CLEAN, locations=["Melbourne"]), "Melbourne") == []


def test_application_route_detection():
    emp = Posting("t", "https://boards.greenhouse.io/acme/jobs/1", "x")
    assert detect_route("", "u", "Acme", emp) == ("employer_ats", "https://boards.greenhouse.io/acme/jobs/1")
    assert detect_route("Please send your resume to jobs@acme.test", "https://adzuna.com.au/x", "Acme", None) == ("email", "jobs@acme.test")
    assert detect_route("hello", "https://x", "Hays Recruitment", None)[0] == "recruiter"
    assert detect_route("Quick apply now", "https://x", "Acme", None)[0] == "seek_quick_apply"
    assert detect_route("hello", "https://x", "Acme", Posting("t", "https://careers.acme.test/j/1", "x"))[0] == "employer_site"
    assert detect_route("hello", "https://x", "Acme", None) == ("unknown", None)
