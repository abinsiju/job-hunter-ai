"""Command-bar assistant. It maps a question to a fixed set of intents and answers FROM THE DATABASE, so it cannot invent facts.
Rules handle the common phrasings; the local model is only a fallback that must return one of the same intents."""
import json
import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.listings import _plain_explanation, _tracked
from app.api.v1.serializers import application_row, listing_row
from app.db.models import Application, Listing
from app.discovery.triage import ranked
from app.tracker.normalize import employers_match, norm_employer

router = APIRouter(prefix="/assistant", tags=["assistant"])

INTENTS = ["summary", "list_jobs", "list_applications", "explain_job", "check_employer", "help"]
SUGGESTIONS = ["How am I doing?", "Show my best matches", "What is waiting for my approval?", "Have I applied to Example Hospital?", "Why did job 12 score that way?"]
STOP = {"show", "me", "my", "the", "a", "an", "all", "find", "list", "jobs", "job", "roles", "role", "listings", "matches", "best", "top", "new",
        "in", "for", "with", "please", "any", "are", "there", "what", "which", "of", "and", "to", "is", "that", "pass", "review", "failed", "fail",
        "passing", "good", "clear", "excluded", "ones", "latest", "newest", "recent", "me?", "give", "get", "i", "have", "can", "apply"}
GENERIC_WORDS = {"anything", "anywhere", "yet", "already", "today", "somewhere", "else", "before", "ever", "all", "any", "at", "to", "for", "so", "far", "now", "jobs", "job"}
STATUS_WORDS = {"waiting": "drafted", "pending": "drafted", "approval": "drafted", "approve": "drafted", "drafted": "drafted", "drafts": "drafted",
                "approved": "approved", "applied": "applied", "interview": "interview", "interviews": "interview", "offer": "offer", "offers": "offer",
                "rejected": "rejected", "rejections": "rejected", "withdrawn": "withdrawn"}


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)


# An instruction to DO something (as opposed to a question). The assistant only reads data, so these get an honest refusal.
ACTION_RE = re.compile(r"^(?:please\s+|ok\s+|okay\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?(?:go\s+ahead\s+and\s+)?"
                       r"(?:apply|submit|send|email|message|contact|call|delete|remove|dismiss|hide|block|approve|withdraw|reject|update|change|edit|mark|record|log|post|upload|reply)\b")


def understand(message: str, known_locations: set[str]) -> dict:
    m = message.strip().lower()
    words = re.findall(r"[a-z0-9']+", m)
    if ACTION_RE.match(m):
        return {"intent": "refuse_action"}
    if re.search(r"\b(help|what can you do|how do you work)\b", m):
        return {"intent": "help"}
    num = re.search(r"(?:#|\bjob\b|\blisting\b|\bnumber\b|\bid\b)\s*(\d+)|\b(?:why|explain)\b.*?\b(\d+)\b", m)
    if re.search(r"\b(why|explain|how come|reason)\b", m) and num:
        return {"intent": "explain_job", "id": int(num.group(1) or num.group(2))}
    emp = re.search(r"\b(?:have i|did i|already|do i)\b.*?\b(?:applied|apply|application|tracked)\b(?:\s+(?:to|at|for|with))?\s+(.+?)[?.!]*$", m)
    if emp and emp.group(1).strip() and not set(re.findall(r"[a-z']+", emp.group(1))) <= GENERIC_WORDS:
        return {"intent": "check_employer", "employer": re.sub(r"\b(the|company|employer)\b", "", emp.group(1)).strip()}
    if re.search(r"\b(summary|overview|how am i|how's it going|how is it going|where are we|where am i|progress|status)\b", m) and not any(w in STATUS_WORDS for w in words):
        return {"intent": "summary"}
    status = next((STATUS_WORDS[w] for w in words if w in STATUS_WORDS), None)
    if status and not re.search(r"\b(jobs?|listings?|matches)\b", m):
        return {"intent": "list_applications", "status": status}
    if re.search(r"\b(jobs?|roles?|listings?|matches|show|find|list|best|top|new|latest|newest|suggest)\b", m):
        gate = "pass" if re.search(r"\b(pass|passing|clear|good)\b", m) else "review" if "review" in words else "fail" if re.search(r"\b(fail|failed|excluded)\b", m) else "open"
        loc = next((l for l in known_locations if l in m), None)
        leftover = [w for w in words if w not in STOP and w not in (loc or "").split() and len(w) > 2]
        return {"intent": "list_jobs", "gate": gate, "location": loc, "text": " ".join(leftover) or None, "sort": "newest" if re.search(r"\b(new|newest|latest|recent)\b", m) else "rank"}
    return {"intent": "unknown"}


LLM_SCHEMA = {"type": "object", "properties": {
    "intent": {"type": "string", "enum": INTENTS}, "gate": {"type": "string", "enum": ["open", "pass", "review", "fail"]},
    "location": {"type": "string"}, "text": {"type": "string"}, "status": {"type": "string", "enum": ["drafted", "approved", "applied", "interview", "offer", "rejected", "withdrawn", ""]},
    "id": {"type": "integer"}, "employer": {"type": "string"}}, "required": ["intent", "gate", "location", "text", "status", "id", "employer"]}
LLM_SYSTEM = ("Map the user's question about their job search to one intent. summary = overall progress; list_jobs = find job listings "
              "(gate: pass/review/fail/open; location; text keywords); list_applications = applications by status; explain_job = why a listing "
              "scored as it did (id); check_employer = have I applied to an employer (employer); help = what can you do. Use empty string or 0 for unused fields.")


async def llm_understand(ai, message: str) -> dict | None:
    try:
        out = json.loads(await ai.chat(LLM_SYSTEM, message, json_schema=LLM_SCHEMA, temperature=0))
    except Exception:  # noqa: BLE001 - the model is only a fallback
        return None
    if out.get("intent") not in INTENTS:
        return None
    return {k: (v or None) for k, v in out.items()}


def _jobs(db: Session, q: dict) -> list[Listing]:
    rows = ranked(db, include_fail=q.get("gate") == "fail", collapse=True)
    gates = {"open": ("PASS", "REVIEW"), "pass": ("PASS",), "review": ("REVIEW",), "fail": ("FAIL",)}[q.get("gate") or "open"]
    rows = [l for l in rows if l.gate_status in gates]
    if q.get("location"):
        rows = [l for l in rows if q["location"] in (l.location or "").lower()]
    if q.get("text"):
        terms = q["text"].lower().split()
        rows = [l for l in rows if all(t in f"{l.title} {l.employer or ''} {l.description}".lower() for t in terms)]
    if q.get("sort") == "newest":
        rows.sort(key=lambda l: l.posted_at or l.first_seen.date(), reverse=True)
    return rows


def answer(db: Session, q: dict) -> dict:
    intent = q["intent"]
    if intent == "help":
        return {"intent": intent, "text": "I answer from your own data: pipeline progress, job matches, applications, whether you already applied somewhere, and why a job scored as it did. I never invent facts and I never apply for anything.",
                "cards": [], "suggestions": SUGGESTIONS}
    if intent == "refuse_action":
        return {"intent": intent, "cards": [], "suggestions": ["Show my best matches", "What is waiting for my approval?"],
                "text": "I can only read and explain your data. I never apply, send or change anything. To act on a job, open it and choose what you want to do. You always apply yourself."}
    if intent == "summary":
        apps = list(db.scalars(select(Application)))
        live = [l for l in ranked(db, collapse=True)]
        drafts = sum(a.status == "drafted" for a in apps)
        approved = sum(a.status == "approved" for a in apps)
        interviews = sum(a.status == "interview" for a in apps)
        applied = sum(a.status in ("applied", "interview", "offer", "rejected") for a in apps)
        passes = sum(l.gate_status == "PASS" for l in live)
        bits = [f"{applied} applications recorded", f"{interviews} interview{'s' if interviews != 1 else ''}", f"{drafts} draft{'s' if drafts != 1 else ''} waiting for your review",
                f"{approved} approved and waiting for you to apply", f"{len(live)} jobs worth a look ({passes} clear match{'es' if passes != 1 else ''})"]
        return {"intent": intent, "text": ". ".join(bits) + ".", "cards": [{"type": "listing", **listing_row(l, _tracked(db, l))} for l in live[:3]],
                "suggestions": ["What is waiting for my approval?", "Show my best matches"]}
    if intent == "list_applications":
        rows = [a for a in db.scalars(select(Application).order_by(Application.updated_at.desc())) if a.status == q["status"]]
        word = {"drafted": "waiting for your approval", "approved": "approved, waiting for you to apply"}.get(q["status"], q["status"])
        return {"intent": intent, "text": f"{len(rows)} application{'s' if len(rows) != 1 else ''} {word}." if rows else f"Nothing is {word} right now.",
                "cards": [{"type": "application", **application_row(a)} for a in rows[:8]], "suggestions": ["How am I doing?"]}
    if intent == "list_jobs":
        rows = _jobs(db, q)
        label = {"open": "worth a look", "pass": "clear matches", "review": "to review", "fail": "excluded"}[q.get("gate") or "open"]
        where = f" in {q['location'].title()}" if q.get("location") else ""
        return {"intent": intent, "text": (f"{len(rows)} job{'s' if len(rows) != 1 else ''} {label}{where}" + (f" matching “{q['text']}”" if q.get("text") else "") + ("." if rows else ". Try another search, or run Find jobs.")),
                "cards": [{"type": "listing", **listing_row(l, _tracked(db, l))} for l in rows[:6]], "suggestions": ["Show new jobs", "Show my best matches"]}
    if intent == "explain_job":
        l = db.get(Listing, q["id"])
        if not l or l.triaged_at is None:
            return {"intent": intent, "text": f"I can't find a scored job with id {q['id']}.", "cards": [], "suggestions": SUGGESTIONS[:2]}
        lines = _plain_explanation(l) + [f"Flag: {f}" for f in (l.flags or [])]
        return {"intent": intent, "text": f"{l.title} at {l.employer or 'an unnamed employer'} scored {l.score:g} and is {l.gate_status}.",
                "cards": [{"type": "listing", **listing_row(l, _tracked(db, l))}, {"type": "note", "lines": lines or ["Nothing notable."]}], "suggestions": []}
    if intent == "check_employer":
        name = norm_employer(q["employer"])
        apps = [a for a in db.scalars(select(Application)) if employers_match(a.employer_norm, name)]
        if apps:
            return {"intent": intent, "text": f"Yes. {len(apps)} record{'s' if len(apps) != 1 else ''} for {apps[0].employer}.", "cards": [{"type": "application", **application_row(a)} for a in apps[:6]], "suggestions": []}
        return {"intent": intent, "text": f"No record of an application to {q['employer'].title()}.", "cards": [], "suggestions": []}
    return {"intent": "unknown", "text": "I didn't catch that. I can summarise progress, find jobs, list applications, check an employer, or explain a score.", "cards": [], "suggestions": SUGGESTIONS}


@router.post("")
async def ask(body: AskIn, request: Request, db: Session = Depends(get_db)) -> dict:
    locations = {t for l in db.scalars(select(Listing)) for t in re.findall(r"[a-z]{4,}", (l.location or "").lower()) if t not in ("region", "australia", "state")}
    q = understand(body.message, locations)
    if q["intent"] == "unknown":
        q = await llm_understand(request.app.state.ai_factory(), body.message) or q
        if q["intent"] == "list_applications" and not q.get("status"):
            q = {"intent": "unknown"}
        if q["intent"] == "explain_job" and not q.get("id"):
            q = {"intent": "unknown"}
        if q["intent"] == "check_employer" and not q.get("employer"):
            q = {"intent": "unknown"}
    return answer(db, q)
