"""Adzuna job search API (https://developer.adzuna.com). Needs a free app_id/app_key.

The API returns only a SNIPPET of each description (its docs say so), so every listing is marked incomplete and can
never pass the gate on its own. We never fetch the redirect_url page: the person opens it and pastes the full ad.
"""
import asyncio
from datetime import date

import httpx

from app.core.logging import quiet_http_loggers
from app.discovery.base import USER_AGENT, RawListing, SourceError, html_to_text

quiet_http_loggers()  # Adzuna requires app_key in the URL: never let a library log it

BASE = "https://api.adzuna.com/v1/api/jobs"
HINTS = {"permanent": None, "contract": "contract"}


class AdzunaSource:
    name = "adzuna"

    def __init__(self, app_id: str, app_key: str, *, country: str = "au", client: httpx.AsyncClient | None = None, delay: float = 1.0):
        if not app_id or not app_key:
            raise SourceError("Adzuna needs ADZUNA_APP_ID and ADZUNA_APP_KEY in .env (free keys: https://developer.adzuna.com/signup)")
        self._id, self._key, self.country, self.delay = app_id, app_key, country, delay
        self._client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=30)

    async def search(self, what: str, where: str = "Melbourne", pages: int = 1, per_page: int = 20, max_days_old: int | None = None,
                     full_time: bool = False, part_time: bool = False, sort_by: str = "date") -> list[RawListing]:
        out: list[RawListing] = []
        for page in range(1, pages + 1):
            params = {"app_id": self._id, "app_key": self._key, "results_per_page": per_page, "what": what, "where": where,
                      "sort_by": sort_by, "content-type": "application/json"}
            if max_days_old:
                params["max_days_old"] = max_days_old
            if full_time:
                params["full_time"] = 1
            if part_time:
                params["part_time"] = 1
            try:
                r = await self._client.get(f"{BASE}/{self.country}/search/{page}", params=params, headers={"Accept": "application/json"})
            except httpx.HTTPError as e:  # message deliberately omitted: it would contain the URL with the key
                raise SourceError(f"Adzuna request failed ({type(e).__name__})") from None
            if r.status_code != 200:
                hint = {401: " (check ADZUNA_APP_ID / ADZUNA_APP_KEY)", 403: " (check the keys, or the plan's limits)",
                        429: " (rate limited: wait and retry, or reduce --pages)"}.get(r.status_code, "")
                raise SourceError(f"Adzuna returned HTTP {r.status_code}{hint}")
            results = r.json().get("results", [])
            out += [self._parse(j) for j in results]
            if len(results) < per_page:
                break
            if page < pages:
                await asyncio.sleep(self.delay)  # polite: one request per second
        return out

    @staticmethod
    def _parse(j: dict) -> RawListing:
        predicted = str(j.get("salary_is_predicted", 0)) == "1"
        hint = {"full_time": "full_time", "part_time": "part_time"}.get(j.get("contract_time") or "")
        hint = HINTS.get(j.get("contract_type") or "") or hint
        created = (j.get("created") or "")[:10]
        return RawListing(
            source="adzuna", source_id=str(j["id"]), url=j.get("redirect_url", ""), title=html_to_text(j.get("title")),
            employer=(j.get("company") or {}).get("display_name"), location=(j.get("location") or {}).get("display_name"),
            description=html_to_text(j.get("description")), description_complete=False,
            posted_at=date.fromisoformat(created) if created else None, employment_hint=hint,
            # a PREDICTED salary is Adzuna's guess, not the employer's: never use it for pay checks
            salary_min=None if predicted else j.get("salary_min"), salary_max=None if predicted else j.get("salary_max"),
            salary_period="unknown" if predicted or j.get("salary_min") is None else "year")
