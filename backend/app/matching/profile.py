import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

# --- location tiers (user rule: west Melbourne + CBD highest, rest of Melbourne, then Sydney > Canberra > others)
WEST_AND_CBD = (
    "exampleton", "truganina", "wyndham", "werribee", "point cook", "hoppers crossing", "laverton", "altona",
    "williamstown", "sunshine", "footscray", "derrimut", "melton", "caroline springs", "deer park", "st albans",
    "yarraville", "maribyrnong", "braybrook", "sydenham", "tullamarine", "melbourne cbd", "docklands",
    "southbank", "south wharf", "melbourne 3000", "west melbourne", "north melbourne", "flagstaff", "city of melbourne",
)

# Target roles from the original handover. Secondary roles earn SECONDARY_ROLE_FACTOR of the role-relevance points.
PRIMARY_ROLES = [
    "it support officer", "it support technician", "service desk analyst", "help desk analyst", "helpdesk analyst",
    "desktop support", "junior it support", "technical support", "ict support officer", "msp support technician",
    "junior systems administrator", "junior network technician", "it operations support", "application support",
    "it support", "service desk", "help desk", "helpdesk",
]
SECONDARY_ROLES = [
    "junior cybersecurity analyst", "soc trainee", "junior network engineer", "graduate it consultant",
    "junior software developer", "junior web developer", "ui/ux designer", "junior data analyst", "technical support engineer",
]
SECONDARY_ROLE_FACTOR = 0.6

# Australian National Minimum Wage, Fair Work Commission Annual Wage Review 2026, effective 2026-07-01.
# UPDATE EVERY 1 JULY. Awards can pay more than this floor; the app cannot look up awards, so this only catches clear breaches.
MIN_WAGE_HOURLY = 26.44
CASUAL_LOADING = 0.25
MIN_WAGE_EFFECTIVE = "2026-07-01"
FULL_TIME_HOURS_PER_YEAR = 38 * 52


@dataclass
class CandidateProfile:
    skills: dict[str, str] = field(default_factory=dict)  # skill -> "verified" | "needs_confirmation"
    relevant_years: float = 0.0
    degree_status: Literal["completed", "in_progress", "none"] = "none"
    certifications: list[str] = field(default_factory=list)
    primary_roles: list[str] = field(default_factory=lambda: list(PRIMARY_ROLES))
    secondary_roles: list[str] = field(default_factory=lambda: list(SECONDARY_ROLES))
    has_licence_and_car: bool = False
    restricted_until: date = date(2027, 6, 1)  # student-visa work limits apply before this
    max_hours_per_week_in_semester: float = 24.0  # 48 hrs / fortnight
    applied_employers: list[str] = field(default_factory=list)
    blocked_employers: list[str] = field(default_factory=list)  # employers the user never wants to hear about
    min_hourly_rate: float | None = None  # candidate has no minimum -> salary scored neutral


def _clean(name: str) -> str:
    n = re.sub(r"\b(pty|ltd|limited|group|australia|the)\b", " ", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def find_duplicate(employer: str | None, applied: list[str]) -> str | None:
    """Return the already-applied employer matching this one, if any."""
    if not employer:
        return None
    e = _clean(employer)
    for a in applied:
        c = _clean(a)
        if c and e and (c == e or (len(c) >= 5 and (c in e or e in c))):
            return a
    return None


def screening_answer(question: str) -> str | None:
    """Fixed answers for screening questions. Never auto-answers 'yes' to unrestricted work rights."""
    q = question.lower()
    if re.search(r"unrestricted|full work rights|right to work.*without|no restrictions", q):
        return "NO"
    return None
