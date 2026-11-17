"""Job sources. Every source says plainly HOW it connects (api / authorised feed / public pages / browser search / manual import), what it is
PERMITTED to do, and whether it actually returned data. A search shortcut is never presented as a connection."""
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Protocol


class Status(str, Enum):
    API_CONNECTED = "API_CONNECTED"
    FEED_CONNECTED = "FEED_CONNECTED"
    PUBLIC_PAGES = "PUBLIC_PAGES"  # employer pages and feeds that publish their own jobs and allow reading (robots.txt)
    MANUAL_IMPORT = "MANUAL_IMPORT"
    SEARCH_LINK_ONLY = "SEARCH_LINK_ONLY"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


REAL_DATA = {Status.API_CONNECTED, Status.FEED_CONNECTED, Status.PUBLIC_PAGES}  # only these ever return listings by themselves


class SourceError(Exception):
    """A source failed. `code` is one of: unavailable, invalid_credentials, rate_limited, not_permitted, bad_input."""

    def __init__(self, code: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


@dataclass
class SearchCriteria:
    keywords: str = ""
    title: str = ""
    location: str = ""  # free text: "Melbourne", "Melbourne VIC"
    city: str = ""
    suburb: str = ""
    state: str = ""
    distance_km: int | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_period: str = "year"  # year | hour
    employment_types: list[str] = field(default_factory=list)  # full_time part_time casual contract
    level: str = ""  # entry | junior | graduate | mid | senior
    experience_years: int | None = None
    industry: str = ""
    date_posted_days: int | None = None
    work_rights: str = ""  # informational: used for the gate afterwards, never sent to a board
    employer: str = ""
    sources: list[str] = field(default_factory=list)  # empty = all

    def terms(self) -> str:
        return " ".join(x for x in (self.title or "", self.keywords or "") if x).strip()

    def place(self) -> str:
        return " ".join(x for x in (self.suburb, self.city or self.location, self.state) if x).strip()


@dataclass
class NormalisedJob:
    """The one shared job shape. Anything a source did not give stays None: nothing is guessed."""
    source: str
    source_job_id: str
    title: str
    original_url: str
    employer: str | None = None
    location: str | None = None
    state: str | None = None
    postcode: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = "AUD"
    salary_period: str = "unknown"
    employment_type: str | None = None
    description: str = ""
    requirements: str | None = None
    date_posted: date | None = None
    closing_date: date | None = None
    source_method: str = "api"
    permission_status: str = "permitted"
    description_complete: bool = False
    application_url: str | None = None
    ats_provider: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


@dataclass
class SourceInfo:
    source: str
    label: str
    status: Status
    methods: list[str]  # api | authorised_feed | public_pages | browser_search | manual_import
    permission: str  # permitted | approval_required | no_permission | user_supplied_only
    permission_note: str
    auth: str  # none_needed | not_configured | configured | invalid
    search_link: bool
    manual_import: bool
    returns_data: bool  # can this source fetch listings by itself?
    notes: list[str] = field(default_factory=list)
    docs_url: str = ""


@dataclass
class SourceResult:
    source: str
    status: str
    ok: bool
    jobs: list[NormalisedJob] = field(default_factory=list)
    link: str | None = None
    filters_applied: list[str] = field(default_factory=list)
    filters_not_applied: list[str] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    took_ms: int = 0
    fetched_data: bool = False  # True only if the source itself returned listings


class JobSourceConnector(Protocol):
    name: str

    async def health_check(self) -> dict: ...
    async def search_jobs(self, criteria: SearchCriteria) -> SourceResult: ...
    async def get_job(self, source_job_id: str) -> NormalisedJob | None: ...
    def normalise_job(self, raw: dict) -> NormalisedJob: ...
    async def get_source_status(self) -> SourceInfo: ...
