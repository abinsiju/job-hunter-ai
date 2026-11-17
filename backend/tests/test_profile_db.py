import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.db.models import ProfileFact
from app.db.seed import seed_facts
from app.documents.facts import check_document_text, document_facts, form_facts
from app.matching.loader import load_candidate_profile

SEED = Path(__file__).resolve().parents[2] / "data" / "profile_seed.json"
pytestmark = pytest.mark.skipif(not SEED.exists(), reason="data/profile_seed.json not present")


@pytest.fixture(scope="module")
def engine():
    url = settings.database_url.rsplit("/", 1)[0] + "/jobhunter_test"
    e = create_engine(url)
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    yield e
    Base.metadata.drop_all(e)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        s.execute(text("truncate profile_facts, applications restart identity"))
        s.commit()
        seed_facts(s, json.loads(SEED.read_text()))
        yield s


def test_every_row_has_status_source_and_verified_rows_have_date(session):
    rows = session.query(ProfileFact).all()
    assert len(rows) > 50
    for r in rows:
        assert r.status in ("verified", "needs_confirmation") and r.source
        assert (r.last_confirmed is not None) == (r.status == "verified")


def test_db_rejects_verified_without_date(session):
    session.add(ProfileFact(category="x", key="y", value={}, status="verified", source="t", visibility="document"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_documents_only_get_verified_document_facts(session):
    facts = document_facts(session)
    assert facts and all(f.status == "verified" and f.visibility == "document" for f in facts)
    keys = {(f.category, f.key) for f in facts}
    assert sum(c == "skill" for c, _ in keys) == 24  # all 24 skills are now verified
    assert not any(c in ("work_rights", "eligibility", "referee", "applied_employer", "guardrail") for c, _ in keys)
    assert ("education", "uni_bit") in keys and ("employment", "acme_packaging") in keys


def test_forms_can_see_referees_and_work_rights_but_not_unverified(session):
    cats = {f.category for f in form_facts(session)}
    assert {"referee", "work_rights"} <= cats
    assert all(f.status == "verified" for f in form_facts(session))


def test_skill_levels_recorded_20_basic_rest_strong(session):
    skills = session.query(ProfileFact).filter_by(category="skill").order_by(ProfileFact.id).all()
    assert len(skills) == 24 and all(f.status == "verified" and f.last_confirmed == date(2026, 11, 9) for f in skills)
    levels = {f.key: f.value["level"] for f in skills}
    assert levels.pop("3D printers / laser cutters") == "basic"
    assert set(levels.values()) == {"strong"} and len(levels) == 23


def test_a_skill_that_loses_verification_leaves_documents(session):
    f = session.query(ProfileFact).filter_by(category="skill", key="Python").one()
    assert f in document_facts(session)
    f.status, f.last_confirmed = "needs_confirmation", None
    session.commit()
    assert f not in document_facts(session)


def test_forbidden_job_titles_are_caught(session):
    assert check_document_text(session, "Worked as a Network & Systems Administrator at Acme") == ["Network & Systems Administrator"]
    assert check_document_text(session, "Provided IT support and assisted with VLAN configuration") == []


def test_seed_is_insert_only(session):
    f = session.query(ProfileFact).filter_by(category="identity", key="name").one()
    f.value = "Changed"
    session.commit()
    added, skipped = seed_facts(session, json.loads(SEED.read_text()))
    assert added == 0 and skipped > 50
    assert session.query(ProfileFact).filter_by(key="name").one().value == "Changed"


def test_scoring_profile_loads_from_facts(session):
    p = load_candidate_profile(session)
    assert p.degree_status == "in_progress" and p.has_licence_and_car
    assert p.relevant_years == 0.67  # Acme 12 months x 0.5 + Globex 2 months x 1.0
    assert p.certifications == []
    assert p.skills["Python"] == "verified" and len(p.skills) == 24
    from app.tracker.seed import seed_applied
    assert p.applied_employers == []  # nothing in the tracker yet
    seed_applied(session, [{"employer": "Example Studios"}, {"employer": "Example Recruiters", "title": "Service Desk Analyst"}])
    assert load_candidate_profile(session).applied_employers == ["Example Studios", "Example Recruiters"]


def test_safecall_is_not_a_separate_project_and_answers_are_recorded(session):
    assert session.query(ProfileFact).filter_by(key="safecall").count() == 0
    exampletel = session.query(ProfileFact).filter_by(key="capstone_network_console").one()
    assert "SafeCall" in exampletel.value["customer_scenario"] and "fictional" in exampletel.value["customer_scenario"] and exampletel.status == "verified"
    cert = session.query(ProfileFact).filter_by(category="certification").one()
    assert cert.status == "verified" and cert.value == {"items": []}
    assert session.query(ProfileFact).filter_by(key="globex_australia").one().value["start"] == "2026-08"


def test_telstra_capstone_verified_with_contributions_and_not_employment(session):
    t = session.query(ProfileFact).filter_by(key="capstone_network_console").one()
    assert t.status == "verified" and t.visibility == "document" and t.last_confirmed is not None
    assert t.value["role"] == "UX Designer" and t.value["team_size"] == 5 and len(t.value["contributions"]) == 6
    assert "NOT ExampleTel employment" in t.notes and t.category == "project"  # never in an employment section


def test_jigjas_title_is_warehouse_operator_with_it_as_beyond_core_duties(session):
    j = session.query(ProfileFact).filter_by(key="acme_packaging").one()
    assert j.status == "verified" and j.value["title"] == "Warehouse Operator"
    assert j.value["resume_heading"] == "Warehouse Operator — Acme Packaging, Workville VIC | Feb 2024 – Jan 2025"
    assert "beyond the core role" in j.value["framing"] and len(j.value["duties_beyond_core_role"]) == 3
    assert session.query(ProfileFact).filter_by(key="jigjas_official_payslip_title").count() == 0
    # the only place the retired title may still appear is the forbidden-terms guardrail
    hits = [f for f in session.query(ProfileFact).all() if "Warehouse Operations" in json.dumps(f.value) + (f.notes or "")]
    assert {(f.category, f.key) for f in hits} <= {("guardrail", "forbidden_terms"), ("employment", "acme_packaging")}
    assert check_document_text(session, "Warehouse Operations & IT Support at Acme") == ["Warehouse Operations & IT Support"]


def test_venuevendors_verified_with_small_team_wording(session):
    v = session.query(ProfileFact).filter_by(key="venuehub").one()
    assert v.status == "verified" and v.value["document_wording"] == "small team" and "Next.js" in v.value["stack"]
    assert any(f.key == "venuehub" for f in document_facts(session))


def test_recipe_planner_solo_with_confirmed_description_only(session):
    p = session.query(ProfileFact).filter_by(key="recipe_planner").one()
    assert p.status == "verified" and p.value["team"] == "solo" and p.value["stack"] == ["Java", "SQLite"]
    feats = session.query(ProfileFact).filter_by(key="recipe_planner_features").one()
    assert feats.status == "verified" and feats.value == {"description": "Solo bio-waste website built with Java and SQLite"}
    assert feats in document_facts(session)


def test_tcs_forage_dates_recorded_and_completion_row_removed(session):
    t = session.query(ProfileFact).filter_by(key="virtual_internship").one()
    assert (t.value["start"], t.value["end"]) == ("2023-06", "2023-09")
    assert session.query(ProfileFact).filter_by(key="tcs_forage_completion_date").count() == 0


def test_grounds_maintenance_job_is_not_in_the_profile(session):
    assert session.query(ProfileFact).filter(ProfileFact.key.ilike("%grounds%")).count() == 0
    assert "grounds" not in json.dumps([f.value for f in session.query(ProfileFact).all()]).lower()


def test_jigjas_counts_half_and_flaschengeist_full(session):
    jig = session.query(ProfileFact).filter_by(key="acme_packaging").one()
    fla = session.query(ProfileFact).filter_by(key="globex_australia").one()
    assert jig.value["it_relevance_factor"] == 0.5 and "it_relevance_factor" not in fla.value


from app.documents.facts import assert_document_clean
from app.documents.guardrails import ForbiddenClaimError

OODIE_AND_AI_CONTRACT = [
    "Worked at ExampleBrand in customer support", "Previously employed by The OODIE", "examplebrand's warehouse team",
    "Worked as an AI contractor", "Subcontracted on machine learning projects", "ML subcontracting for a startup",
    "Contractor work in artificial intelligence", "Freelance contract work training AI models", "AI model training as a sub-contractor",
]
CLEAN = [
    "Provided IT support and assisted with VLAN configuration", "Built an AI chat assistant for a university project",
    "Technical Consultant at Globex Australia", "Warehouse Operator at Acme Packaging", "Goodies and cookies for the team",
    "Machine learning coursework at Example University", "Contract renewal handled by HR",
]


@pytest.mark.parametrize("text", OODIE_AND_AI_CONTRACT)
def test_oodie_and_ai_contracting_claims_are_blocked_in_documents(session, text):
    assert check_document_text(session, text), text
    with pytest.raises(ForbiddenClaimError):
        assert_document_clean(session, text)


@pytest.mark.parametrize("text", CLEAN)
def test_legitimate_text_is_not_blocked(session, text):
    assert check_document_text(session, text) == [], text
    assert_document_clean(session, text)


def test_guardrails_hold_even_if_db_guardrail_rows_are_missing(session):
    session.query(ProfileFact).filter_by(category="guardrail").delete()
    session.commit()
    assert check_document_text(session, "Worked at ExampleBrand")
    assert check_document_text(session, "AI contractor")


def test_profile_rows_cannot_assert_forbidden_claims(session):
    bad = [
        dict(category="employment", key="examplebrand", value={"employer": "ExampleBrand"}),
        dict(category="employment", key="x", value={"employer": "Acme", "duties": ["ML subcontracting"]}),
        dict(category="project", key="y", value={}, notes="contractor work on AI models"),
    ]
    for b in bad:
        row = dict(status="needs_confirmation", source="t", visibility="document", last_confirmed=None, **b)
        with pytest.raises(ForbiddenClaimError):
            seed_facts(session, [row])
    assert session.query(ProfileFact).filter_by(key="examplebrand").count() == 0


def test_seed_is_all_or_nothing_when_one_row_is_forbidden(session):
    n = session.query(ProfileFact).count()
    ok = dict(category="skill", key="Zed", value={}, status="needs_confirmation", source="t", visibility="document", last_confirmed=None)
    bad = dict(ok, key="ExampleBrand job", value={"employer": "ExampleBrand"})
    with pytest.raises(ForbiddenClaimError):
        seed_facts(session, [ok, bad])
    assert session.query(ProfileFact).count() == n


def test_no_existing_profile_row_asserts_forbidden_claims(session):
    from app.documents.guardrails import find_forbidden
    for f in session.query(ProfileFact).filter(ProfileFact.category != "guardrail"):
        assert find_forbidden(f"{f.key} {json.dumps(f.value)} {f.notes or ''}") == [], (f.category, f.key)


def test_power_bi_and_aws_renamed_and_still_strong(session):
    names = {f.key: f.value["level"] for f in session.query(ProfileFact).filter_by(category="skill")}
    assert names["Power BI"] == "strong" and names["AWS"] == "strong"
    assert "Power BI (basics)" not in names and "AWS fundamentals" not in names and len(names) == 24
