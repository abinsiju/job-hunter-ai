"""Everything a document may say, read ONLY from verified, document-visible profile rows."""
from datetime import date

from sqlalchemy.orm import Session

from app.documents.facts import document_facts

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FULL_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]


def month(ym: str, full: bool = False) -> str:
    y, m = ym[:7].split("-")
    return f"{(FULL_MONTHS if full else MONTHS)[int(m) - 1]} {y}"


def month_range(start: str | None, end: str | None) -> str:
    if start and end:
        a, b = month(start), month(end)
        return a if a == b else f"{a} – {b}"
    return month(start) if start else ""


class FactBook:
    def __init__(self, facts):
        self.by_key = {(f.category, f.key): f.value for f in facts}
        self.facts = facts

    @classmethod
    def from_session(cls, session: Session) -> "FactBook":
        return cls(document_facts(session))

    def get(self, category: str, key: str):
        return self.by_key.get((category, key))

    def in_category(self, category: str) -> dict:
        return {k: v for (c, k), v in self.by_key.items() if c == category}

    # --- header
    @property
    def name(self) -> str | None:
        return self.get("identity", "name")

    def contact_lines(self) -> tuple[list[str], list[str]]:
        """(contact parts, warnings). Email is only printed once the user has confirmed one."""
        parts, warnings = [], []
        for cat, key in (("contact", "phone"), ("contact", "location")):
            v = self.get(cat, key)
            if v:
                parts.append(str(v))
        em = self.get("contact", "email")
        if em and em.get("email"):
            parts.append(em["email"])
        else:
            warnings.append("No confirmed email on file: documents have no email in the header")
        return parts, warnings

    def has_licence_and_car(self) -> bool:
        v = self.get("license", "vic_drivers_licence")
        return bool(v and v.get("held") and v.get("own_car"))

    # --- plain-text fact list for the writer prompt and the grounding check
    def fact_lines(self) -> list[str]:
        L = []
        edu = self.get("education", "uni_bit")
        if edu:
            L.append(f"Education: {edu['degree']}, {edu['institution']}, expected completion {month(edu['expected_completion'], True)} (currently studying).")
        for key, e in self.in_category("employment").items():
            duties = e.get("duties_beyond_core_role") or e.get("duties") or []
            lead = f"Experience: {e.get('title')} at {e['employer']}, {e.get('location', '')}, {month_range(e.get('start'), e.get('end'))}."
            if e.get("framing"):
                lead += " IT support, network and admin duties were taken on beyond the core role."
            L.append(lead + (" Duties: " + "; ".join(duties) + "." if duties else ""))
        tcs = self.get("virtual_experience", "virtual_internship")
        if tcs:
            L.append(f"Virtual experience: {tcs['program']} on {tcs['provider']}, {month_range(tcs.get('start'), tcs.get('end'))}, covering {tcs['topic'].lower()}. This is not employment.")
        for key, p in self.in_category("project").items():
            if key == "recipe_planner_features":
                L.append(f"Project Recipe Planner: {p['description']}.")
            elif key == "capstone_network_console":
                L.append(f"Project (university capstone, not employment): {p['project']}; role {p['role']} in a team of {p['team_size']}; course {p['course']}, {p['team']}, {p['period']}; client {p['client']}. Contributions: " + "; ".join(p["contributions"]) + ".")
            elif key == "venuehub":
                L.append(f"Project VenueHub ({p['description']}), {p['document_wording']}, using {', '.join(p['stack'])}. Contributions: " + "; ".join(p["contributions"]) + ".")
        if self.has_licence_and_car():
            L.append("Holds a current Victorian driver's licence and has their own car.")
        skills = self.verified_skills()
        if skills:
            L.append("Skills: " + ", ".join(f"{n} ({lvl})" for n, lvl in skills) + ".")
        return L

    def verified_skills(self) -> list[tuple[str, str]]:
        return [(k, v.get("level")) for k, v in self.in_category("skill").items() if v.get("level") in ("basic", "working", "strong")]
