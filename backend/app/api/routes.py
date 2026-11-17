from fastapi import APIRouter, Depends

from app.api.deps import require_user

# Every route on this router requires a valid token.
protected = APIRouter(dependencies=[Depends(require_user)])


@protected.get("/me")
def me(user: str = Depends(require_user)) -> dict:
    return {"user": user}
