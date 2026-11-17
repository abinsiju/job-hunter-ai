"""One canonical job record per real-world job, with a reference for every source it appeared on (nothing is ever deleted), manual import,
unified search with partial-failure handling, and the source status board."""
import asyncio
import ipaddress
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ImportHistory, Listing, SearchRun, SourceAudit, SourceConfig, SourceRef
from app.sources.base import NormalisedJob, REAL_DATA, SearchCriteria, SourceError, SourceResult, Status
from app.tracker.normalize import employers_match, fingerprint, norm_employer, norm_url
from app.verify.resolve import containment

SEARCH_TIMEOUT = 45
MAX_TEXT = 30000
SIMILAR = 0.85
ATS_HOSTS = {"workable": "apply.workable.com", "greenhouse": "greenhouse.io", "lever": "lever.co", "smartrecruiters": "smartrecruiters.com", "workday": "myworkdayjobs.com",
             "successfactors": "successfactors", "icims": "icims.com", "taleo": "taleo.net", "livehire": "livehire.com", "pageup": "pageuppeople.com", "ashby": "ashbyhq.com",
             "teamtailor": "teamtailor.com", "bamboohr": "bamboohr.com", "jobvite": "jobvite.com"}


def valid_url(url: str | None) -> str | None:
    """A web address we are willing to store: http(s), a real host, no embedded login, not a private network address. Else None."""
    if not url or len(url) > 2000:
        return None
    try:
        u = urlparse(url.strip())
        host = (u.hostname or "").lower()
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not host or u.username or u.password or "." not in host:
        return None
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return None
    except ValueError:
        if host == "localhost" or host.endswith((".local", ".internal")):
            return None
    return url.strip()


def ats_provider_of(url: str | None) -> str | None:
    host = (urlparse(url or "").hostname or "").lower()
    return next((n for n, h in ATS_HOSTS.items() if h in host), None)


def clean_text(t: str | None) -> str:
    """Imported text is untrusted: strip control characters and cap the size. It is only ever stored and scored, never executed or obeyed."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", (t or ""))[:MAX_TEXT]


def audit(s: Session, source: str, event: str, **detail) -> None:
    s.add(SourceAudit(source=source, event=event, detail=detail))


def _config(s: Session, source: str) -> SourceConfig:
    row = s.get(SourceConfig, source)
    if not row:
        row = SourceConfig(source=source)
        s.add(row)
        s.flush()
    return row


# ───────────── canonical job + source references ─────────────
def find_duplicate(s: Session, job: NormalisedJob) -> Listing | None:
    """Same job already stored? By the application URL, then employer + title + place, then (for full ads) near-identical wording."""
    key = norm_url(job.original_url)
    if key:
        for l in s.scalars(select(Listing).where(Listing.url.is_not(None))):
            if norm_url(l.url) == key:
                return l
    if job.employer:
        fp = fingerprint(job.employer, job.title, job.location)
        cands = [l for l in s.scalars(select(Listing).where(Listing.employer.is_not(None))) if employers_match(norm_employer(l.employer), norm_employer(job.employer))]
        for l in cands:
            if fingerprint(l.employer, l.title, l.location) == fp:
                return l
        if job.description_complete:
            for l in cands:
                if l.description_complete and containment(job.description[:1500], l.description or "") >= SIMILAR and containment((l.description or "")[:1500], job.description) >= SIMILAR:
                    return l
    return None


def canonical_upsert(s: Session, job: NormalisedJob) -> tuple[Listing, str]:
    """('created' | 'merged' | 'known'). A repeat appearance adds a reference to the existing canonical job; nothing is deleted or overwritten."""
    now = datetime.now(timezone.utc)
    ref = s.scalar(select(SourceRef).where(SourceRef.source == job.source, SourceRef.source_job_id == job.source_job_id))
    if ref:
        ref.last_seen_at = now
        l = s.get(Listing, ref.listing_id)
        l.last_seen = now
        return l, "known"
    dup = find_duplicate(s, job)
    if dup:
        dup.last_seen = now
        for attr, val in (("state", job.state), ("postcode", job.postcode), ("closing_date", job.closing_date), ("requirements", job.requirements), ("application_url", job.application_url),
                          ("ats_provider", job.ats_provider or ats_provider_of(job.application_url)), ("posted_at", job.date_posted)):
            if val and not getattr(dup, attr):
                setattr(dup, attr, val)  # fill gaps only
        if job.description_complete and not dup.description_complete and len(job.description) > len(dup.description or ""):
            dup.employer_description = dup.employer_description or None  # a fuller third-party text is kept as a reference, scored on the next verify
        s.add(SourceRef(listing_id=dup.id, source=job.source, source_job_id=job.source_job_id, url=job.original_url, method=job.source_method))
        s.flush()
        return dup, "merged"
    l = Listing(source=job.source, source_id=job.source_job_id, url=job.original_url, title=job.title, employer=job.employer, location=job.location,
                description=job.description, description_complete=job.description_complete, posted_at=job.date_posted, employment_hint=job.employment_type,
                salary_min=job.salary_min, salary_max=job.salary_max, salary_period=job.salary_period, salary_currency=job.salary_currency, source_method=job.source_method,
                permission_status=job.permission_status, state=job.state, postcode=job.postcode, requirements=job.requirements, closing_date=job.closing_date,
                application_url=job.application_url, ats_provider=job.ats_provider or ats_provider_of(job.application_url))
    if job.closing_date and job.closing_date < date.today():  # freshness: a passed closing date means closed
        l.open_status, l.closed_reason = "closed", f"The closing date ({job.closing_date.isoformat()}) has passed."
    s.add(l)
    s.flush()
    s.add(SourceRef(listing_id=l.id, source=job.source, source_job_id=job.source_job_id, url=job.original_url, method=job.source_method))
    s.flush()
    return l, "created"


# ───────────── manual import: the listing you copied from your own browser ─────────────
class ImportRejected(ValueError):
    pass


def manual_import(s: Session, registry: dict, source: str, *, text: str, url: str | None = None, title: str = "", employer: str = "", location: str = "",
                  application_url: str | None = None, closing_date: date | None = None) -> tuple[Listing, str]:
    conn = registry.get(source)
    if conn is None or not hasattr(conn, "is_own_url"):  # manual import is for the board sources you browse yourself
        raise ImportRejected(f"Manual import is available for SEEK, Indeed and Jora, not {source!r}.")
    text = clean_text(text).strip()
    if len(text) < 40:
        raise ImportRejected("Paste the job ad's text (at least a few sentences). Only a link is not enough to check or score a job.")
    clean_url = None
    if url:
        clean_url = valid_url(url)
        if not clean_url:
            raise ImportRejected("That is not a valid web address (use the full https:// link from your browser).")
        if hasattr(conn, "is_own_url") and not conn.is_own_url(clean_url):
            raise ImportRejected(f"That link is not a {conn.label} address. Choose the matching source or paste the right link.")
    app_url = valid_url(application_url) if application_url else None
    if application_url and not app_url:
        raise ImportRejected("The application link is not a valid web address.")
    t = (title or "").strip()
    if not t:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        t = first[:120]
    raw = {"url": clean_url or "", "title": t, "employer": employer.strip() or None, "location": location.strip() or None, "text": text, "application_url": app_url,
           "closing_date": closing_date}
    job = conn.normalise_job(raw)
    if not clean_url:
        job.original_url = f"manual://{source}/{job.source_job_id}"
    listing, outcome = canonical_upsert(s, job)
    s.add(ImportHistory(source=source, method="manual_import", url=clean_url, listing_id=listing.id, outcome=outcome))
    audit(s, source, "manual_import", listing_id=listing.id, outcome=outcome)
    _config(s, source).last_success_at = datetime.now(timezone.utc)
    s.commit()
    return listing, outcome


# ───────────── unified search ─────────────
async def _one(conn, criteria: SearchCriteria, timeout: float) -> SourceResult:
    t0 = time.monotonic()
    try:
        r = await asyncio.wait_for(conn.search_jobs(criteria), timeout)
    except asyncio.TimeoutError:
        return SourceResult(conn.name, Status.ERROR.value, False, error=f"{conn.label} did not answer in {int(timeout)} seconds", error_code="unavailable")
    except SourceError as e:
        return SourceResult(conn.name, Status.ERROR.value, False, error=str(e), error_code=e.code)
    except Exception as e:  # noqa: BLE001  (one broken source must never break the others)
        return SourceResult(conn.name, Status.ERROR.value, False, error=f"{conn.label} failed ({type(e).__name__})", error_code="unavailable")
    r.took_ms = r.took_ms or int((time.monotonic() - t0) * 1000)
    return r


async def unified_search(s: Session, registry: dict, criteria: SearchCriteria, *, timeout: float = SEARCH_TIMEOUT) -> dict:
    """Search every selected source at once. Sources that fetch data have their jobs stored (deduplicated); link-only sources just return their link.
    A failing source is reported and the others carry on."""
    wanted = [registry[n] for n in (criteria.sources or list(registry)) if n in registry and getattr(registry[n], "supports_search", True)]
    run = SearchRun(criteria=criteria.__dict__ | {"sources": [c.name for c in wanted]})
    s.add(run)
    s.flush()
    results = await asyncio.gather(*[_one(c, criteria, timeout) for c in wanted])
    out, new_ids = [], []
    for r in results:
        stored = {"created": 0, "merged": 0, "known": 0, "skipped_invalid": 0}
        for job in r.jobs:
            if not valid_url(job.original_url) or not job.title.strip():
                stored["skipped_invalid"] += 1
                continue
            listing, outcome = canonical_upsert(s, job)
            stored[outcome] += 1
            if outcome == "created":
                new_ids.append(listing.id)
        cfg = _config(s, r.source)
        if r.ok and r.fetched_data:
            cfg.last_success_at, cfg.last_error = datetime.now(timezone.utc), None
        elif r.ok and r.link:
            cfg.last_link_at = datetime.now(timezone.utc)
        elif not r.ok:
            cfg.last_error, cfg.last_error_at = (r.error or "failed")[:300], datetime.now(timezone.utc)
        audit(s, r.source, "search", ok=r.ok, status=r.status, fetched=len(r.jobs), stored=stored, error_code=r.error_code)
        out.append({"source": r.source, "status": r.status, "ok": r.ok, "fetched_data": r.fetched_data, "jobs_found": len(r.jobs), "stored": stored, "link": r.link,
                    "filters_applied": r.filters_applied, "filters_not_applied": r.filters_not_applied, "error": r.error, "error_code": r.error_code, "took_ms": r.took_ms,
                    "label": registry[r.source].label})
    run.results = {o["source"]: {k: o[k] for k in ("status", "ok", "jobs_found", "stored", "error_code")} for o in out}
    run.finished_at = datetime.now(timezone.utc)
    s.commit()
    return {"run_id": run.id, "sources": out, "new_listing_ids": new_ids,
            "summary": {"returned_data": [o["source"] for o in out if o["fetched_data"]], "link_only": [o["source"] for o in out if o["link"] and not o["fetched_data"]],
                        "failed": [o["source"] for o in out if not o["ok"]]}}


# ───────────── the status board ─────────────
async def source_board(s: Session, registry: dict) -> list[dict]:
    board = []
    for name, c in registry.items():
        info = await c.get_source_status()
        cfg = s.get(SourceConfig, name)
        refs = s.scalar(select(func.count()).select_from(SourceRef).where(SourceRef.source == name)) or 0
        last_import = s.scalar(select(func.max(ImportHistory.at)).where(ImportHistory.source == name))
        status = Status.DISABLED if (cfg and not cfg.enabled) else info.status
        board.append({"source": name, "label": info.label, "status": status.value, "methods": info.methods, "permission": info.permission, "permission_note": info.permission_note,
                      "auth": info.auth, "search_link": info.search_link, "manual_import": info.manual_import, "returns_data": info.returns_data and status in REAL_DATA,
                      "real_data_connection": status in REAL_DATA, "jobs_discovered": refs, "enabled": cfg.enabled if cfg else True, "notes": info.notes, "docs_url": info.docs_url,
                      "last_success_at": cfg.last_success_at.isoformat() if cfg and cfg.last_success_at else None,
                      "last_link_at": cfg.last_link_at.isoformat() if cfg and cfg.last_link_at else None,
                      "last_import_at": last_import.isoformat() if last_import else None,
                      "last_error": cfg.last_error if cfg else None, "last_error_at": cfg.last_error_at.isoformat() if cfg and cfg.last_error_at else None})
    return board
