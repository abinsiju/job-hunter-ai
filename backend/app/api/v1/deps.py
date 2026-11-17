from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import settings


def get_db(request: Request) -> Iterator[Session]:
    s = request.app.state.session_factory()
    try:
        yield s
    finally:
        s.close()


def get_ai(request: Request):
    return request.app.state.ai_factory()


def generated_root() -> Path:
    return Path(settings.generated_dir).resolve()
