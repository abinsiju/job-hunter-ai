import asyncio
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import generated_root, get_db
from app.api.v1.jobs_routes import serialize_draft
from app.documents.validate import GenerationRejected
from app.fastdraft.service import BUDGET, TooSlow, fast_draft

router = APIRouter(prefix="/quick-draft", tags=["quick-draft"])


class QuickIn(BaseModel):
    text: str = Field(..., max_length=40000)
    url: str | None = Field(None, max_length=4000)
    title: str = Field("", max_length=120)
    employer: str = Field("", max_length=120)


@router.post("")
async def quick(body: QuickIn, request: Request, db: Session = Depends(get_db)) -> dict:
    text = body.text.strip()
    if len(text) < 80:
        raise HTTPException(422, "Paste the whole job ad (at least a few sentences).")
    try:
        r = await asyncio.wait_for(fast_draft(db, request.app.state.ai_factory(), text, date.today(), generated_root(), url=(body.url or None), title=body.title, employer=body.employer), BUDGET + 2)
    except GenerationRejected as e:  # the fact-checker refused a sentence: say so plainly instead of failing
        raise HTTPException(422, f"The fact-checker stopped this draft because: {'; '.join(e.args[0][:3]) if e.args and isinstance(e.args[0], list) else e}. Type the job title and employer above and try again.") from None
    except (TooSlow, asyncio.TimeoutError) as e:
        raise HTTPException(504, str(e) if isinstance(e, TooSlow) else f"This took longer than {int(BUDGET)} seconds, so I stopped.") from None
    out = serialize_draft(r["result"])
    return {**out, "ats": r["ats"], "timings": r["timings"], "elapsed_ms": r["elapsed_ms"], "within_budget": r["within_budget"], "engine": r["engine"]}


class SkillIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=60, pattern=r"^[A-Za-z0-9 +#./&()\-]+$")
    level: str = Field(..., pattern="^(basic|working|strong)$")


@router.post("/confirm-skill")
def confirm_skill(body: SkillIn, db: Session = Depends(get_db)) -> dict:
    """You tell Job Hunter 'I really have this skill'. It is stored as verified by your statement (today), so future resumes may list it. Nothing is guessed."""
    from datetime import date as _d
    from sqlalchemy import select
    from app.db.models import ProfileFact
    from app.db.seed import ForbiddenClaimError, check_row
    name = body.name.strip()
    row = {"category": "skill", "key": name, "value": {"level": body.level}, "status": "verified", "visibility": "document",
           "source": f"user statement in Quick draft, {_d.today().isoformat()}", "last_confirmed": _d.today(), "notes": None}
    try:
        check_row(row)
    except ForbiddenClaimError as e:
        raise HTTPException(422, str(e)) from None
    existing = db.scalar(select(ProfileFact).where(ProfileFact.category == "skill", ProfileFact.key == name))
    if existing:
        existing.value, existing.status, existing.source, existing.last_confirmed = row["value"], "verified", row["source"], row["last_confirmed"]
    else:
        db.add(ProfileFact(**row))
    db.commit()
    return {"skill": name, "level": body.level, "message": f"{name} is now in your profile as {body.level}. Draft again to include it."}
