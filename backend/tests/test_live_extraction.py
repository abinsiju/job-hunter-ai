import os

import pytest

from app.ai.factory import get_provider
from app.matching.extract import extract_job
from tests.fixtures.jobs import EXTRACT, RAW

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 (needs Ollama)")]


@pytest.mark.parametrize("key", list(RAW))
async def test_llm_extracts_gate_critical_fields(key):
    got = await extract_job(get_provider(), RAW[key])
    want = EXTRACT[key]
    assert set(got.work_rights_requirements) == set(want.work_rights_requirements)
    assert got.employment_type == want.employment_type
    assert bool(got.requires_drivers_licence) == want.requires_drivers_licence
