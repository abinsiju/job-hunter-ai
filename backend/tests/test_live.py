"""Needs Ollama running: RUN_LIVE=1 uv run pytest tests/test_live.py -s"""
import os

import pytest

from app.ai.factory import get_provider
from app.matching.extract import extract_job
from tests.fixtures.jobs import EXTRACT, RAW

pytestmark = [pytest.mark.live, pytest.mark.skipif(not os.getenv("RUN_LIVE"), reason="set RUN_LIVE=1")]


@pytest.mark.parametrize("key", list(RAW))
async def test_llm_extraction_matches_expected_key_fields(key):
    got, want = await extract_job(get_provider(), RAW[key]), EXTRACT[key]
    print(f"\n{key}: {got.model_dump(mode='json', exclude_defaults=True)}")
    assert set(got.work_rights_requirements) == set(want.work_rights_requirements)
    assert got.employment_type == want.employment_type
    assert bool(got.requires_drivers_licence) == bool(want.requires_drivers_licence)
    assert got.min_years_experience == want.min_years_experience
