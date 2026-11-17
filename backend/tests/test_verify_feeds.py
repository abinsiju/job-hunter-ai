import json

import httpx
import pytest

from app.verify import feeds
from app.verify.fetch import PoliteFetcher
from app.verify.posting import Posting

GH_JOBS = {"jobs": [{"id": 4001, "title": "IT Support Officer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/4001", "updated_at": "2026-11-03T01:00:00Z",
                     "first_published": "2026-11-02T00:00:00Z", "location": {"name": "Melbourne, VIC"},
                     "content": "&lt;p&gt;Support staff.&lt;/p&gt;&lt;p&gt;Must hold Australian citizenship.&lt;/p&gt;"}]}
LEVER = [{"id": "uuid-1", "text": "Help Desk Analyst", "hostedUrl": "https://jobs.lever.co/acme/uuid-1", "applyUrl": "https://jobs.lever.co/acme/uuid-1/apply", "createdAt": 1762041600000,
          "categories": {"location": "Sydney", "commitment": "Full-time"}, "workplaceType": "hybrid", "descriptionPlain": "Help our staff.", "lists": [{"text": "Requirements", "content": "<li>ITIL</li>"}],
          "additionalPlain": "Equal opportunity."}]
WK = {"name": "Acme Pty Ltd", "jobs": [{"title": "Service Desk Analyst", "shortcode": "ABC1", "url": "https://apply.workable.com/j/ABC1", "application_url": "https://apply.workable.com/j/ABC1/apply",
                                       "city": "Melbourne", "state": "Victoria", "country": "Australia", "published_on": "2026-11-04", "employment_type": "Full-time", "description": "<p>Join us</p>"}]}
SR_LIST = {"content": [{"id": "999", "name": "Support Technician", "location": {"city": "Geelong", "region": "VIC", "remote": False}, "releasedDate": "2026-11-05T00:00:00.000Z", "company": {"name": "Acme"}}]}
SR_DETAIL = {"postingUrl": "https://jobs.smartrecruiters.com/acme/999", "applyUrl": "https://jobs.smartrecruiters.com/acme/999?oga=true",
             "jobAd": {"sections": {"jobDescription": {"text": "<p>Fix PCs</p>"}, "qualifications": {"text": "<p>Licence required</p>"}}}}


def handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    table = {"boards-api.greenhouse.io/v1/boards/acme/jobs": GH_JOBS, "boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme Pty Ltd"}, "api.lever.co/v0/postings/acme": LEVER,
             "apply.workable.com/api/v1/widget/accounts/acme": WK, "api.smartrecruiters.com/v1/companies/acme/postings/999": SR_DETAIL,
             "api.smartrecruiters.com/v1/companies/acme/postings": SR_LIST}
    for k, v in table.items():
        if k in u and (u.split("?")[0].endswith(k.split("/")[-1]) or True):
            if u.split("?")[0].endswith(k):
                return httpx.Response(200, json=v)
    return httpx.Response(404, json={"error": "nope"})


@pytest.fixture
def fetcher():
    async def nosleep(s): pass
    f = PoliteFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), delay=0, sleep=nosleep)
    f.requests = []
    return f


def test_board_names_are_guessed_from_the_employer_name_and_capped():
    assert feeds.slug_variants("Westside Logistics Pty Ltd") == ["westsidelogistics", "westside-logistics", "westside"]
    assert feeds.slug_variants("Acme Pty Ltd") == ["acme"]
    assert feeds.slug_variants("Study & Work") == ["studyandwork", "study-and-work", "study"]
    assert feeds.slug_variants("") == [] and feeds.slug_variants("Pty Ltd") == []
    assert len(feeds.slug_variants("Alpha Beta Gamma Delta")) <= 3


async def test_greenhouse_posting_is_read_with_its_company_name(fetcher):
    [p] = await feeds.greenhouse(fetcher, "acme")
    assert (p.title, p.employer, p.locations, p.job_id, p.source) == ("IT Support Officer", "Acme Pty Ltd", ["Melbourne, VIC"], "4001", "feed:greenhouse")
    assert "Must hold Australian citizenship." in p.text and "<" not in p.text and p.posted.isoformat() == "2026-11-02"


async def test_lever_posting_has_no_company_name_and_includes_the_lists(fetcher):
    [p] = await feeds.lever(fetcher, "acme")
    assert p.employer is None and p.title == "Help Desk Analyst" and p.locations == ["Sydney"] and p.posted.isoformat() == "2025-11-02" and p.employment == "Full-time"
    assert "Help our staff." in p.text and "Requirements" in p.text and "ITIL" in p.text and p.apply_url.endswith("/apply")


async def test_workable_and_smartrecruiters_feeds(fetcher):
    [w] = await feeds.workable(fetcher, "acme")
    assert (w.employer, w.title, w.locations, w.job_id) == ("Acme Pty Ltd", "Service Desk Analyst", ["Melbourne, Victoria, Australia"], "ABC1")
    [s] = await feeds.smartrecruiters(fetcher, "acme")
    assert s.text == "" and s.detail_url and s.locations == ["Geelong, VIC"]
    s = await feeds.smartrecruiters_detail(fetcher, s)
    assert "Fix PCs" in s.text and "Licence required" in s.text and s.url == "https://jobs.smartrecruiters.com/acme/999" and s.detail_url is None


async def test_unknown_boards_return_nothing_not_errors(fetcher):
    for feed in feeds.FEEDS.values():
        assert await feed(fetcher, "no-such-company") is None


async def test_search_collects_every_platform_and_caches_for_the_run(fetcher):
    calls = []
    orig = fetcher.get

    async def counting(url, **kw):
        calls.append(url)
        return await orig(url, **kw)
    fetcher.get = counting
    cache = {}
    found, notes = await feeds.search_employer(fetcher, "Acme Pty Ltd", cache)
    assert sorted({p.source for p, _ in found}) == ["feed:greenhouse", "feed:lever", "feed:smartrecruiters", "feed:workable"] and all(slug == "acme" for _, slug in found)
    assert any("board found" in n for n in notes)
    n = len(calls)
    await feeds.search_employer(fetcher, "Acme", cache)  # same board names: answered from the cache
    assert len(calls) == n


async def test_a_blocked_feed_is_recorded_and_the_others_still_run():
    def h(req):
        return httpx.Response(403) if "lever" in str(req.url) else handler(req)
    async def nosleep(s): pass
    f = PoliteFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(h)), delay=0, sleep=nosleep)
    found, notes = await feeds.search_employer(f, "Acme")
    assert {p.source for p, _ in found} == {"feed:greenhouse", "feed:workable", "feed:smartrecruiters"} and any("lever/acme" in n and "403" in n for n in notes)
