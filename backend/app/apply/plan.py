"""What the form filler is allowed to know: verified profile rows only, each answer tagged with the row it came from.
Anything not derivable from a verified row is absent, so the filler leaves it blank."""
import re
from dataclasses import dataclass
from pathlib import Path

from app.apply.answers import build_qa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Application, ProfileFact
from app.documents.facts import form_facts


@dataclass
class Answer:
    id: str
    pattern: str  # regex on the question text
    answer: str  # "Yes" or "No"
    source: str  # the verified profile row this comes from


def _verified(session: Session, category: str, key: str) -> dict | None:
    f = session.scalar(select(ProfileFact).where(ProfileFact.category == category, ProfileFact.key == key, ProfileFact.status == "verified"))
    return f.value if f else None


def phone_forms(raw: str) -> dict:
    """The same mobile number three ways: as stored, with the +61 country code, and the national form (no leading 0) for forms with a separate country-code box."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("61"):
        digits = "0" + digits[2:]
    if not digits.startswith("0") or len(digits) < 9:
        return {"phone_intl": "", "phone_national": ""}
    nat = digits[1:]
    pretty = f"{nat[:3]} {nat[3:6]} {nat[6:]}".strip()
    return {"phone_intl": f"+61 {pretty}", "phone_national": pretty}


def build_plan(session: Session, a: Application, url: str, platform: str) -> dict:
    facts = {(f.category, f.key): f.value for f in form_facts(session)}  # verified, document or forms-only visibility
    name = str(facts.get(("identity", "name")) or "").strip()
    first, _, last = name.partition(" ")
    loc = str(facts.get(("contact", "location")) or "")
    city, _, state = (x.strip() for x in loc.partition(","))
    email = (facts.get(("contact", "email")) or {}).get("email") or ""
    meta = a.draft_meta or {}
    cl = meta.get("cover_letter") or {}
    letter = "\n\n".join([cl.get("greeting", ""), *(cl.get("paragraphs") or []), cl.get("signoff", ""), name]).strip() if cl.get("paragraphs") else ""

    answers: list[Answer] = []
    rule = _verified(session, "eligibility", "screening_unrestricted_work_rights")  # internal row: the user's own rule
    if rule and str(rule.get("answer", "")).upper() == "NO":
        answers.append(Answer("unrestricted_work_rights", r"unrestricted (right|work)|full work rights|work (in australia )?without (any )?restrictions?|no (work )?restrictions", "No",
                              "eligibility/screening_unrestricted_work_rights"))
    wr = facts.get(("work_rights", "current"))
    if wr and "student" in str(wr.get("visa", "")).lower():  # a student visa holder is not a citizen or permanent resident
        answers.append(Answer("citizen_or_pr", r"australian citizen|permanent resident|citizen(ship)? (or|/) ?(pr|permanent)", "No", "work_rights/current"))
    lic = facts.get(("license", "vic_drivers_licence"))
    if lic and lic.get("held"):
        answers.append(Answer("drivers_licence", r"driver'?s?\s+licen[cs]e|driving\s+licen[cs]e", "Yes", "license/vic_drivers_licence"))
    edu = facts.get(("education", "uni_bit"))
    if edu and edu.get("status") == "in_progress":
        answers.append(Answer("studying", r"currently (studying|enrolled)|are you (a )?(current )?student", "Yes", "education/uni_bit"))

    std = facts.get(("form_answers", "standard")) or {}
    addr = facts.get(("contact", "full_address")) or {}
    if not addr.get("street"):
        addr = {}
    qa = build_qa(std, str((facts.get(("work_rights", "current")) or {}).get("visa", "")))

    folder = None
    files = {}
    for key, label in (("resume.pdf", "resume"), ("cover_letter.pdf", "cover_letter")):
        p = (a.doc_paths or {}).get(key)
        if p and Path(p).is_file():
            files[label] = str(Path(p).resolve())
            folder = folder or str(Path(p).resolve().parent)
    return {
        "url": url, "platform": platform, "employer": a.employer, "role": a.title, "folder": folder,
        "person": {"first_name": first, "last_name": last, "full_name": name, "email": email, "phone": str(facts.get(("contact", "phone")) or ""),
                   "city": addr.get("suburb") or city, "state": addr.get("state") or state, "location": loc, **phone_forms(str(facts.get(("contact", "phone")) or "")),
                   "street": addr.get("street", ""), "postcode": addr.get("postcode", ""), "country": addr.get("country", ""), "address_full": addr.get("line", "")},
        "files": files, "cover_letter_text": letter,
        "qa": qa, "answers": [{"id": x.id, "pattern": x.pattern, "answer": x.answer, "source": x.source} for x in answers],
    }
