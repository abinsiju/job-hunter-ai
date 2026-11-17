import logging
import time
import uuid

from fastapi import FastAPI, Request

from app.api.auth import router as auth_router
from app.api.routes import protected
from app.api.autofill import router as autofill_router
from app.api.v1 import v1
from app.api.v1.jobs import JobManager
from app.apply.gmail import credentials as gmail_credentials, get_mailer
from app.api.v1.apply_assist import spawn_runner
from app.ai.factory import get_provider
from app.db.session import get_session
from app.core.logging import request_id_var, setup_logging

setup_logging()
log = logging.getLogger("jobhunter.http")

# Interactive docs/OpenAPI are disabled: they would be unauthenticated routes.
app = FastAPI(title="Job Hunter AI", docs_url=None, redoc_url=None, openapi_url=None)
# Injectable so tests can swap in a test database and a fake model.
app.state.session_factory = get_session
app.state.ai_factory = get_provider
app.state.jobs = JobManager()
app.state.assist_spawn = spawn_runner  # starts the visible-browser fill in its own process
app.state.mailer_factory = get_mailer  # returns a Gmail DRAFT writer or None; there is no sender
app.state.mail_account = lambda: (gmail_credentials(with_password=False) or None) and gmail_credentials(with_password=False).address


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info("request", extra={"method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "ms": round((time.perf_counter() - start) * 1000, 1)})
        return response
    except Exception:
        log.exception("unhandled error", extra={"method": request.method, "path": request.url.path})
        raise
    finally:
        request_id_var.reset(token)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(protected)
app.include_router(v1)
app.include_router(autofill_router)  # scoped 30-minute codes only: not behind the login
