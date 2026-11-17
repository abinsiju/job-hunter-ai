"""Load profile facts from a JSON file. Insert-only: existing rows (e.g. confirmed since) are never overwritten.
    cd backend && uv run python -m app.db.seed ../data/profile_seed.json"""
import json
import sys
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

import json as _json

from app.db.models import ProfileFact
from app.documents.guardrails import ForbiddenClaimError, find_forbidden


def check_row(row: dict) -> None:
    """Refuse to store a fact that asserts a forbidden claim (guardrail rows must name them, so they are exempt)."""
    if row.get("category") == "guardrail":
        return
    hits = find_forbidden(" ".join([str(row.get("key", "")), _json.dumps(row.get("value", "")), str(row.get("notes") or "")]))
    if hits:
        raise ForbiddenClaimError(f"profile row {row.get('category')}/{row.get('key')} contains forbidden claim(s): {hits}")


def seed_facts(session: Session, rows: list[dict]) -> tuple[int, int]:
    for r in rows:
        check_row(r)  # validate everything first: all or nothing
    added = skipped = 0
    for r in rows:
        exists = session.scalar(select(ProfileFact.id).where(ProfileFact.category == r["category"], ProfileFact.key == r["key"]))
        if exists:
            skipped += 1
            continue
        lc = r.get("last_confirmed")
        session.add(ProfileFact(**{**r, "last_confirmed": date.fromisoformat(lc) if lc else None}))
        added += 1
    session.commit()
    return added, skipped


if __name__ == "__main__":
    from app.db.session import get_session

    with get_session() as s:
        print("added/skipped:", seed_facts(s, json.load(open(sys.argv[1]))))
