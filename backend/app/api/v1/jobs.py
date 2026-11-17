"""In-process job runner for slow operations (LLM extraction, discovery). One LLM job at a time: the model is single-tenant."""
import asyncio
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

log = logging.getLogger("jobhunter.jobs")
MAX_KEPT = 50


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    message: str = "Waiting"
    result: Any = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None

    def view(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "message": self.message, "result": self.result,
                "error": self.error, "created_at": self.created_at, "finished_at": self.finished_at}


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._lock: asyncio.Lock | None = None
        self._tasks: set[asyncio.Task] = set()

    def submit(self, kind: str, fn: Callable[[Callable[[str], None]], Awaitable[Any]]) -> Job:
        """`fn(progress)` runs in the background; its return value becomes `result`. Errors become a readable `error`."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        self.jobs[job.id] = job
        for old in list(self.jobs)[:-MAX_KEPT]:
            self.jobs.pop(old, None)

        async def run():
            self._lock = self._lock or asyncio.Lock()
            async with self._lock:
                job.status, job.message = "running", "Working"
                try:
                    job.result = await fn(lambda m: setattr(job, "message", m))
                    job.status, job.message = "done", "Done"
                except Exception as e:  # noqa: BLE001 - surfaced to the UI as a message, details to the log
                    job.status, job.error = "error", getattr(e, "user_message", None) or str(e) or type(e).__name__
                    job.message = "Failed"
                    log.error("job failed", extra={"job": job.id, "kind": kind, "trace": traceback.format_exc()[-600:]})
                finally:
                    job.finished_at = datetime.now(timezone.utc).isoformat()

        t = asyncio.get_running_loop().create_task(run())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def recent(self, n: int = 10) -> list[dict]:
        return [j.view() for j in list(self.jobs.values())[-n:]][::-1]
