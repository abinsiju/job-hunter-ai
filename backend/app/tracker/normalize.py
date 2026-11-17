"""Normalisation used to recognise the same vacancy however it is worded or wherever it was found."""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse

LEGAL = r"\b(pty|ltd|limited|group|holdings|the|inc|co|company|australia|australian)\b"
EMPLOYMENT_WORDS = r"\b(part[- ]time|full[- ]time|casual|permanent|temporary|contract|ongoing|fixed[- ]term)\b"
STATE_WORDS = r"\b(vic|victoria|nsw|new south wales|act|qld|queensland|wa|sa|tas|nt|australia)\b"
TRACKING_PARAMS = {"gclid", "fbclid", "ref", "source", "src", "trk", "campaign", "mc_cid", "mc_eid", "from", "referrer", "sid"}


def _squash(s: str) -> str:
    s = s.lower().replace("&", " and ")  # "Study & Work" and "Study and Work" are the same employer
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#]+", " ", s)).strip()


def norm_employer(s: str | None) -> str:
    return _squash(re.sub(LEGAL, " ", (s or "").lower()))


def norm_title(s: str | None) -> str:
    t = re.sub(r"[\(\[].*?[\)\]]", " ", (s or "").lower())  # "(Part-time, 20 hrs/week)"
    t = re.sub(EMPLOYMENT_WORDS, " ", t)
    t = re.split(r"\s[-–—|@]\s", t)[0]  # drop " - Company / location" tails
    return _squash(t)


def norm_location(s: str | None) -> str:
    return _squash(re.sub(STATE_WORDS, " ", (s or "").lower()))


def norm_url(url: str | None) -> str | None:
    """Host + path + non-tracking query, so the same listing URL always compares equal."""
    if not url or not url.strip():
        return None
    u = urlparse(url.strip())
    host = u.netloc.lower().removeprefix("www.")
    path = u.path.rstrip("/")
    q = sorted((k, v) for k, v in parse_qsl(u.query) if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_"))
    return f"{host}{path}" + (f"?{urlencode(q)}" if q else "")


def fingerprint(employer: str, title: str | None, location: str | None) -> str:
    return hashlib.sha256(f"{norm_employer(employer)}|{norm_title(title)}|{norm_location(location)}".encode()).hexdigest()


def locations_compatible(a: str, b: str) -> bool:
    """Same place, or either unknown, or one named place contained in the other ('melbourne' within 'werribee melbourne')."""
    if not a or not b or a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    return ta <= tb or tb <= ta


def employers_match(a: str, b: str) -> bool:
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 5 and (a in b or b in a)))


def shorten_url(url: str | None, limit: int = 450) -> str | None:
    """Job-board links carry long tracking strings. Keep what identifies the job (Indeed: the jk id; SEEK/Jora: the path) so it always fits and still opens the same ad."""
    if not url or len(url) <= limit:
        return url
    u = urlparse(url.strip())
    host = u.netloc.lower()
    if "indeed." in host:
        q = dict(parse_qsl(u.query))
        jk = q.get("jk") or q.get("vjk")
        if jk:
            return f"{u.scheme}://{u.netloc}{u.path or '/viewjob'}?jk={jk}"
    short = f"{u.scheme}://{u.netloc}{u.path}"
    return short[:limit]
