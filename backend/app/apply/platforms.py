"""Which sites assisted form fill may touch. Employer ATS pages only; SEEK, LinkedIn and Indeed are refused by name."""
import re
from urllib.parse import urlparse

from app.core.config import settings

PLATFORMS = {
    "workable": ("apply.workable.com",),
    "greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"),
    "lever": ("jobs.lever.co", "jobs.eu.lever.co"),
    "smartrecruiters": ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"),
}
NEVER = ("seek.com.au", "seek.com", "linkedin.com", "indeed.com", "indeed.com.au", "glassdoor.com", "jora.com", "adzuna.com.au", "adzuna.com")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


class NotAllowed(ValueError):
    pass


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def test_hosts() -> set[str]:
    return {h.strip().lower() for h in settings.assist_test_hosts.split(",") if h.strip()}


def classify_url(url: str) -> str:
    """Return the platform name, or raise NotAllowed with a reason the user can read."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        raise NotAllowed("That is not a valid web address.") from None
    host = (u.hostname or "").lower()
    if not host or u.scheme not in ("http", "https"):
        raise NotAllowed("That is not a valid web address.")
    for bad in NEVER:
        if _host_matches(host, bad):
            raise NotAllowed(f"Assisted form fill never touches {bad.split('.')[0].title()} or other job boards. Apply there yourself.")
    if host in test_hosts():
        return "test"
    if u.scheme != "https":
        raise NotAllowed("Only secure (https) application pages are allowed.")
    for name, hosts in PLATFORMS.items():
        if any(_host_matches(host, h) for h in hosts):
            return name
    raise NotAllowed("Assisted form fill only works on Workable, Greenhouse, Lever and SmartRecruiters application pages.")


def find_ats_links(text: str) -> list[str]:
    """Allowed application-page links that appear in the ad text."""
    out: list[str] = []
    for m in URL_RE.finditer(text):
        link = m.group(0).rstrip(".,;:")
        try:
            classify_url(link)
        except NotAllowed:
            continue
        if link not in out:
            out.append(link)
    return out
