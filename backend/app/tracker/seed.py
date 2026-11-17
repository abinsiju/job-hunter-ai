"""Load already-applied employers as 'applied' rows. Insert-only and idempotent.
    cd backend && uv run python -m app.tracker.seed ../data/applied_seed.json"""
import json
import sys
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Application
from app.tracker.normalize import fingerprint, norm_employer, norm_location, norm_title
from sqlalchemy import select

EVIDENCE = "Pre-existing application reported by the user on 2026-11-09 (before the tracker existed); no confirmation on file."


def seed_applied(session: Session, rows: list[dict]) -> tuple[int, int]:
    added = skipped = 0
    for r in rows:
        fp = fingerprint(r["employer"], r.get("title"), r.get("location"))
        if session.scalar(select(Application.id).where(Application.fingerprint == fp)):
            skipped += 1
            continue
        at = datetime(2026, 11, 9, tzinfo=timezone.utc)
        session.add(Application(
            fingerprint=fp, employer=r["employer"], title=r.get("title"), location=r.get("location"),
            employer_norm=norm_employer(r["employer"]), title_norm=norm_title(r.get("title")), location_norm=norm_location(r.get("location")),
            source="user-reported", status="applied", evidence=EVIDENCE, notes="Date applied not recorded; role/location unknown unless shown.",
            history=[{"status": "applied", "at": at.isoformat(), "note": "seeded from the user's already-applied list"}]))
        added += 1
    session.commit()
    return added, skipped


if __name__ == "__main__":
    from app.db.session import get_session

    with get_session() as s:
        print("added/skipped:", seed_applied(s, json.load(open(sys.argv[1]))))
