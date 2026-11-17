"""A polite fetcher. It reads a public page or feed only if robots.txt allows it, identifies itself honestly, waits between requests to the
same site, and STOPS (raises Blocked) on a 401/403/429/503, a CAPTCHA or a login wall. It never fetches job boards that forbid bots,
private addresses, or anything that is not a web page."""
import asyncio
import ipaddress
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from app.apply.platforms import test_hosts
from app.discovery.base import USER_AGENT

JOB_BOARDS = ("seek.com.au", "seek.com", "linkedin.com", "indeed.com", "indeed.com.au", "glassdoor.com", "jora.com", "careerone.com.au")
BLOCK_STATUSES = {401, 403, 407, 429, 451, 503}
CONTENT_OK = ("text/html", "application/xhtml", "application/json", "application/ld+json", "text/plain")
CAPTCHA_RE = re.compile(r"g-recaptcha|h-captcha|cf-turnstile|cf-chl|px-captcha|<title>\s*(just a moment|access denied|attention required)", re.I)
LOGIN_RE = re.compile(r'<input[^>]+type=["\']?password', re.I)
MAX_BYTES = 3_000_000
MAX_HOPS = 5


class Blocked(Exception):
    """The site does not allow (or cannot give) a plain automated read. The reason is shown to the user."""


@dataclass
class Page:
    url: str
    status: int
    text: str
    content_type: str
    hops: list[str] = field(default_factory=list)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _refuse(url: str) -> str | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        return "not a web address"
    if host in test_hosts():
        return None
    if any(host == b or host.endswith("." + b) for b in JOB_BOARDS):
        return f"{host} is a job board that does not allow automated reading"
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return "private address"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return "private address"
    except ValueError:
        pass
    return None


class PoliteFetcher:
    def __init__(self, client: httpx.AsyncClient | None = None, *, delay: float = 1.0, sleep=asyncio.sleep):
        self._client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5"}, timeout=25)
        self.delay, self._sleep = delay, sleep
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | Blocked] = {}
        self._blocked_hosts: dict[str, str] = {}  # a site that blocked us once is not asked again this run
        self.log: list[str] = []  # human-readable record of what was tried

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _wait(self, host: str) -> None:
        gap = self.delay - (time.monotonic() - self._last.get(host, -1e9))
        if gap > 0:
            await self._sleep(gap)
        self._last[host] = time.monotonic()

    async def _robots_ok(self, url: str) -> None:
        u = urlparse(url)
        origin = f"{u.scheme}://{u.netloc}"
        if origin not in self._robots:
            await self._wait(_host(url))
            try:
                r = await self._client.get(origin + "/robots.txt", follow_redirects=True)
            except httpx.HTTPError:
                self._robots[origin] = Blocked(f"could not read {u.netloc}/robots.txt")
            else:
                if r.status_code in (404, 410):
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse([])
                    self._robots[origin] = rp
                elif r.status_code == 200:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(r.text.splitlines())
                    self._robots[origin] = rp
                else:  # 401/403/5xx on robots.txt: the site is not giving permission
                    self._robots[origin] = Blocked(f"{u.netloc} refused robots.txt (HTTP {r.status_code}), so it does not allow automated reading")
        rp = self._robots[origin]
        if isinstance(rp, Blocked):
            raise rp
        if not rp.can_fetch(USER_AGENT, url):
            raise Blocked(f"{u.netloc} robots.txt does not allow reading {u.path or '/'}")

    async def get(self, url: str, *, public_api: bool = False) -> Page:
        """Fetch one URL. `public_api` is for documented public JSON feeds (robots.txt is for crawlers); everything else obeys robots.txt."""
        hops: list[str] = []
        current = url
        for _ in range(MAX_HOPS + 1):
            why = _refuse(current)
            if why:
                raise Blocked(why)
            host = _host(current)
            if host in self._blocked_hosts:
                raise Blocked(self._blocked_hosts[host])
            try:
                if not public_api:
                    await self._robots_ok(current)
                await self._wait(host)
                r = await self._client.get(current, follow_redirects=False)
            except Blocked as e:
                self._blocked_hosts[host] = str(e)
                self.log.append(f"{host}: {e}")
                raise
            except httpx.HTTPError as e:
                raise Blocked(f"could not reach {host} ({type(e).__name__})") from None
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                current = urljoin(current, r.headers["location"])
                hops.append(current)
                continue
            if r.status_code in BLOCK_STATUSES:
                msg = f"{host} answered HTTP {r.status_code}: it does not allow automated reading"
                self._blocked_hosts[host] = msg
                self.log.append(msg)
                raise Blocked(msg)
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if r.status_code in (404, 410):
                return Page(current, r.status_code, "", ctype, hops)
            if r.status_code >= 400 or not any(ctype.startswith(c) for c in CONTENT_OK):
                raise Blocked(f"{host} returned HTTP {r.status_code} ({ctype or 'no content type'})")
            if len(r.content) > MAX_BYTES:
                raise Blocked("the page is too large")
            text = r.text
            if ctype.startswith("text/html") and (CAPTCHA_RE.search(text[:60000]) or (LOGIN_RE.search(text) and len(text) < 20000)):
                msg = f"{host} shows a CAPTCHA or a login wall"
                self._blocked_hosts[host] = msg
                self.log.append(msg)
                raise Blocked(msg)
            return Page(current, r.status_code, text, ctype, hops)
        raise Blocked("too many redirects")
