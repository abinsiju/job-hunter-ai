from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings

_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("not-the-password")  # equalises timing for unknown usernames


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(subject: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": subject, "exp": exp}, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> str | None:
    """A normal login token. A scoped token (for example the 30-minute autofill code) is NEVER accepted here, so it can open nothing else."""
    if len(settings.jwt_secret) < 32:
        return None
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    return None if "scope" in claims else claims["sub"]


def create_scoped_token(scope: str, app_id: int, minutes: int = 30) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode({"sub": f"{scope}:{app_id}", "scope": scope, "app": app_id, "exp": exp}, settings.jwt_secret, algorithm="HS256")


def decode_scoped_token(token: str, scope: str) -> int | None:
    """The application id a scoped token is for, or None."""
    if len(settings.jwt_secret) < 32:
        return None
    try:
        c = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub", "scope", "app"]})
    except jwt.PyJWTError:
        return None
    return c["app"] if c.get("scope") == scope else None
