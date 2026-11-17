"""Workable public jobs widget feed (https://apply.workable.com/api/v1/widget/accounts/<slug>), the documented
embeddable feed that employers publish on their own careers pages. Returns full descriptions."""
from datetime import date

import httpx

from app.discovery.base import USER_AGENT, RawListing, SourceError, html_to_text

FEED = "https://apply.workable.com/api/v1/widget/accounts/{slug}"
TYPES = {"full-time": "full_time", "part-time": "part_time", "contract": "contract", "casual": "casual", "temporary": "contract"}


class WorkableSource:
    def __init__(self, slug: str, employer: str, *, client: httpx.AsyncClient | None = None):
        self.slug, self.employer, self.name = slug, employer, f"workable:{slug}"
        self._client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)

    async def search(self, country: str = "Australia", city: str | None = None, title_contains: list[str] | None = None) -> list[RawListing]:
        try:
            r = await self._client.get(FEED.format(slug=self.slug), params={"details": "true"}, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise SourceError(f"Workable request failed ({type(e).__name__})") from None
        if r.status_code != 200:
            raise SourceError(f"Workable returned HTTP {r.status_code} for account '{self.slug}'")
        seen, out = set(), []
        for j in r.json().get("jobs", []):
            if country and (j.get("country") or "").lower() != country.lower():
                continue
            if city and (j.get("city") or "").lower() != city.lower():
                continue
            if title_contains and not any(t.lower() in (j.get("title") or "").lower() for t in title_contains):
                continue
            sid = f"{j['shortcode']}:{j.get('city') or ''}"  # the feed repeats a job once per location
            if sid in seen:
                continue
            seen.add(sid)
            pub = (j.get("published_on") or "")[:10]
            out.append(RawListing(
                source=self.name, source_id=sid, url=j.get("url", ""), title=j.get("title", ""), employer=self.employer,
                location=", ".join(x for x in (j.get("city"), j.get("state")) if x) or None, description=html_to_text(j.get("description")),
                description_complete=True, posted_at=date.fromisoformat(pub) if pub else None,
                employment_hint=TYPES.get((j.get("employment_type") or "").lower())))
        return out
