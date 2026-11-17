"""For ONE form control: what (if anything) goes in it, or why it is left to you. Pure logic, no browser: the automated assistant and
'autofill in your own browser' both call this, so they can never disagree about an answer."""
import re
from dataclasses import dataclass

from app.apply.engine import Decision, decide, qa_apply
from app.apply.questions import classify_question

NEVER_REASON = {"LEGAL": "a declaration or legal question: only you can answer it", "DEMOGRAPHIC": "voluntary personal information: your choice, so I left it",
                "SALARY": "salary: your decision, I did not fill it", "BEHAVIOURAL": "needs a real example from you: I do not invent experience"}


@dataclass
class Resolved:
    label: str
    cat: str
    pol: str
    dec: Decision | None = None
    skip: bool = False  # nothing to do and nothing to report
    need: str | None = None  # left to you, with the reason
    keep_existing: bool = False  # the box already holds something: never overwritten
    semantic_id: str | None = None
    review: bool = False  # read this before you submit


def resolve_control(c: dict, plan: dict, n_files: int = 1, hints: dict | None = None, semantic=None) -> Resolved:
    label = c["label"] or c["name"] or c["id"] or c["type"]
    if c.get("occ", 1) > 1:
        label = f"{label} (#{c['occ']})"  # a repeated section (second employer, third referee…) is tracked on its own
    cat, pol = classify_question(label, c["options"])
    r = Resolved(label, cat, pol)
    if c["type"] == "password":
        r.skip = True
        return r
    if c["type"] == "file" and (c.get("hasFile") or re.search(r"replace file|remove file|change file", label, re.I)):
        r.skip = True  # already uploaded: never upload the same document twice
        return r
    if c["type"] == "checkbox":
        r.need = "a declaration or consent box: only you can tick it" if cat == "LEGAL" else "a checkbox: your choice"
        return r
    dec = decide(c, plan, n_files, hints)
    if dec is None and semantic is not None and c["type"] not in ("file", "checkbox"):  # no exact rule: a known question worded differently?
        e = semantic.match(label)
        if e and (dec := qa_apply(c, e)):
            r.semantic_id = e["id"]
    from_bank = bool(dec and str(dec.source).startswith("form_answers"))
    if pol == "never" and c["type"] != "file" and not from_bank:
        r.need = NEVER_REASON.get(cat, "yours to answer")
        return r
    if dec is None and cat == "MOTIVATION" and c["type"] == "textarea" and plan.get("cover_letter_text"):
        dec = Decision("text", plan["cover_letter_text"], "draft: cover letter (verified facts only)")
    if dec is None:
        if c["type"] == "file" and not label.strip(" *"):
            r.skip = True
            return r
        r.need = ("visa and sponsorship answers are never inferred: please answer" if cat == "VISA" else
                  "a requested document: I only attach your resume and cover letter. Sensitive documents are yours to attach" if c["type"] == "file" else
                  "no verified answer in your profile")
        return r
    if c["type"] not in ("file", "radio", "select", "checkbox") and (c.get("value") or "").strip() not in ("", str(dec.value).strip()):
        r.keep_existing = True
        return r
    r.dec = dec
    r.review = bool(pol == "review" or dec.review or cat in ("WORK_RIGHTS", "VISA", "MOTIVATION"))
    return r
