"""Connectivity + model smoke tests. Run: cd backend && uv run python ../scripts/check_env.py"""
import asyncio
import json
import sys
import time

import psycopg
import redis

sys.path.insert(0, ".")
from app.ai.factory import get_provider  # noqa: E402
from app.core.config import settings  # noqa: E402

JOB = "Junior Software Engineer, Melbourne. Python, FastAPI, PostgreSQL, Docker. 1-2 years experience. Australian citizens/PR only."
PROFILE = "Skills: Python, SQL, Docker, React. 1 year experience as support engineer. Work rights: not stated."


def ok(name: str, detail: str = "") -> None:
    print(f"PASS  {name} {detail}")


async def main() -> int:
    fails = 0
    try:
        with psycopg.connect(settings.database_url.replace("+psycopg", "")) as c:
            ok("postgres", c.execute("select version()").fetchone()[0].split(",")[0])
            c.execute("create extension if not exists vector")
            ok("pgvector extension")
    except Exception as e:
        fails += 1
        print("FAIL  postgres", type(e).__name__)
    try:
        ok("redis ping", str(redis.from_url(settings.redis_url).ping()))
    except Exception as e:
        fails += 1
        print("FAIL  redis", type(e).__name__)
    ai = get_provider()
    try:
        v = await ai.embed("python backend developer melbourne")
        ok("embedding", f"dim={len(v)}")
    except Exception as e:
        fails += 1
        print("FAIL  embedding", type(e).__name__, e)
    try:
        t = time.time()
        out = await ai.chat(
            'You are a job-matching assistant. Never invent facts. Reply JSON: {"score":0-100,"matched":[],"gaps":[],"work_rights_risk":bool}',
            f"JOB:\n{JOB}\n\nCANDIDATE:\n{PROFILE}",
            json_mode=True,
        )
        ok("chat job-match", f"{time.time() - t:.1f}s -> {json.dumps(json.loads(out))[:300]}")
    except Exception as e:
        fails += 1
        print("FAIL  chat", type(e).__name__, e)
    return fails


sys.exit(asyncio.run(main()))
