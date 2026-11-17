"""Find employer career pages with a generic web search (agent-reach, read-only), then read each page politely.
Only job-keyword queries leave this Mac: never your name, profile or documents. Job boards are never followed."""
import json
import re
import subprocess
from datetime import date
from urllib.parse import urlparse

from app.discovery.base import RawListing
from app.tracker.normalize import norm_url
from app.verify.fetch import Blocked, PoliteFetcher
from app.verify.resolve import is_job_board
from app.verify.verify import read_url

QUERY_RE = re.compile(r"^[\w\s,&/+#.\-–()]{3,120}$")
FULL_AD = 200
LANDING_RE = re.compile(r"^\s*(it |tech |\w+ )?jobs\b|\bjobs (in|at|near)\b|\bcareers? (at|page)\b|^\s*careers?\s*$|\b(certificates?|courses?|bootcamp|short course)\b", re.I)  # a listing, landing or course page, not one job


def search_urls(query: str, limit: int = 8, runner=subprocess.run) -> list[dict]:
    """[{title, url}] from `agent-reach web search`. The query is a plain job search; it is passed as one argument, never through a shell."""
    if not QUERY_RE.match(query):
        raise ValueError("The search text may only contain letters, numbers and simple punctuation.")
    r = runner(["agent-reach", "web", "search", query, "--limit", str(max(1, min(limit, 15)))], capture_output=True, text=True, timeout=120)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("agent-reach returned nothing readable") from None
    if not d.get("ok"):
        raise RuntimeError((d.get("error") or {}).get("hint") or "agent-reach could not search")
    out, seen = [], set()
    for i in d.get("items", []):
        u = i.get("url") or ""
        if urlparse(u).scheme != "https" or is_job_board(u) or norm_url(u) in seen:
            continue
        seen.add(norm_url(u))
        out.append({"title": (i.get("title") or "")[:200], "url": u})
    return out


def _employer_from(host: str, title: str) -> str | None:
    m = re.search(r"\|\s*([^|]{2,60})$|\bat\s+([A-Z][\w &.-]{1,50})$|[-–]\s*([A-Z][\w &.-]{1,50})$", title)
    if m:
        return next(g for g in m.groups() if g).strip()
    parts = [p for p in host.split(".") if p not in ("www", "careers", "jobs", "join", "com", "au", "org", "net", "co")]
    return parts[0].title() if parts else None


async def read_hits(f: PoliteFetcher, hits: list[dict], today: date) -> tuple[list[RawListing], list[str]]:
    """Read each page. Pages that are not a real, open job posting are skipped with a reason."""
    raws, notes = [], []
    for h in hits:
        try:
            p = await read_url(f, h["url"], today)
        except Blocked as e:
            notes.append(f"{h['url']}: {e}")
            continue
        if p.open is False:
            notes.append(f"{h['url']}: closed")
            continue
        title = (p.title or h["title"]).strip()
        if not title or LANDING_RE.search(title) or len(p.text or "") < FULL_AD or not re.search(r"\b(support|service desk|help ?desk|it |technician|analyst|desktop|systems?)\b", title, re.I):
            notes.append(f"{h['url']}: not a job ad with full text")
            continue
        host = urlparse(p.url).hostname or ""
        raws.append(RawListing("web", norm_url(p.url) or p.url, p.url, title[:300], p.employer or _employer_from(host, h["title"]), (p.locations[0] if p.locations else None),
                               p.text, True, p.posted, (p.employment or "").lower().replace("-", "_") or None, p.salary_min, p.salary_max, p.salary_period))
    return raws, notes
