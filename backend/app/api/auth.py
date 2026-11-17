import logging
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import create_access_token, verify_password

log = logging.getLogger("jobhunter.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILS, LOCK_SECONDS = 5, 300
_fails: dict[str, list[float]] = {}  # per-client failed attempts (in-memory, single process)


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, request: Request) -> dict:
    if len(settings.jwt_secret) < 32 or not settings.admin_password_hash:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth not configured (run scripts/set_password.py)")
    client = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [t for t in _fails.get(client, []) if now - t < LOCK_SECONDS]
    if len(recent) >= MAX_FAILS:
        log.warning("login locked out", extra={"client": client})
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, try later")
    user_ok = secrets.compare_digest(body.username, settings.admin_username)
    pw_ok = verify_password(body.password, settings.admin_password_hash if user_ok else "")
    if not (user_ok and pw_ok):
        _fails[client] = recent + [now]
        log.warning("login failed", extra={"client": client})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    _fails.pop(client, None)
    log.info("login ok", extra={"client": client})
    return {"access_token": create_access_token(body.username), "token_type": "bearer"}
