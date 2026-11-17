"""Where the assistant may go. It follows the person's own Apply path to the EMPLOYER's pages and any applicant-tracking system the employer uses.
It never opens job boards, never leaves https (tests excepted), and never touches private network addresses."""
import ipaddress
from urllib.parse import urlparse

from app.apply.platforms import NEVER, NotAllowed, PLATFORMS, _host_matches, test_hosts


def check_url(url: str) -> str:
    """Returns the host class ('test' | 'ats:<name>' | 'employer') or raises NotAllowed with a readable reason."""
    try:
        u = urlparse((url or "").strip())
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
    private = host == "localhost" or host.endswith((".local", ".internal", ".localhost"))
    try:  # NotAllowed is a ValueError, so the address parse must not share a try block with the refusal
        ip = ipaddress.ip_address(host)
        private = ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified
    except ValueError:
        pass
    if private:
        raise NotAllowed("Private network addresses are never opened.")
    for name, hosts in PLATFORMS.items():
        if any(_host_matches(host, h) for h in hosts):
            return f"ats:{name}"
    return "employer"
