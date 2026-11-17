import json
import subprocess
from datetime import date

import httpx
import pytest

from app.discovery import websearch as W
from app.verify import feeds
from app.verify.fetch import PoliteFetcher

AD = "Join our service desk team in Melbourne. " * 12
JSONLD = ('<html><script type="application/ld+json">{"@type":"JobPosting","title":"Service Desk Analyst","description":"' + AD + '",'
          '"hiringOrganization":{"name":"Capgemini"},"jobLocation":{"address":{"addressLocality":"Melbourne"}},"datePosted":"2026-11-05"}</script></html>')


def runner(items, ok=True):
    def run(cmd, **kw):
        assert cmd[0] == "agent-reach" and isinstance(cmd, list)  # never a shell string
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"ok": ok, "items": items, "error": {"hint": "nope"}}), "")
    return run


def test_search_keeps_https_employer_pages_and_drops_job_boards_and_duplicates():
    items = [{"title": "a", "url": "https://careers.cap.test/job/1"}, {"title": "dup", "url": "https://careers.cap.test/job/1?utm_source=x"},
             {"title": "seek", "url": "https://www.seek.com.au/job/9"}, {"title": "linkedin", "url": "https://au.linkedin.com/jobs/view/1"},
             {"title": "adz", "url": "https://www.adzuna.com.au/land/ad/1"}, {"title": "http", "url": "http://insecure.test/j"}]
    assert [h["url"] for h in W.search_urls("it support melbourne", runner=runner(items))] == ["https://careers.cap.test/job/1"]


@pytest.mark.parametrize("q", ["; rm -rf ~", "a $(whoami) b", "x" * 200, "ab", "q`id`"])
def test_odd_search_text_is_refused_before_anything_runs(q):
    with pytest.raises(ValueError):
        W.search_urls(q, runner=lambda *a, **k: pytest.fail("must not run"))


def test_a_failing_search_reports_why():
    with pytest.raises(RuntimeError, match="nope"):
        W.search_urls("it support", runner=runner([], ok=False))


def fetcher(routes):
    def h(req):
        for k, v in routes.items():
            if str(req.url).split("?")[0].endswith(k):
                return v
        return httpx.Response(404, text="gone", headers={"content-type": "text/html"})
    async def nosleep(s): pass
    return PoliteFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(h)), delay=0, sleep=nosleep)


async def test_pages_become_listings_only_when_they_are_real_open_ads():
    ok = httpx.Response(200, text=JSONLD, headers={"content-type": "text/html"})
    f = fetcher({"careers.cap.test/job/1": ok, "careers.cap.test/robots.txt": httpx.Response(404),
                 "careers.cap.test/job/2": httpx.Response(200, text="<html><title>Careers</title>Welcome</html>", headers={"content-type": "text/html"})})
    hits = [{"title": "x", "url": "https://careers.cap.test/job/1"}, {"title": "y", "url": "https://careers.cap.test/job/2"},
            {"title": "z", "url": "https://careers.cap.test/job/3"}]
    raws, notes = await W.read_hits(f, hits, date(2026, 11, 10))
    [r] = raws
    assert (r.source, r.title, r.employer, r.location, r.description_complete) == ("web", "Service Desk Analyst", "Capgemini", "Melbourne", True)
    assert any("job/2" in n and "not a job ad" in n for n in notes) and any("job/3" in n and "closed" in n for n in notes)


async def test_ashby_board_is_read():
    def h(req):
        return httpx.Response(200, json={"jobs": [{"id": "a1", "title": "IT Support", "jobUrl": "https://jobs.ashbyhq.com/acme/a1", "location": "Melbourne",
                                                   "descriptionHtml": "<p>Help</p>", "publishedAt": "2026-11-04T00:00:00Z", "isListed": True}]}) if "ashbyhq" in str(req.url) else httpx.Response(404)
    async def nosleep(s): pass
    f = PoliteFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(h)), delay=0, sleep=nosleep)
    [p] = await feeds.ashby(f, "acme")
    assert (p.title, p.locations, p.source, p.open) == ("IT Support", ["Melbourne"], "feed:ashby", True)
    assert await feeds.ashby(PoliteFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))), delay=0, sleep=nosleep), "nope") is None


def test_landing_pages_are_not_jobs():
    for t in ("IT Jobs Melbourne | IT Support Careers at IT8", "Careers", "Jobs in Melbourne", "Careers at Acme", "Google IT Certificates - Grow with Google", "Cyber Security Short Course"):
        assert W.LANDING_RE.search(t), t
    for t in ("Service Desk Analyst", "IT Support Technician Level 1–2", "Junior Helpdesk Support – Apply Now – Careers @ Axiom IT"):
        assert not W.LANDING_RE.search(t), t
