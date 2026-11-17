"""The eligibility gate, re-run on the LIVE application form just before anything is filled. Employer forms often ask things the ad never said."""
import re
from dataclasses import dataclass, field

CITIZEN_REQ = re.compile(r"(must|need to|required to|have to|only)\b[^.\n]{0,60}\b(be|hold|have)?[^.\n]{0,20}(australian )?(citizen(ship)?|permanent residen(t|cy)|\bPR\b)|"
                         r"(australian )?citizen(ship)?[^.\n]{0,30}(only|required|essential|mandatory)|(security|baseline|nv[12]|agsva)[^.\n]{0,20}(clearance|vetting)[^.\n]{0,30}(required|essential|must)|"
                         r"(must|required to|need to)[^.\n]{0,40}(security|baseline|nv[12]|agsva)[^.\n]{0,20}(clearance|vetting)", re.I)
WORK_RIGHTS_Q = re.compile(r"work rights|right to work|entitled to work|authori[sz]ed to work|work in australia|sponsor|visa|citizen|permanent residen|residency status|clearance", re.I)


@dataclass
class FormGate:
    status: str = "PASS"  # PASS | REVIEW | FAIL
    reasons: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


def check_form(page_text: str, labels: list[str]) -> FormGate:
    """FAIL: the form itself states citizenship/PR/clearance as a requirement (we are a student visa holder: stop).
    REVIEW: it asks a work-rights / sponsorship / visa question: answered only from the verified profile, shown to the user."""
    g = FormGate()
    m = CITIZEN_REQ.search(page_text or "")
    if m:
        g.status = "FAIL"
        g.reasons.append(f"the application form states a requirement the verified profile cannot meet: “{re.sub(chr(10), ' ', m.group(0).strip())[:160]}”")
        return g
    for lab in labels:
        if WORK_RIGHTS_Q.search(lab or "") and lab not in g.questions:
            g.questions.append(lab.strip()[:200])
    if g.questions:
        g.status = "REVIEW"
        g.reasons.append("the form asks work-rights or visa questions; answered only from verified profile facts, please read them before submitting")
    return g
