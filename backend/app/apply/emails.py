"""Find the address an ad asks you to apply to. Deterministic, from the ad text only (never guessed)."""
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
CUE_RE = re.compile(r"(apply|application|applications|resume|resumé|\bcv\b|send|submit|forward|e-?mail|enquir|contact|quoting)", re.I)
IGNORE_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply", "unsubscribe", "privacy", "abuse", "postmaster", "webmaster", "mailer-daemon"}
IGNORE_DOMAINS = {"example.com", "example.org", "example.net", "test.com", "sentry.io", "wixpress.com"}
SAFE_ADDRESS_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")  # no spaces, commas or angle brackets: no header injection


def valid_address(addr: str) -> bool:
    return bool(addr) and len(addr) <= 254 and bool(SAFE_ADDRESS_RE.match(addr))


def find_application_emails(text: str, exclude: tuple[str, ...] = ()) -> list[dict]:
    """Addresses in the ad, those right after words like 'apply' or 'send your resume' first. `exclude` drops the user's own address."""
    skip = {e.lower() for e in exclude}
    seen: dict[str, dict] = {}
    for m in EMAIL_RE.finditer(text):
        addr = m.group(0).lower()
        local, _, domain = addr.partition("@")
        if local in IGNORE_LOCAL or domain in IGNORE_DOMAINS or addr in skip:
            continue
        cue = bool(CUE_RE.search(text[max(0, m.start() - 120):m.start()]))
        if addr not in seen or (cue and not seen[addr]["near_apply_cue"]):
            seen[addr] = {"address": addr, "near_apply_cue": cue}
    return sorted(seen.values(), key=lambda e: not e["near_apply_cue"])  # stable: order of appearance is kept within each group
