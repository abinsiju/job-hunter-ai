"""Job discovery sources. Only documented public APIs / feeds are used; nothing here scrapes HTML pages or bypasses a block."""
import html
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Protocol

# Identifies this client honestly. It carries no personal details.
USER_AGENT = "JobHunterAI-personal/0.1 (personal job search tool; polite, low-volume)"


@dataclass(frozen=True)
class RawListing:
    source: str
    source_id: str
    url: str
    title: str
    employer: str | None
    location: str | None
    description: str
    description_complete: bool  # False when the source only returns a snippet (e.g. Adzuna)
    posted_at: date | None = None
    employment_hint: str | None = None  # full_time / part_time / contract / casual, as given by the source
    salary_min: float | None = None
    salary_max: float | None = None
    salary_period: str = "unknown"

    def ad_text(self) -> str:
        """Header + description, so the extractor sees the structured facts the source gave us."""
        head = [f"Title: {self.title}"]
        if self.employer:
            head.append(f"Employer: {self.employer}")
        if self.location:
            head.append(f"Location: {self.location}")
        if self.employment_hint:
            head.append(f"Employment: {self.employment_hint.replace('_', ' ')}")
        if self.salary_min is not None and self.salary_period != "unknown":
            hi = f" to {self.salary_max:g}" if self.salary_max and self.salary_max != self.salary_min else ""
            head.append(f"Salary: {self.salary_min:g}{hi} per {self.salary_period}")
        return "\n".join(head) + "\n\n" + self.description


class JobSource(Protocol):
    name: str

    async def search(self, **query) -> list[RawListing]: ...


class SourceError(Exception):
    """A source failed. Messages never contain request URLs (they can carry API keys)."""


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "h1", "h2", "h3", "h4", "ul", "ol", "tr"):
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    p = _Text()
    p.feed(s)
    text = html.unescape("".join(p.parts))
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\xa0]+", " ", text)).strip()
