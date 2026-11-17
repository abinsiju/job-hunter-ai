"""Search addresses for SEEK, Indeed and Jora, built from your criteria. These are LINKS you open in your own browser: nothing is fetched.
Each result says which filters made it into the link and which did not, so the page never promises more than the link does."""
from urllib.parse import quote_plus, urlencode

from app.sources.base import SearchCriteria

WORKTYPE_SEEK = {"full_time": "242", "part_time": "243", "contract": "244", "casual": "245"}
JT_INDEED = {"full_time": "fulltime", "part_time": "parttime", "contract": "contract", "casual": "temporary"}
LEVEL_WORDS = {"entry": "entry level", "junior": "junior", "graduate": "graduate"}
DAYS = (1, 3, 7, 14, 31)
RADII = (0, 5, 10, 15, 25, 35, 50, 100)
JORA_AGE = {1: "24h", 3: "3d", 7: "7d", 14: "14d", 31: "30d"}


def _near(n: int | None, allowed: tuple[int, ...]) -> int | None:
    return min(allowed, key=lambda x: abs(x - n)) if n else None


def _terms(c: SearchCriteria) -> str:
    extra = LEVEL_WORDS.get(c.level, "")
    return " ".join(x for x in (extra, c.terms(), c.industry, c.employer) if x).strip()


def seek_link(c: SearchCriteria) -> dict:
    p, ok, no = {"keywords": _terms(c)}, ["keywords"], []
    if c.place():
        p["where"] = c.place(); ok.append("location")
    if c.date_posted_days:
        p["daterange"] = str(_near(c.date_posted_days, DAYS)); ok.append("date posted")
    if c.employment_types:
        p["worktype"] = ",".join(WORKTYPE_SEEK[t] for t in c.employment_types if t in WORKTYPE_SEEK); ok.append("employment type")
    if c.salary_min or c.salary_max:
        p["salaryrange"] = f"{int(c.salary_min or 0)}-{int(c.salary_max or 0) or ''}"
        p["salarytype"] = "hourly" if c.salary_period == "hour" else "annual"; ok.append("salary")
    if c.distance_km:
        no.append("distance")
    return {"source": "seek", "url": "https://www.seek.com.au/jobs?" + urlencode(p), "filters_applied": ok, "filters_not_applied": no + _unsupported(c, ("experience", "work rights"))}


def indeed_link(c: SearchCriteria) -> dict:
    q = _terms(c)
    if c.salary_min and c.salary_period != "hour":
        q += f" ${int(c.salary_min):,}"
    p, ok, no = {"q": q}, ["keywords"], []
    if c.place():
        p["l"] = c.place(); ok.append("location")
    if c.distance_km:
        p["radius"] = str(_near(c.distance_km, RADII)); ok.append("distance")
    if c.date_posted_days:
        p["fromage"] = str(_near(c.date_posted_days, DAYS)); ok.append("date posted")
    types = [JT_INDEED[t] for t in c.employment_types if t in JT_INDEED]
    if types:
        p["jt"] = types[0]; ok.append("employment type" + (" (first only)" if len(types) > 1 else ""))
    if c.salary_min and c.salary_period != "hour":
        ok.append("salary (as a search word)")
    elif c.salary_min or c.salary_max:
        no.append("salary (hourly)")
    return {"source": "indeed", "url": "https://au.indeed.com/jobs?" + urlencode(p), "filters_applied": ok, "filters_not_applied": no + _unsupported(c, ("experience", "work rights"))}


def jora_link(c: SearchCriteria) -> dict:
    p, ok, no = {"q": _terms(c)}, ["keywords"], []
    if c.place():
        p["l"] = c.place(); ok.append("location")
    if c.distance_km:
        p["r"] = str(_near(c.distance_km, RADII)); ok.append("distance")
    if c.date_posted_days:
        p["a"] = JORA_AGE[_near(c.date_posted_days, DAYS)]; ok.append("date posted")
    if c.employment_types:
        no.append("employment type")
    if c.salary_min or c.salary_max:
        no.append("salary")
    return {"source": "jora", "url": "https://au.jora.com/j?" + urlencode(p), "filters_applied": ok, "filters_not_applied": no + _unsupported(c, ("experience", "work rights"))}


def _unsupported(c: SearchCriteria, which: tuple[str, ...]) -> list[str]:
    out = []
    if "experience" in which and c.experience_years:
        out.append("experience")
    if "work rights" in which and c.work_rights:
        out.append("work rights (checked by this app after you import)")
    return out


BUILDERS = {"seek": seek_link, "indeed": indeed_link, "jora": jora_link}
