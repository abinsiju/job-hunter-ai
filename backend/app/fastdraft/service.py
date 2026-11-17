"""Paste an ad, get a resume and cover letter in seconds. Reads the ad with rules (no language model), scores and builds with the same verified-facts engine
as the full draft, then reports how well an ATS will match it. Hard ceiling: 10 seconds."""
import asyncio
import hashlib
import re
import time
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.ai.base import AIProvider
from app.db.models import Listing
from app.documents.factbook import FactBook
from app.fastdraft.ats import ats_report
from app.fastdraft.extract import ad_capabilities, extract_fast
from app.pipeline import draft

BUDGET = 10.0
PROBE_TIMEOUT = 3.0


class LexicalAI:
    """Fallback when the local embedding model is not answering: hashed bag-of-words vectors. Weaker matching, same safety (only verified facts are ever used)."""

    async def embed(self, text: str) -> list[float]:
        v = [0.0] * 256
        for w in re.findall(r"[a-z0-9+#.]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1.0
        return v

    async def chat(self, *a, **k):  # pragma: no cover
        raise RuntimeError("not available in quick mode")


class TooSlow(Exception):
    pass


async def fast_draft(session: Session, ai: AIProvider, text: str, today: date, out_root: Path, *, url: str | None = None, source: str = "quick", budget: float = BUDGET, title: str = "", employer: str = "") -> dict:
    t0 = time.perf_counter()
    marks: dict[str, int] = {}

    def mark(k: str) -> None:
        marks[k] = round((time.perf_counter() - t0) * 1000)

    book = FactBook.from_session(session)
    skills = [n for n, _ in book.verified_skills()]
    job = extract_fast(text, skills, title, employer)
    mark("read_ad_ms")
    engine = "local embeddings"
    try:
        await asyncio.wait_for(ai.embed("probe"), PROBE_TIMEOUT)
    except Exception:  # noqa: BLE001  (Ollama down or slow: keep going with keyword matching rather than miss the deadline)
        ai, engine = LexicalAI(), "keyword matching (the local AI was not answering)"
    mark("check_ai_ms")
    try:
        result = await asyncio.wait_for(draft(text, session, ai, today, out_root, docs=True, source=source, url=url, job=job), max(0.05, budget - (time.perf_counter() - t0)))
    except asyncio.TimeoutError:
        raise TooSlow(f"This took longer than {int(budget)} seconds, so I stopped. Try again, or use Draft studio for the full read.") from None
    mark("draft_ms")
    ats = None
    if result.resume is not None and "resume.pdf" in result.files:
        corpus = [d for (d,) in session.query(Listing.description).limit(300) if d]
        ats = ats_report(job, text, result.resume, skills, result.files["resume.pdf"].read_bytes(), corpus, extra_terms=ad_capabilities(text))
    mark("ats_ms")
    elapsed = round((time.perf_counter() - t0) * 1000)
    return {"result": result, "ats": ats, "timings": marks, "elapsed_ms": elapsed, "within_budget": elapsed <= budget * 1000, "engine": engine}
