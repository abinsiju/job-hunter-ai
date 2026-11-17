"""The connectors. SEEK, Indeed and Jora are LINK + MANUAL IMPORT sources: that is all their terms and APIs allow a personal tool to do today.
Each keeps a slot for official credentials, but calls nothing with them until a documented, permitted job-discovery interface exists."""
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.discovery.adzuna import AdzunaSource
from app.discovery.base import RawListing, SourceError as DiscoveryError
from app.discovery.workable import WorkableSource
from app.sources.base import NormalisedJob, SearchCriteria, SourceError, SourceInfo, SourceResult, Status
from app.sources.links import BUILDERS

STATE_RE = re.compile(r"\b(VIC|NSW|QLD|SA|WA|TAS|NT|ACT)\b")
POSTCODE_RE = re.compile(r"\b(\d{4})\b")


def split_place(place: str | None) -> tuple[str | None, str | None]:
    """(state, postcode) only when the text actually says so."""
    if not place:
        return None, None
    st, pc = STATE_RE.search(place), POSTCODE_RE.search(place)
    return (st.group(1) if st else None), (pc.group(1) if pc else None)


def from_raw(raw: RawListing, *, method: str, permission: str) -> NormalisedJob:
    state, pc = split_place(raw.location)
    return NormalisedJob(source=raw.source.split(":")[0] if raw.source.startswith("adzuna") else raw.source, source_job_id=raw.source_id, title=raw.title, original_url=raw.url,
                         employer=raw.employer, location=raw.location, state=state, postcode=pc, salary_min=raw.salary_min, salary_max=raw.salary_max,
                         salary_period=raw.salary_period, employment_type=raw.employment_hint, description=raw.description or "", date_posted=raw.posted_at,
                         source_method=method, permission_status=permission, description_complete=raw.description_complete,
                         first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc))


# ───────────── SEEK / Indeed / Jora: browser search link + manual import ─────────────
class LinkSource:
    name = ""
    label = ""
    domains: tuple[str, ...] = ()
    id_patterns: tuple[str, ...] = ()
    permission = "no_permission"
    permission_note = ""
    docs_url = ""
    cred_keys: tuple[str, ...] = ()
    notes: list[str] = []
    supports_search = True

    def credentials_set(self) -> bool:
        return all(getattr(settings, k, "") for k in self.cred_keys) if self.cred_keys else False

    async def health_check(self) -> dict:
        return {"ok": True, "connected": False, "detail": "Nothing to check: this source offers a search link and manual import only."}

    async def search_jobs(self, criteria: SearchCriteria) -> SourceResult:
        t0 = time.monotonic()
        link = BUILDERS[self.name](criteria)
        return SourceResult(source=self.name, status=Status.SEARCH_LINK_ONLY.value, ok=True, jobs=[], link=link["url"], filters_applied=link["filters_applied"],
                            filters_not_applied=link["filters_not_applied"], fetched_data=False, took_ms=int((time.monotonic() - t0) * 1000))

    async def get_job(self, source_job_id: str) -> NormalisedJob | None:
        return None  # fetching a single listing by machine is not permitted: you copy it from your own browser

    def is_own_url(self, url: str) -> bool:
        try:
            u = urlparse(url)
        except ValueError:
            return False
        host = (u.hostname or "").lower()
        return u.scheme in ("http", "https") and any(host == d or host.endswith("." + d) for d in self.domains)

    def job_id_from(self, url: str) -> str | None:
        for pat in self.id_patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return None

    def normalise_job(self, raw: dict) -> NormalisedJob:
        """A listing YOU copied from this site. Nothing is filled in that you did not provide."""
        url = (raw.get("url") or "").strip()
        sid = raw.get("source_job_id") or (self.job_id_from(url) if url else None) or f"manual-{abs(hash((raw.get('title'), raw.get('employer'), raw.get('text', '')[:80])))}"
        state, pc = split_place(raw.get("location"))
        now = datetime.now(timezone.utc)
        return NormalisedJob(source=self.name, source_job_id=str(sid), title=(raw.get("title") or "").strip()[:300], original_url=url, employer=(raw.get("employer") or None),
                             location=raw.get("location") or None, state=state, postcode=pc, description=raw.get("text") or "", requirements=raw.get("requirements"),
                             salary_min=raw.get("salary_min"), salary_max=raw.get("salary_max"), salary_period=raw.get("salary_period") or "unknown",
                             employment_type=raw.get("employment_type"), date_posted=raw.get("date_posted"), closing_date=raw.get("closing_date"),
                             source_method="manual_import", permission_status="user_supplied", description_complete=len(raw.get("text") or "") >= 400,
                             application_url=raw.get("application_url") or None, first_seen_at=now, last_seen_at=now)

    async def get_source_status(self) -> SourceInfo:
        creds = self.credentials_set()
        notes = list(self.notes)
        if creds:
            notes.append("Credentials are stored, but nothing is called with them: no documented job-discovery interface is available to this app.")
        return SourceInfo(self.name, self.label, Status.SEARCH_LINK_ONLY, ["browser_search", "manual_import"], self.permission, self.permission_note,
                          "configured" if creds else "not_configured", True, True, False, notes, self.docs_url)


class SeekConnector(LinkSource):
    name, label = "seek", "SEEK"
    domains = ("seek.com.au", "seek.com")
    id_patterns = (r"/job/(\d{5,})",)
    permission = "approval_required"
    permission_note = "SEEK's API is for approved recruitment-software partners and has no candidate job search. Automated browsing for listings is not permitted by its terms."
    docs_url = "https://developer.seek.com/"
    cred_keys = ("seek_client_id", "seek_client_secret")
    notes = ["Open the search in your own browser, then copy a listing you like and import it here.", "No partnership request has been made."]


class IndeedConnector(LinkSource):
    name, label = "indeed", "Indeed"
    domains = ("indeed.com", "au.indeed.com")
    id_patterns = (r"[?&]jk=([0-9a-fA-F]{8,})", r"[?&]vjk=([0-9a-fA-F]{8,})")
    permission = "no_permission"
    permission_note = "Indeed's APIs (Job Sync, Indeed Apply and others) are for employers and applicant-tracking partners. Automating Indeed Apply is prohibited."
    docs_url = "https://docs.indeed.com/"
    cred_keys = ("indeed_client_id", "indeed_client_secret")
    notes = ["This app never automates Indeed Apply. Apply on Indeed yourself, or follow the listing to the employer's own site."]


class JoraConnector(LinkSource):
    name, label = "jora", "Jora"
    domains = ("jora.com", "au.jora.com")
    id_patterns = (r"/job/[^?#]*?-([0-9a-fA-F]{8,})(?:[?#]|$)", r"/job/([^/?#]+)")
    permission = "no_permission"
    permission_note = "Jora's terms prohibit robots, scraping and automated data extraction without written permission. No feed or partner programme for job seekers was found."
    docs_url = "https://au.jora.com/cms/terms-of-service"
    cred_keys = ("jora_feed_url",)
    notes = ["Written permission from Jora would be needed before any automated reading."]


# ───────────── sources that really return data ─────────────
class AdzunaConnector:
    name, label = "adzuna", "Adzuna"
    supports_search = True

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    def credentials_set(self) -> bool:
        return bool(settings.adzuna_app_id and settings.adzuna_app_key)

    async def health_check(self) -> dict:
        return {"ok": self.credentials_set(), "connected": self.credentials_set(), "detail": "API keys set" if self.credentials_set() else "ADZUNA_APP_ID / ADZUNA_APP_KEY not set"}

    async def search_jobs(self, c: SearchCriteria) -> SourceResult:
        t0 = time.monotonic()
        if not self.credentials_set():
            return SourceResult(self.name, Status.DISABLED.value, False, error="Adzuna keys are not set", error_code="invalid_credentials")
        src = AdzunaSource(settings.adzuna_app_id, settings.adzuna_app_key, client=self._client, delay=0)
        types = c.employment_types
        try:
            raws = await src.search(what=c.terms() or "it support", where=c.place() or "Melbourne", pages=1, max_days_old=c.date_posted_days or 14,
                                    full_time="full_time" in types, part_time="part_time" in types)
        except DiscoveryError as e:
            msg = str(e)
            code = "invalid_credentials" if re.search(r"HTTP (401|403)", msg) else "rate_limited" if "429" in msg else "unavailable"
            return SourceResult(self.name, Status.ERROR.value, False, error=msg, error_code=code)
        return SourceResult(self.name, Status.API_CONNECTED.value, True, [from_raw(r, method="api", permission="permitted") for r in raws], fetched_data=True,
                            filters_applied=["keywords", "location", "date posted", "employment type"], took_ms=int((time.monotonic() - t0) * 1000))

    async def get_job(self, source_job_id: str):
        return None

    def normalise_job(self, raw: dict) -> NormalisedJob:
        return from_raw(RawListing(**raw), method="api", permission="permitted")

    async def get_source_status(self) -> SourceInfo:
        ok = self.credentials_set()
        return SourceInfo(self.name, self.label, Status.API_CONNECTED if ok else Status.DISABLED, ["api"], "permitted", "Adzuna's documented public API, used with your own free keys.",
                          "configured" if ok else "not_configured", False, False, True,
                          ["Adzuna shares only a snippet of each ad, so its jobs can never show as a clear match until you read the full ad."], "https://developer.adzuna.com/")


class EmployerFeedConnector:
    """Employers' own public job boards (Workable feeds in the watch-list). Documented public JSON, no login."""
    name, label = "employer_feeds", "Employer feeds"
    supports_search = True

    def __init__(self, client: httpx.AsyncClient | None = None, boards: list[tuple[str, str]] | None = None):
        self._client = client
        self.boards = boards if boards is not None else [("first-focus", "First Focus")]

    async def health_check(self) -> dict:
        return {"ok": True, "connected": True, "detail": f"{len(self.boards)} employer board(s) in the watch-list"}

    async def search_jobs(self, c: SearchCriteria) -> SourceResult:
        t0 = time.monotonic()
        raws: list[RawListing] = []
        try:
            for slug, name in self.boards:
                raws += await WorkableSource(slug, name, client=self._client).search(country="Australia", city=c.city or c.location or None,
                                                                                      title_contains=[w for w in c.terms().split() if len(w) > 2] or None)
        except DiscoveryError as e:
            return SourceResult(self.name, Status.ERROR.value, False, error=str(e), error_code="unavailable")
        return SourceResult(self.name, Status.FEED_CONNECTED.value, True, [from_raw(r, method="feed", permission="permitted") for r in raws], fetched_data=True,
                            filters_applied=["keywords (title words)", "city"], took_ms=int((time.monotonic() - t0) * 1000))

    async def get_job(self, source_job_id: str):
        return None

    def normalise_job(self, raw: dict) -> NormalisedJob:
        return from_raw(RawListing(**raw), method="feed", permission="permitted")

    async def get_source_status(self) -> SourceInfo:
        return SourceInfo(self.name, self.label, Status.FEED_CONNECTED, ["authorised_feed"], "permitted",
                          "Employers publish these boards for anyone to read (documented public feeds, no login).", "none_needed", False, False, True,
                          [f"Boards: {', '.join(n for _, n in self.boards)}. Add more in app/discovery/career_pages.json."], "https://workable.readme.io/")


class EmployerPagesConnector:
    """Employer career pages found by web search and read politely (robots.txt, public pages only). Runs in the scheduled hunt, not in this search box."""
    name, label = "employer_pages", "Employer career pages"
    supports_search = False

    async def health_check(self) -> dict:
        return {"ok": True, "connected": True, "detail": "runs with the scheduled hunt (scripts/hunt.sh) or `discover web`"}

    async def search_jobs(self, c: SearchCriteria) -> SourceResult:
        return SourceResult(self.name, Status.PUBLIC_PAGES.value, True, [], error=None, fetched_data=False)

    async def get_job(self, source_job_id: str):
        return None

    def normalise_job(self, raw: dict) -> NormalisedJob:
        return from_raw(RawListing(**raw), method="web_page", permission="permitted")

    async def get_source_status(self) -> SourceInfo:
        return SourceInfo(self.name, self.label, Status.PUBLIC_PAGES, ["public_pages"], "permitted",
                          "Only employer pages that allow reading (robots.txt), never job boards, logins or CAPTCHAs.", "none_needed", False, False, True,
                          ["Runs automatically every 12 hours while your Mac is on (scripts/hunt.sh)."], "")


def build_registry(adzuna_client: httpx.AsyncClient | None = None, feed_client: httpx.AsyncClient | None = None) -> dict:
    cs = [SeekConnector(), IndeedConnector(), JoraConnector(), AdzunaConnector(adzuna_client), EmployerFeedConnector(feed_client), EmployerPagesConnector()]
    return {c.name: c for c in cs}
