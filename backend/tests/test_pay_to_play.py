import json
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import cli
from app.blocklist import add_blocked, list_blocked, remove_blocked
from app.core.config import settings
from app.db.base import Base
from app.db.models import Listing, ProfileFact
from app.db.seed import seed_facts
from app.discovery.base import RawListing
from app.discovery.triage import ranked, triage_listing, upsert_listings
from app.documents.facts import document_facts
from app.matching.extract import PLACEMENT_PROGRAM_RE, detect_applicant_pays, extract_job
from app.matching.gate import evaluate_gate
from app.matching.loader import load_candidate_profile
from app.matching.schema import JobExtract
from tests.conftest import PipeAI
from tests.fixtures.jobs import EXTRACT

SEED = Path(__file__).resolve().parents[2] / "data" / "profile_seed.json"
TODAY = date(2026, 11, 9)

PAY_TO_PLAY = [
    "A program fee of $2,500 applies to secure your placement.",
    "Participants pay a placement fee before the internship starts.",
    "You will pay a one-off registration fee.",
    "You must pay tuition for the training component.",
    "There is an upfront cost of $990 for onboarding.",
    "Pay $1,200 to be matched with a host company.",
    "The cost of the program is $3,000, payable on acceptance.",
    "This is a fee-paying internship pathway.",
    "Your investment of $1,500 covers mentoring and placement.",
    "An administration fee is charged to candidates.",
]
NOT_PAY_TO_PLAY = [
    "There is no program fee. This is a paid internship.",
    "No application fee, free of charge for candidates.",
    "Candidates never pay a fee at any stage.",
    "Support staff with Microsoft 365. Pay: $32 per hour.",
    "We pay above award rates and cover your certification fees.",   # employer pays: not the applicant
    "Join our fee-free graduate program, paid from day one.",
    "Applications are free and there is no registration fee.",
]


@pytest.mark.parametrize("text_", PAY_TO_PLAY)
def test_applicant_pays_language_is_detected(text_):
    assert detect_applicant_pays(text_), text_


@pytest.mark.parametrize("text_", NOT_PAY_TO_PLAY)
def test_negated_or_employer_side_fee_language_is_not_flagged(text_):
    assert not detect_applicant_pays(text_), text_


def test_placement_agency_wording_is_recognised():
    assert PLACEMENT_PROGRAM_RE.search("we have successfully matched 6,000 interns, trainees, graduates and professionals")
    assert PLACEMENT_PROGRAM_RE.search("our internship program connects emerging talent with employers")
    assert not PLACEMENT_PROGRAM_RE.search("Support staff with PCs and Microsoft 365 at our logistics depot.")


class Canned:
    def __init__(self, job):
        self.job = job

    async def chat(self, *a, **k):
        return self.job.model_dump_json()


async def test_extraction_sets_the_flags_from_the_ad_text_not_from_the_model():
    liar = JobExtract(title="IT Support Intern", employment_type="internship", applicant_pays=False, placement_program=False)
    job = await extract_job(Canned(liar), "IT Support Intern. A program fee of $2,500 applies. We have matched 6,000 interns with employers.")
    assert job.applicant_pays and job.placement_program
    claims = JobExtract(title="IT Support Officer", applicant_pays=True, placement_program=True)  # model hallucinates
    job = await extract_job(Canned(claims), "IT Support Officer. Support staff with Microsoft 365. Pay: $32 per hour.")
    assert not job.applicant_pays and not job.placement_program


def gate(db_profile, **kw):
    return evaluate_gate(EXTRACT["it_support_pt"].model_copy(update=kw), db_profile, TODAY)


@pytest.fixture
def profile():
    from app.matching.profile import CandidateProfile
    return CandidateProfile(relevant_years=1.0, degree_status="in_progress", has_licence_and_car=True)


def test_applicant_pays_is_a_hard_fail(profile):
    g = gate(profile, applicant_pays=True)
    assert g.status == "FAIL" and any("asked to pay" in r for r in g.fail_reasons)
    assert gate(profile).status == "PASS"


def test_placement_program_internship_gets_a_strong_flag_but_is_not_failed_without_fee_evidence(profile):
    g = gate(profile, placement_program=True, employment_type="internship")
    assert any("often charge the candidate" in f for f in g.flags) and g.status != "FAIL"
    assert gate(profile, placement_program=True, applicant_pays=True).status == "FAIL"


def test_blocked_employer_fails_the_gate_with_fuzzy_name_match(profile):
    profile.blocked_employers = ["Study and Work"]
    assert gate(profile, employer="STUDY AND WORK Pty Ltd").status == "FAIL"
    assert any("blocked list" in r for r in gate(profile, employer="Study & Work").fail_reasons) or gate(profile, employer="Study and Work").status == "FAIL"
    assert gate(profile, employer="Westside Logistics").status == "PASS" and gate(profile, employer=None).status == "PASS"


# ---------------------------------------------------------------- with the database
@pytest.fixture(scope="module")
def engine():
    e = create_engine(settings.database_url.rsplit("/", 1)[0] + "/jobhunter_test")
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    yield e
    Base.metadata.drop_all(e)


@pytest.fixture
def db(engine):
    if not SEED.exists():
        pytest.skip("data/profile_seed.json not present")
    with Session(engine) as s:
        s.execute(text("truncate profile_facts, applications, listings restart identity cascade"))
        s.commit()
        seed_facts(s, json.loads(SEED.read_text()))
        yield s


def test_blocklist_add_list_remove_and_it_never_reaches_documents(db):
    assert add_blocked(db, "Study and Work", "placement agency, asks candidates to pay") is True
    assert add_blocked(db, "study & work pty ltd") is False and add_blocked(db, "STUDY AND WORK") is False  # same employer
    assert list_blocked(db) == {"Study and Work": "placement agency, asks candidates to pay"}
    f = db.query(ProfileFact).filter_by(category="preference", key="blocked_employers").one()
    assert f.visibility == "internal" and f.status == "verified" and f not in document_facts(db)
    assert load_candidate_profile(db).blocked_employers == ["Study and Work"]
    assert remove_blocked(db, "study and work") is True and remove_blocked(db, "study and work") is False and list_blocked(db) == {}
    with pytest.raises(ValueError):
        add_blocked(db, "  ")


def raw(**kw):
    d = dict(source="adzuna", source_id="1", url="https://x.example/1", title="IT Support Intern", employer="Study and Work", location="Melbourne",
             description="IT Support Intern. We have matched 6,000 interns with employers. A program fee of $2,000 applies.",
             description_complete=False, employment_hint="contract")
    return RawListing(**{**d, **kw})


async def test_a_fee_charging_listing_is_a_hard_fail_and_hidden_from_the_list(db):
    upsert_listings(db, [raw()])
    l = db.query(Listing).one()
    await triage_listing(db, PipeAI(EXTRACT["it_support_pt"].model_copy(update={"title": "IT Support Intern"})), l, TODAY)
    assert l.gate_status == "FAIL" and any("asked to pay" in r for r in l.fail_reasons)
    assert l not in ranked(db) and l in ranked(db, include_fail=True)


async def test_a_blocked_employer_listing_is_hidden_even_without_fee_language(db):
    add_blocked(db, "Study and Work")
    upsert_listings(db, [raw(description="IT Support Intern. Great opportunity for students.")])
    l = db.query(Listing).one()
    await triage_listing(db, PipeAI(EXTRACT["it_support_pt"]), l, TODAY)
    assert l.gate_status == "FAIL" and any("blocked list" in r for r in l.fail_reasons) and l not in ranked(db)


def test_cli_blocklist(db, engine, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_session", lambda: Session(engine))
    assert cli.main(["blocklist", "list"]) == 0 and "empty" in capsys.readouterr().out
    assert cli.main(["blocklist", "add", "Study and Work", "--reason", "pay-to-place"]) == 0 and "Blocked: Study and Work" in capsys.readouterr().out
    assert cli.main(["blocklist", "add", "study and work"]) == 0 and "Already blocked" in capsys.readouterr().out
    assert cli.main(["blocklist", "list"]) == 0 and "Study and Work  (pay-to-place)" in capsys.readouterr().out
    assert cli.main(["blocklist", "remove", "Study and Work"]) == 0 and "Unblocked" in capsys.readouterr().out
