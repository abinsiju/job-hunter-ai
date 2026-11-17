"""The employer's own public job feeds (Greenhouse, Lever, Workable, SmartRecruiters). We guess a few board names from the employer's name
and read the board if it exists. Matching (title, employer, location, content) decides whether a posting really is the same vacancy."""
import html as htmllib
import json
import re

from app.discovery.base import html_to_text
from app.tracker.normalize import norm_employer
from app.verify.fetch import Blocked, Page, PoliteFetcher
from app.verify.posting import Posting, parse_date

MAX_SLUGS = 3


def slug_variants(employer: str) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]+", norm_employer(employer))]
    if not toks:
        return []
    out = ["".join(toks), "-".join(toks)]
    if len(toks) > 1 and len(toks[0]) >= 4:
        out.append(toks[0])
    return list(dict.fromkeys(s for s in out if len(s) >= 3))[:MAX_SLUGS]


def _json(page: Page):
    if page.status in (404, 410) or not page.text:
        return None
    try:
        return json.loads(page.text)
    except ValueError:
        return None


async def greenhouse(f: PoliteFetcher, slug: str) -> list[Posting] | None:
    data = _json(await f.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", public_api=True))
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    name = None
    if data["jobs"]:
        board = _json(await f.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", public_api=True))
        name = (board or {}).get("name")
    return [Posting(title=j.get("title", ""), url=j.get("absolute_url", ""), text=html_to_text(htmllib.unescape(j.get("content") or "")), employer=name,
                    locations=[(j.get("location") or {}).get("name")] if (j.get("location") or {}).get("name") else [],
                    posted=parse_date(j.get("first_published") or j.get("updated_at")), job_id=str(j.get("id")), apply_url=j.get("absolute_url"),
                    source="feed:greenhouse", open=True) for j in data["jobs"]]


async def lever(f: PoliteFetcher, slug: str) -> list[Posting] | None:
    data = _json(await f.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", public_api=True))
    if not isinstance(data, list):
        return None
    out = []
    for j in data:
        cats = j.get("categories") or {}
        text = "\n".join(filter(None, [j.get("descriptionPlain"), *[f"{x.get('text', '')}\n{html_to_text(x.get('content', ''))}" for x in j.get("lists", [])], j.get("additionalPlain")]))
        out.append(Posting(title=j.get("text", ""), url=j.get("hostedUrl", ""), text=text, employer=None, locations=[cats["location"]] if cats.get("location") else [],
                           remote=str(j.get("workplaceType", "")).lower() == "remote", posted=parse_date(j.get("createdAt")), employment=cats.get("commitment"), job_id=j.get("id"),
                           apply_url=j.get("applyUrl"), source="feed:lever", open=True))
    return out


async def workable(f: PoliteFetcher, slug: str) -> list[Posting] | None:
    data = _json(await f.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", public_api=True))
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    return [Posting(title=j.get("title", ""), url=j.get("url", ""), text=html_to_text(j.get("description") or ""), employer=data.get("name"),
                    locations=[", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)], posted=parse_date(j.get("published_on")),
                    employment=j.get("employment_type"), job_id=j.get("shortcode"), apply_url=j.get("application_url") or j.get("url"), source="feed:workable", open=True)
            for j in data["jobs"]]


async def smartrecruiters(f: PoliteFetcher, slug: str) -> list[Posting] | None:
    data = _json(await f.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100", public_api=True))
    if not isinstance(data, dict) or "content" not in data:
        return None
    out = []
    for j in data["content"]:
        loc = j.get("location") or {}
        out.append(Posting(title=j.get("name", ""), url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", employer=(j.get("company") or {}).get("name"),
                           locations=[", ".join(x for x in (loc.get("city"), loc.get("region")) if x)], remote=bool(loc.get("remote")), posted=parse_date(j.get("releasedDate")),
                           job_id=j.get("id"), source="feed:smartrecruiters", open=True,
                           detail_url=f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j.get('id')}"))
    return out


async def smartrecruiters_detail(f: PoliteFetcher, p: Posting) -> Posting:
    data = _json(await f.get(p.detail_url, public_api=True)) if p.detail_url else None
    sections = ((data or {}).get("jobAd") or {}).get("sections") or {}
    p.text = "\n\n".join(html_to_text(s.get("text", "")) for s in sections.values() if isinstance(s, dict))
    p.apply_url = (data or {}).get("applyUrl") or p.url
    p.url = (data or {}).get("postingUrl") or p.url
    p.detail_url = None
    return p


async def ashby(f: PoliteFetcher, slug: str) -> list[Posting] | None:
    data = _json(await f.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", public_api=True))
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    return [Posting(title=j.get("title", ""), url=j.get("jobUrl", ""), text=html_to_text(j.get("descriptionHtml") or j.get("descriptionPlain") or ""), employer=None,
                    locations=[j["location"]] if j.get("location") else [], remote=bool(j.get("isRemote")), posted=parse_date(j.get("publishedAt")),
                    employment=j.get("employmentType"), job_id=j.get("id"), apply_url=j.get("applyUrl") or j.get("jobUrl"), source="feed:ashby",
                    open=j.get("isListed", True)) for j in data["jobs"]]


FEEDS = {"ashby": ashby, "greenhouse": greenhouse, "lever": lever, "workable": workable, "smartrecruiters": smartrecruiters}


async def search_employer(f: PoliteFetcher, employer: str, cache: dict | None = None) -> tuple[list[tuple[Posting, str]], list[str]]:
    """All current postings from the employer's public feeds, as (posting, board_slug). Plus notes on what was tried."""
    cache = cache if cache is not None else {}
    found: list[tuple[Posting, str]] = []
    notes: list[str] = []
    for slug in slug_variants(employer):
        for name, feed in FEEDS.items():
            key = (name, slug)
            if key not in cache:
                try:
                    cache[key] = await feed(f, slug)
                except Blocked as e:
                    cache[key] = None
                    notes.append(f"{name}/{slug}: {e}")
            if cache[key]:
                found += [(p, slug) for p in cache[key]]
                notes.append(f"{name}/{slug}: board found, {len(cache[key])} postings")
    return found, notes
