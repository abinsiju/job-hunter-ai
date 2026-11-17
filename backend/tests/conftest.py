from datetime import date

import pytest

from app.matching.profile import CandidateProfile

TODAY = date(2026, 11, 9)

# Deterministic stand-in for nomic-embed-text: keyword -> concept axis. Distinct concepts are orthogonal.
CONCEPTS = {
    "desktop": ["windows desktop", "pc troubleshooting", "desktop support", "end user computing"],
    "m365": ["microsoft office", "ms office", "sharepoint online"],
    "web": ["react", "react.js framework", "vue", "frontend"],
    "db": ["sql", "postgresql", "mysql"],
    "py": ["python", "scripting"],
    "net": ["vlan", "firewall rules", "networking"],
    "support": ["it support", "service desk", "help desk", "helpdesk", "technical support", "support analyst"],
    "dev": ["junior developer", "software developer", "graduate software engineer", "web developer"],
}
AXES = list(CONCEPTS)


class FakeAI:
    DIM = 512

    def __init__(self):
        self._axis: dict[str, int] = {}

    async def embed(self, text: str) -> list[float]:
        t = text.lower()
        v = [0.0] * (len(AXES) + self.DIM)
        hit = False
        for i, ax in enumerate(AXES):
            if any(k in t for k in CONCEPTS[ax]) or ax == t:
                v[i] = 1.0
                hit = True
        if not hit:  # every unknown text gets its own axis, so unrelated terms are exactly orthogonal (no collisions)
            v[len(AXES) + self._axis.setdefault(t, len(self._axis))] = 1.0
        return v

    async def chat(self, *a, **k):  # not used by scoring
        raise AssertionError("scoring must not call the LLM")


@pytest.fixture
def ai():
    return FakeAI()


@pytest.fixture
def profile():
    stated = ["Microsoft 365", "Active Directory", "SharePoint", "Python", "SQL", "React", "TypeScript",
              "Power BI", "Jira", "Figma"]
    return CandidateProfile(
        skills={s: "needs_confirmation" for s in stated},
        relevant_years=1.0,
        degree_status="in_progress",
        has_licence_and_car=True,
        applied_employers=["Example Studios", "Example Recruiters", "Example Hospital"],
    )


class PipeAI(FakeAI):
    """FakeAI embeddings + a canned extraction. Counts chat calls: only extraction may use the LLM."""

    def __init__(self, job):
        super().__init__()
        self.job, self.chat_calls = job, 0

    async def chat(self, system, user, *, json_mode=False, temperature=None, json_schema=None):
        self.chat_calls += 1
        assert json_schema is not None  # extraction only
        return self.job.model_dump_json()
