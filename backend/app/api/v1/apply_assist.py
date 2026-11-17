"""Apply assist: (1) a Gmail DRAFT with your documents attached, (2) assisted form fill. Neither ever submits or sends anything."""
import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.serializers import application_row
from app.apply.emails import valid_address
from app.apply.gmail import MailError, build_message
from app.apply.plan import build_plan
from app.apply.platforms import NotAllowed, PLATFORMS
from app.apply.policy import check_url
from app.db.models import Application
from app.documents.factbook import FactBook
from app.tracker.service import TrackerError, update_status

router = APIRouter(prefix="/applications", tags=["apply"])
OPEN_STATES = ("drafted", "approved")


def _err(status: int, code: str, message: str):
    return HTTPException(status, {"code": code, "message": message})


def _get(db: Session, app_id: int) -> Application:
    a = db.get(Application, app_id)
    if not a:
        raise HTTPException(404, "Application not found")
    return a


def _role(a: Application) -> str:
    return re.sub(r"\s*\(.*?\)", "", a.title or "").strip() or a.employer


def compose(a: Application, book: FactBook) -> tuple[str, str]:
    """Subject and a SHORT body built from the cover letter's own opening plus fixed sentences. Nothing else is added."""
    name = book.name or ""
    contact = [str(book.get("contact", "phone") or "")]
    em = book.get("contact", "email")
    if em and em.get("email"):
        contact.append(em["email"])
    cl = (a.draft_meta or {}).get("cover_letter") or {}
    paras = cl.get("paragraphs") or []
    opening = paras[0] if paras else f"I am writing to apply for the {_role(a)} position at {a.employer}."
    body = "\n\n".join([cl.get("greeting") or "Dear Hiring Manager,", opening,
                        "My resume and cover letter are attached. I would welcome the opportunity to discuss the role further.",
                        "Kind regards,\n" + "\n".join(x for x in [name, *contact] if x)])
    return f"Application – {_role(a)} – {name}", body


def _attachments(a: Application, name: str) -> list[tuple[str, Path]]:
    out = []
    for key, label in (("resume.pdf", "Resume"), ("cover_letter.pdf", "Cover Letter")):
        p = (a.doc_paths or {}).get(key)
        if p and Path(p).is_file():
            out.append((f"{name} - {label}.pdf", Path(p)))
    return out


def _compose_url(to: str, subject: str, body: str) -> str:
    return f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(to)}&su={quote(subject)}&body={quote(body[:1800])}"


class DraftIn(BaseModel):
    to: str


@router.get("/{app_id}/email")
def email_info(app_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    a = _get(db, app_id)
    book = FactBook.from_session(db)
    subject, body = compose(a, book)
    atts = _attachments(a, book.name or "")
    account = request.app.state.mail_account()
    reason = None if a.status in OPEN_STATES and len(atts) == 2 else (
        "Documents are needed first: draft them in Draft studio." if len(atts) < 2 else "Only drafts and approved applications can be emailed.")
    cands = (a.draft_meta or {}).get("apply_emails") or []
    return {"configured": bool(account), "account": account, "candidates": cands, "subject": subject, "body": body,
            "attachments": [n for n, _ in atts], "can_draft": reason is None, "reason": reason,
            "draft": (a.draft_meta or {}).get("email_draft"),
            "compose_url": _compose_url(cands[0]["address"] if cands else "", subject, body)}


@router.post("/{app_id}/email-draft")
def email_draft(app_id: int, body: DraftIn, request: Request, db: Session = Depends(get_db)) -> dict:
    a = _get(db, app_id)
    to = body.to.strip()
    if not valid_address(to):
        raise _err(422, "bad_address", "That email address does not look right.")
    if a.status not in OPEN_STATES:
        raise _err(409, "wrong_status", f"This application is already {a.status}; only drafts and approved applications can be emailed.")
    book = FactBook.from_session(db)
    atts = _attachments(a, book.name or "")
    if len(atts) < 2:
        raise _err(422, "no_documents", "Both PDFs are needed. Draft the documents first.")
    mailer = request.app.state.mailer_factory()
    if mailer is None:
        raise _err(409, "gmail_not_configured", "Gmail is not set up yet. Run scripts/set_gmail.sh once, then try again.")
    subject, text = compose(a, book)
    msg = build_message(sender=mailer.account, to=to, subject=subject, body=text, attachments=atts)
    try:
        folder = mailer.create_draft(msg)
    except MailError as e:
        raise _err(502, e.code, str(e)) from None
    when = datetime.now(timezone.utc).isoformat()
    a.draft_meta = {**(a.draft_meta or {}), "email_draft": {"to": to, "subject": subject, "at": when, "folder": folder, "attachments": [n for n, _ in atts]}}
    db.commit()
    if a.status == "drafted":  # making the draft means you accepted these documents
        try:
            update_status(db, a.id, "approved", note=f"Gmail draft created for {to} (not sent)")
        except TrackerError as e:
            raise _err(409, "rejected", str(e)) from None
    return {"application": application_row(a, detail=True), "draft": a.draft_meta["email_draft"]}


# ───────────────────────── assisted form fill ─────────────────────────
ACTIVE = ("starting", "opening", "navigating", "filling", "waiting", "ready", "handed_over")  # "ready" = filled and waiting for you: the window is still open


def spawn_runner(plan_path: Path, status_path: Path) -> int:
    """Start the visible-browser run as its own process, so the window stays open after this request ends."""
    backend = Path(__file__).resolve().parents[3]
    p = subprocess.Popen([sys.executable, "-m", "app.apply.runner", str(plan_path), str(status_path)], cwd=backend, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return p.pid


def _folder(a: Application) -> Path | None:
    p = (a.doc_paths or {}).get("resume.pdf")
    return Path(p).resolve().parent if p else None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_status(a: Application) -> dict | None:
    folder = _folder(a)
    f = folder / "assist_status.json" if folder else None
    if not f or not f.is_file():
        return None
    try:
        st = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    if st.get("state") in ACTIVE and not _alive(st.get("pid")):  # the window was closed, or the run died
        st = {**st, "state": "closed", "message": "The browser window was closed."}
    return st


def _suggest_url(a: Application) -> str | None:
    for u in [a.source_url, *((a.draft_meta or {}).get("apply_links") or [])]:
        if u:
            try:
                check_url(u)
                return u
            except NotAllowed:
                continue
    return None


class AssistIn(BaseModel):
    url: str | None = None


@router.get("/{app_id}/assist")
def assist_info(app_id: int, db: Session = Depends(get_db)) -> dict:
    a = _get(db, app_id)
    st = _read_status(a)
    if st and st.get("screenshot") and "filled_form.png" not in (a.doc_paths or {}):  # the screenshot becomes one of the application's documents
        shot = _folder(a) / st["screenshot"]
        if shot.is_file():
            a.doc_paths = {**(a.doc_paths or {}), "filled_form.png": str(shot)}
            db.commit()
    if st and st.get("confirmation") and (st["confirmation"] or {}).get("screenshot") and "confirmation.png" not in (a.doc_paths or {}):
        cs = _folder(a) / st["confirmation"]["screenshot"]
        if cs.is_file():
            a.doc_paths = {**(a.doc_paths or {}), "confirmation.png": str(cs)}
            db.commit()
    have_docs = all((a.doc_paths or {}).get(k) for k in ("resume.pdf", "cover_letter.pdf"))
    running = bool(st and st.get("state") in ACTIVE)
    reason = ("Draft the documents first." if not have_docs else "Only drafts and approved applications can use this." if a.status not in OPEN_STATES
              else "A browser window is already open for this application." if running else None)
    return {"platforms": [n.title() for n in PLATFORMS], "suggested_url": _suggest_url(a), "can_start": reason is None, "reason": reason, "running": running, "status": st}


@router.post("/{app_id}/assist")
def assist_start(app_id: int, body: AssistIn, request: Request, db: Session = Depends(get_db)) -> dict:
    a = _get(db, app_id)
    url = (body.url or "").strip() or _suggest_url(a)
    if not url:
        raise _err(422, "no_url", "Paste the link to the employer's application page (Workable, Greenhouse, Lever or SmartRecruiters).")
    try:
        platform = check_url(url).replace("ats:", "")
    except NotAllowed as e:
        raise _err(422, "site_not_allowed", str(e)) from None
    if a.status not in OPEN_STATES:
        raise _err(409, "wrong_status", f"This application is already {a.status}.")
    folder = _folder(a)
    if not folder or not all((a.doc_paths or {}).get(k) for k in ("resume.pdf", "cover_letter.pdf")):
        raise _err(422, "no_documents", "Both PDFs are needed. Draft the documents first.")
    st = _read_status(a)
    if st and st.get("state") in ACTIVE:
        raise _err(409, "already_running", "A browser window is already open for this application.")
    plan = build_plan(db, a, url, platform)
    plan_path, status_path = folder / "assist_plan.json", folder / "assist_status.json"
    plan_path.write_text(json.dumps(plan, indent=1))
    os.chmod(plan_path, 0o600)  # holds your contact details
    status_path.write_text(json.dumps({"state": "starting", "message": "Starting the browser", "platform": platform, "filled": [], "left_blank": [],
                                       "captcha": False, "login_required": False, "screenshot": None, "submit_clicked": False}))
    pid = request.app.state.assist_spawn(plan_path, status_path)
    st = json.loads(status_path.read_text())
    status_path.write_text(json.dumps({**st, "pid": pid}))
    if a.status == "drafted":  # using these documents on a real form means you accepted them
        update_status(db, a.id, "approved", note=f"Assisted form fill started on {platform} (stops before Submit)")
    return {"status": _read_status(a)}


@router.post("/{app_id}/autofill-token")
def autofill_token(app_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """A 30-minute code that lets the Chrome extension fill THIS application's forms in your own browser. It opens nothing else."""
    a = _get(db, app_id)
    if a.status not in OPEN_STATES:
        raise _err(409, "wrong_status", f"This application is already {a.status}.")
    if not all((a.doc_paths or {}).get(k) for k in ("resume.pdf", "cover_letter.pdf")):
        raise _err(422, "no_documents", "Both PDFs are needed. Draft the documents first.")
    from app.core.security import create_scoped_token
    port = request.url.port or 8000
    token = create_scoped_token("autofill", a.id, 30)
    return {"code": f"jh1:{port}:{token}", "expires_in_minutes": 30, "extension_path": str(Path(__file__).resolve().parents[4] / "extension"), "employer": a.employer, "role": a.title}


@router.post("/{app_id}/assist/stop")
def assist_stop(app_id: int, db: Session = Depends(get_db)) -> dict:
    a = _get(db, app_id)
    st = _read_status(a)
    if st and st.get("state") in ACTIVE and _alive(st.get("pid")):
        try:
            os.kill(st["pid"], signal.SIGTERM)
        except OSError:
            pass
        f = _folder(a) / "assist_status.json"
        f.write_text(json.dumps({**st, "state": "closed", "message": "You closed the browser window."}))
    return {"status": _read_status(a)}
