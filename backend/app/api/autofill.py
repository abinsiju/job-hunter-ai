"""Autofill in YOUR OWN browser. A small Chrome extension (extension/) sends the page's form fields here and receives what to put in each, using the
same rules as the automated assistant. It runs in your normal Chrome with no automation, never clicks buttons, and leaves security checks and Submit to you.
Authentication: a 30-minute code made for ONE application, accepted only on these routes (and never as a login)."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.apply.answers import SemanticMatcher
from app.apply.engine import Decision
from app.apply.formgate import check_form
from app.apply.plan import build_plan
from app.apply.platforms import NotAllowed
from app.apply.policy import check_url
from app.apply.resolver import resolve_control
from app.core.security import decode_scoped_token
from app.db.models import Application

router = APIRouter(prefix="/autofill", tags=["autofill"])
_bearer = HTTPBearer(auto_error=False)
COUNTRY_CODE = r"country code|dial|calling code|phone country|prefix"


def autofill_app(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> Application:
    app_id = decode_scoped_token(creds.credentials, "autofill") if creds else None
    a = db.get(Application, app_id) if app_id else None
    if not a:
        raise HTTPException(401, "This autofill code is missing, wrong or expired. Make a new one in Job Hunter.", headers={"WWW-Authenticate": "Bearer"})
    return a


class Control(BaseModel):
    idx: int
    type: str = Field(max_length=20)
    label: str = Field("", max_length=400)
    name: str = Field("", max_length=200)
    id: str = Field("", max_length=200)
    required: bool = False
    options: list = Field(default_factory=list, max_length=300)
    value: str = Field("", max_length=2000)
    combo: bool = False
    hasFile: bool = False
    multiple: bool = False
    intl: bool = False
    placeholder: str = Field("", max_length=200)
    occ: int = 1
    checked: bool = False


class PlanIn(BaseModel):
    url: str = Field(max_length=2000)
    page_text: str = Field("", max_length=60000)
    controls: list[Control] = Field(max_length=300)
    cc_set: bool = False  # a country-code box was already set earlier on this page


def _file_kinds(value, files: dict) -> list[str]:
    paths = value if isinstance(value, list) else [value]
    return [k for k, p in files.items() if p in paths]


@router.post("/plan")
def plan(body: PlanIn, a: Application = Depends(autofill_app), db: Session = Depends(get_db)) -> dict:
    try:
        host_class = check_url(body.url)
    except NotAllowed as e:
        raise HTTPException(403, str(e)) from None  # job boards and private addresses: never
    gate = check_form(body.page_text, [c.label for c in body.controls])
    if gate.status == "FAIL":
        return {"stop": gate.reasons[0], "gate": {"status": gate.status, "reasons": gate.reasons, "questions": gate.questions}, "actions": [], "needs": [], "review": []}
    p = build_plan(db, a, body.url, host_class.replace("ats:", ""))
    from app.apply.workflow import default_embed
    sem = SemanticMatcher(default_embed(), p.get("qa", []))
    n_files = sum(c.type == "file" for c in body.controls)
    ordered = sorted(body.controls, key=lambda c: not __import__("re").search(COUNTRY_CODE, (c.label or "").lower()))  # country-code box first
    cc_set, actions, needs, review = body.cc_set, [], [], []
    for c in ordered:
        r = resolve_control(c.model_dump(), p, n_files, {"cc_set": cc_set}, sem)
        if r.skip or r.keep_existing:
            continue
        if r.need or not r.dec:
            needs.append({"idx": c.idx, "label": r.label[:160], "reason": r.need or "no verified answer in your profile", "required": c.required, "category": r.cat, "type": c.type})
            continue
        d: Decision = r.dec
        act = {"idx": c.idx, "kind": d.kind, "label": r.label[:160], "source": d.source, "review": r.review}
        if d.kind == "file":
            act["files"] = _file_kinds(d.value, p["files"])
            if not act["files"]:
                needs.append({"idx": c.idx, "label": r.label[:160], "reason": "the document is not available", "required": c.required, "category": r.cat, "type": "file"})
                continue
        else:
            act["value"] = d.value
            act["option_ref"] = d.option_ref
        if d.kind == "select" and "country code" in str(d.source):
            cc_set = True
        actions.append(act)
        if r.review:
            review.append({"label": r.label[:160], "value": (d.value if d.kind != "file" else ", ".join(act["files"]))[:140], "category": r.cat})
    return {"actions": actions, "needs": needs, "review": review, "gate": {"status": gate.status, "reasons": gate.reasons, "questions": gate.questions}, "cc_set": cc_set}


@router.get("/file/{kind}")
def get_file(kind: str, a: Application = Depends(autofill_app)):
    key = {"resume": "resume.pdf", "cover_letter": "cover_letter.pdf"}.get(kind)
    path = (a.doc_paths or {}).get(key) if key else None
    if not path or not Path(path).is_file():
        raise HTTPException(404, "That document does not exist for this application")
    return FileResponse(path, media_type="application/pdf", filename={"resume": "Resume.pdf", "cover_letter": "Cover Letter.pdf"}[kind])
