"""Run one assisted fill in its own process (so the visible browser outlives the web request).
    python -m app.apply.runner PLAN.json STATUS.json"""
import json
import os
import sys
from pathlib import Path

from app.apply.engine import run_assist
from app.core.config import settings


def main(plan_path: str, status_path: str) -> int:
    plan = json.loads(Path(plan_path).read_text())
    status_file = Path(status_path)

    def write(status: dict) -> None:
        tmp = status_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({**status, "pid": os.getpid()}))
        tmp.replace(status_file)  # atomic: the API never reads half a file

    final = run_assist(plan, Path(plan["folder"] or status_file.parent), headless=settings.assist_headless, keep_open=True, on_status=write)
    write(final)
    return 0 if final["state"] in ("ready", "closed") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
