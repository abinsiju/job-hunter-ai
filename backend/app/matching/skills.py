import math
import re

from app.ai.base import AIProvider

ALIASES = {
    "m365": "microsoft 365", "ms365": "microsoft 365", "o365": "microsoft 365", "office 365": "microsoft 365",
    "microsoft office 365": "microsoft 365", "ms 365": "microsoft 365",
    "ad": "active directory", "microsoft ad": "active directory", "ms active directory": "active directory",
    "microsoft active directory": "active directory", "js": "javascript", "ts": "typescript", "reactjs": "react", "react.js": "react",
    "nodejs": "node.js", "node": "node.js", "postgres": "postgresql", "aws": "amazon web services",
}


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9+#./ ]", " ", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class SimilarityMatcher:
    """Exact/alias match first, then embedding cosine. Embeddings are cached per process."""

    def __init__(self, ai: AIProvider, skill_threshold: float = 0.65):
        self.ai, self.skill_threshold = ai, skill_threshold
        self._cache: dict[str, list[float]] = {}

    async def _vec(self, text: str) -> list[float]:
        t = norm(text)
        if t not in self._cache:
            self._cache[t] = await self.ai.embed(t)
        return self._cache[t]

    async def similarity(self, a: str, b: str) -> float:
        if norm(a) == norm(b):
            return 1.0
        return cosine(await self._vec(a), await self._vec(b))

    async def match_skill(self, job_skill: str, candidate_skills: list[str]) -> tuple[str | None, float]:
        best, best_s = None, 0.0
        for c in candidate_skills:
            s = await self.similarity(job_skill, c)
            if s > best_s:
                best, best_s = c, s
        return (best, best_s) if best_s >= self.skill_threshold else (None, best_s)

    async def best_target(self, title: str, targets: list[str]) -> tuple[str | None, float]:
        t = norm(title)
        for tg in targets:  # phrase contained in the title counts as a direct hit
            if norm(tg) in t:
                return tg, 1.0
        best, best_s = None, 0.0
        for tg in targets:
            s = await self.similarity(title, tg)
            if s > best_s:
                best, best_s = tg, s
        return best, best_s
