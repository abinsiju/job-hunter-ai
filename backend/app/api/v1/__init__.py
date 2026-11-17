from fastapi import APIRouter, Depends

from app.api.deps import require_user
from app.api.v1 import applications, apply_assist, assistant, jobs_routes, listings, misc, quick, sources

# Every /v1 route requires a valid session token.
v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_user)])
for module in (listings, applications, apply_assist, jobs_routes, misc, assistant, sources, quick):
    v1.include_router(module.router)
