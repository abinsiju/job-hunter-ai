from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import generated_root, get_db
from app.api.v1.serializers import STATUS_ORDER, application_brief, application_row
from app.db.models import Application, Listing
from app.tracker.service import (DuplicateVacancyError, InvalidTransition, TrackerError, check_duplicate, create_or_refresh_draft,
                                 update_status)

router = APIRouter(prefix="/applications", tags=["applications"])


def _err(status: int, code: str, message: str):
    return HTTPException(status, {"code": code, "message": message})


class StatusIn(BaseModel):
    status: str
    evidence: str | None = None
    note: str | None = Field(None, max_length=2000)


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class NewApplicationIn(BaseModel):
    employer: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    location: str | None = None
    url: str | None = None
    source: str = "manual"


@router.get("")
def list_applications(status: str = "", db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(Application).order_by(Application.updated_at.desc(), Application.id.desc())))
    counts = {s: sum(a.status == s for a in rows) for s in STATUS_ORDER}
    if status:
        rows = [a for a in rows if a.status == status]
    return {"total": len(rows), "counts": counts, "items": [application_row(a) for a in rows]}


@router.post("", status_code=201)
def log_application(body: NewApplicationIn, db: Session = Depends(get_db)) -> dict:
    """Record a vacancy you found yourself. It starts as 'drafted'; a duplicate of something already applied to is refused."""
    try:
        a, _ = create_or_refresh_draft(db, employer=body.employer, title=body.title, location=body.location, source=body.source, url=body.url)
    except DuplicateVacancyError as e:
        raise _err(409, "duplicate", str(e)) from None
    return application_row(a, detail=True)


@router.get("/{app_id}")
def get_application(app_id: int, db: Session = Depends(get_db)) -> dict:
    a = db.get(Application, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    chk = check_duplicate(db, a.employer, a.title, a.location, a.source_url, exclude_id=a.id)
    listing = db.scalar(select(Listing).where(Listing.url == a.source_url)) if a.source_url else None
    return application_row(a, detail=True) | {
        "same_employer": [application_brief(x) for x in chk.same_employer], "listing_id": listing.id if listing else None}


@router.post("/{app_id}/status")
def change_status(app_id: int, body: StatusIn, db: Session = Depends(get_db)) -> dict:
    try:
        a = update_status(db, app_id, body.status, evidence=body.evidence, note=body.note)
    except DuplicateVacancyError as e:
        raise _err(409, "duplicate", str(e)) from None
    except InvalidTransition as e:
        raise _err(409, "invalid_transition", str(e)) from None
    except TrackerError as e:
        msg = str(e)
        raise _err(404 if msg.startswith("No application") else 422, "needs_evidence" if "evidence" in msg else "rejected", msg) from None
    return application_row(a, detail=True)


@router.post("/{app_id}/notes")
def add_note(app_id: int, body: NoteIn, db: Session = Depends(get_db)) -> dict:
    a = db.get(Application, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    a.notes = (a.notes + "\n" if a.notes else "") + f"[{stamp}] {body.note.strip()}"
    db.commit()
    return application_row(a, detail=True)


@router.get("/{app_id}/documents/{name}")
def download(app_id: int, name: str, db: Session = Depends(get_db)):
    a = db.get(Application, app_id)
    path = (a.doc_paths or {}).get(name) if a else None
    if not path:
        raise HTTPException(404, "Document not found")
    p = Path(path).resolve()
    if not p.is_relative_to(generated_root()) or not p.is_file():  # never serve anything outside the generated folder
        raise HTTPException(404, "Document not found")
    kinds = {".pdf": ("application/pdf", "inline"), ".png": ("image/png", "inline")}
    media, disposition = kinds.get(p.suffix, ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "attachment"))
    return FileResponse(p, media_type=media, filename=f"{a.employer} - {name}".replace("/", "-"), content_disposition_type=disposition)
