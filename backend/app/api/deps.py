from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def require_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    user = decode_access_token(creds.credentials) if creds else None
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return user
