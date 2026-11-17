from dataclasses import dataclass, field


@dataclass
class Entry:
    heading: str
    sub: str | None = None
    bullets: list[str] = field(default_factory=list)


@dataclass
class Section:
    title: str
    text: str | None = None  # paragraph-style section (summary, skills line)
    entries: list[Entry] = field(default_factory=list)


@dataclass
class Resume:
    name: str
    contact: list[str]
    sections: list[Section]


@dataclass
class CoverLetter:
    name: str
    contact: list[str]
    date_line: str
    subject: str
    greeting: str
    paragraphs: list[str]
    signoff: str = "Kind regards,"
