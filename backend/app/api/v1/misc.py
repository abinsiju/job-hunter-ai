"""profile, blocklist, system status, overview."""
import asyncio
import shutil
import sys
from datetime import date, datetime, timezone

import httpx
import redis
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.v1.deps import generated_root, get_db
from app.api.v1.serializers import CATEGORY_LABELS, CATEGORY_ORDER, STATUS_ORDER, application_row, fact_row, listing_row
from app.blocklist import add_blocked, list_blocked, remove_blocked
from app.core.config import settings
from app.db.models import Application, Listing, ProfileFact
from app.discovery.triage import ranked

router = APIRouter(tags=["dashboard"])


# ------------------------------------------------------------------ profile
@router.get("/profile")
def profile(db: Session = Depends(get_db)) -> dict:
    facts = list(db.scalars(select(ProfileFact).order_by(ProfileFact.id)))
    groups = []
    for cat in CATEGORY_ORDER:
        rows = [fact_row(f) for f in facts if f.category == cat]
        if rows:
            groups.append({"category": cat, "label": CATEGORY_LABELS[cat], "facts": rows})
    verified = sum(f.status == "verified" for f in facts)
    return {"groups": groups, "summary": {"total": len(facts), "verified": verified, "needs_confirmation": len(facts) - verified,
                                          "used_in_documents": sum(f.status == "verified" and f.visibility == "document" for f in facts)}}


# ------------------------------------------------------------------ blocklist
class BlockIn(BaseModel):
    employer: str = Field(min_length=1, max_length=200)
    reason: str = ""


@router.get("/blocklist")
def get_blocklist(db: Session = Depends(get_db)) -> list[dict]:
    return [{"employer": e, "reason": r} for e, r in list_blocked(db).items()]


@router.post("/blocklist", status_code=201)
def block(body: BlockIn, db: Session = Depends(get_db)) -> dict:
    try:
        return {"employer": body.employer.strip(), "added": add_blocked(db, body.employer, body.reason)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.delete("/blocklist/{employer}")
def unblock(employer: str, db: Session = Depends(get_db)) -> dict:
    return {"employer": employer, "removed": remove_blocked(db, employer)}


# ------------------------------------------------------------------ system
async def _ollama() -> dict:
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            v = (await c.get(f"{settings.ai_base_url}/api/version")).json().get("version")
            ps = (await c.get(f"{settings.ai_base_url}/api/ps")).json().get("models", [])
        return {"ok": True, "version": v, "loaded": [m.get("name") for m in ps]}
    except Exception:  # noqa: BLE001
        return {"ok": False, "version": None, "loaded": []}


def _redis_ok() -> bool:
    try:
        return bool(redis.from_url(settings.redis_url, socket_connect_timeout=1.5).ping())
    except Exception:  # noqa: BLE001
        return False


@router.get("/system")
async def system(request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("select 1"))
        pg = True
    except Exception:  # noqa: BLE001
        pg = False
    ollama = await _ollama()
    gen = generated_root()
    return {
        "services": {"database": pg, "redis": await asyncio.to_thread(_redis_ok), "ollama": ollama},
        "ai": {"provider": settings.ai_provider, "chat_model": settings.ai_chat_model, "embed_model": settings.ai_embed_model, "context": settings.ai_num_ctx},
        "discovery": {"adzuna_configured": bool(settings.adzuna_app_id and settings.adzuna_app_key)},
        "mail": {"configured": bool(request.app.state.mail_account()), "account": request.app.state.mail_account() or None},
        "counts": {"listings": db.query(Listing).count(), "applications": db.query(Application).count(), "facts": db.query(ProfileFact).count(),
                   "generated_folders": len([p for p in gen.iterdir() if p.is_dir()]) if gen.exists() else 0},
        "disk_free_gb": round(shutil.disk_usage("/").free / 1e9, 1), "python": sys.version.split()[0], "env": settings.app_env,
        "running_jobs": [j for j in request.app.state.jobs.recent(5) if j["status"] in ("queued", "running")],
        "backup_hint": "scripts/backup_db.sh writes a compressed Postgres dump to backups/",
    }


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    apps = list(db.scalars(select(Application).order_by(Application.updated_at.desc())))
    by_status = {s: sum(a.status == s for a in apps) for s in STATUS_ORDER}
    rows = ranked(db, include_fail=True, collapse=True)
    live = [l for l in rows if l.gate_status in ("PASS", "REVIEW")]
    attention = []
    for a in apps:
        if a.status == "drafted":
            attention.append({"kind": "review_draft", "title": "Review and approve", "detail": f"{a.employer}: {a.title or 'role not recorded'}", "application_id": a.id})
        elif a.status == "approved":
            attention.append({"kind": "record_application", "title": "Applied? Record it", "detail": f"{a.employer}: {a.title or 'role not recorded'}", "application_id": a.id})
    activity = sorted(({"at": h["at"], "status": h["status"], "note": h.get("note"), "application_id": a.id, "employer": a.employer, "title": a.title}
                       for a in apps for h in (a.history or []) if a.source != "user-reported"), key=lambda x: x["at"], reverse=True)[:8]
    name = db.scalar(select(ProfileFact).where(ProfileFact.category == "identity", ProfileFact.key == "name"))
    return {
        "owner": {"first_name": str(name.value).split()[0] if name and name.value else None},
        "listings": {"total": len(rows), "pass": sum(l.gate_status == "PASS" for l in rows), "review": sum(l.gate_status == "REVIEW" for l in rows),
                     "fail": sum(l.gate_status == "FAIL" for l in rows)},
        "pipeline": [{"status": s, "count": by_status[s]} for s in STATUS_ORDER],
        "applied_total": sum(bool(a.status in ("applied", "interview", "offer", "rejected") or (a.status == "withdrawn" and a.applied_at)) for a in apps),
        "top_matches": [listing_row(l) for l in live[:5]], "attention": attention[:6], "activity": activity,
        "has_discovery": bool(rows),
    }
