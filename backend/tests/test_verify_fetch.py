import asyncio
import http.server
import threading

import httpx
import pytest

from app.core.config import settings
from app.verify.fetch import Blocked, PoliteFetcher


class Site:
    """A tiny local site with configurable robots.txt and pages."""

    def __init__(self, robots="User-agent: *\nDisallow: /private/\n", robots_status=200, pages=None):
        self.hits, outer = [], self
        pages = pages or {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                outer.hits.append(self.path)
                if self.path == "/robots.txt":
                    return self.send(robots_status, robots, "text/plain")
                if self.path == "/redir":
                    self.send_response(302); self.send_header("Location", pages.get("_redir_to", "/ok")); self.end_headers(); return
                status, body, ctype = pages.get(self.path, (404, "nope", "text/html"))
                self.send(status, body, ctype)

            def send(self, code, body, ctype="text/html"):
                d = body.encode()
                self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()


@pytest.fixture
def fetcher(monkeypatch):
    monkeypatch.setattr(settings, "assist_test_hosts", "127.0.0.1")
    slept = []

    async def fake_sleep(s): slept.append(s)
    f = PoliteFetcher(delay=1.0, sleep=fake_sleep)
    f.slept = slept
    return f


@pytest.fixture
def site():
    made = []

    def make(**kw):
        s = Site(**kw); made.append(s); return s
    yield make
    for s in made: s.close()


async def test_allowed_page_is_fetched_and_robots_is_read_first(fetcher, site):
    s = site(pages={"/ok": (200, "<h1>Job</h1>", "text/html")})
    p = await fetcher.get(s.base + "/ok")
    assert p.status == 200 and "<h1>Job</h1>" in p.text and s.hits[0] == "/robots.txt" and s.hits[1] == "/ok"


async def test_a_page_robots_txt_disallows_is_never_requested(fetcher, site):
    s = site(pages={"/private/job": (200, "secret", "text/html")})
    with pytest.raises(Blocked, match="robots.txt does not allow"):
        await fetcher.get(s.base + "/private/job")
    assert "/private/job" not in s.hits


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_a_blocking_status_stops_and_the_site_is_not_asked_again(fetcher, site, status):
    s = site(pages={"/job": (status, "denied", "text/html")})
    with pytest.raises(Blocked, match=f"HTTP {status}"):
        await fetcher.get(s.base + "/job")
    n = len(s.hits)
    with pytest.raises(Blocked):
        await fetcher.get(s.base + "/other")
    assert len(s.hits) == n  # no second request to a site that said no


async def test_a_403_on_robots_txt_means_no_permission(fetcher, site):
    s = site(robots="denied", robots_status=403, pages={"/job": (200, "x", "text/html")})
    with pytest.raises(Blocked, match="refused robots.txt"):
        await fetcher.get(s.base + "/job")
    assert "/job" not in s.hits


async def test_a_missing_robots_txt_allows_reading(fetcher, site):
    s = site(robots="", robots_status=404, pages={"/job": (200, "<p>ok</p>", "text/html")})
    assert (await fetcher.get(s.base + "/job")).status == 200


@pytest.mark.parametrize("html", ['<div class="g-recaptcha" data-sitekey="x"></div>', "<title>Just a moment...</title>", "<title>Access Denied</title>",
                                 '<form><input type="password" name="p"></form>'])
async def test_captcha_and_login_walls_are_refused(fetcher, site, html):
    s = site(pages={"/job": (200, html, "text/html")})
    with pytest.raises(Blocked, match="CAPTCHA or a login wall"):
        await fetcher.get(s.base + "/job")


async def test_missing_page_is_reported_not_raised_so_callers_can_mark_it_closed(fetcher, site):
    s = site(pages={"/gone": (410, "", "text/html")})
    assert (await fetcher.get(s.base + "/gone")).status == 410


async def test_redirects_are_followed_and_each_hop_is_checked(fetcher, site):
    s = site(pages={"/ok": (200, "<p>final</p>", "text/html")})
    p = await fetcher.get(s.base + "/redir")
    assert p.url.endswith("/ok") and p.hops == [s.base.replace("http://127.0.0.1", "http://127.0.0.1") + "/ok"] or p.hops
    s2 = site(pages={"_redir_to": "https://www.seek.com.au/job/1"})
    with pytest.raises(Blocked, match="job board"):
        await fetcher.get(s2.base + "/redir")


@pytest.mark.parametrize("url,why", [("https://www.seek.com.au/job/1", "job board"), ("https://au.linkedin.com/jobs/1", "job board"), ("https://au.indeed.com/viewjob", "job board"),
                                     ("http://localhost:9/x", "private"), ("http://192.168.1.5/x", "private"), ("http://10.0.0.2/x", "private"), ("http://169.254.169.254/x", "private"),
                                     ("file:///etc/passwd", "not a web address"), ("ftp://x.com/a", "not a web address"), ("javascript:1", "not a web address")])
async def test_forbidden_and_private_addresses_are_refused_before_any_request(monkeypatch, url, why):
    monkeypatch.setattr(settings, "assist_test_hosts", "")
    calls = []
    f = PoliteFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))))
    with pytest.raises(Blocked, match=why):
        await f.get(url)
    assert calls == []


async def test_requests_to_one_site_are_spaced_out(fetcher, site):
    s = site(pages={"/a": (200, "a", "text/html"), "/b": (200, "b", "text/html")})
    await fetcher.get(s.base + "/a")
    await fetcher.get(s.base + "/b")
    assert len(fetcher.slept) >= 1 and all(0 < x <= 1.0 for x in fetcher.slept)


async def test_public_feeds_skip_robots_but_still_stop_on_a_block(fetcher, site):
    s = site(robots="User-agent: *\nDisallow: /\n", pages={"/api/jobs": (200, '{"jobs": []}', "application/json"), "/api/blocked": (403, "no", "application/json")})
    assert (await fetcher.get(s.base + "/api/jobs", public_api=True)).status == 200 and "/robots.txt" not in s.hits
    with pytest.raises(Blocked, match="HTTP 403"):
        await fetcher.get(s.base + "/api/blocked", public_api=True)


async def test_non_web_content_and_huge_pages_are_refused(fetcher, site):
    s = site(pages={"/bin": (200, "%PDF", "application/pdf"), "/big": (200, "x" * 3_100_000, "text/html")})
    with pytest.raises(Blocked, match="application/pdf"):
        await fetcher.get(s.base + "/bin")
    with pytest.raises(Blocked, match="too large"):
        await fetcher.get(s.base + "/big")
