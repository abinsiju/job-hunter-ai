import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import cli
from app.core.config import settings
from app.db.base import Base
from app.db.models import Application, ProfileFact
from app.db.seed import seed_facts
from app.matching.schema import JobExtract
from app.pipeline import draft
from app.tracker import normalize as N
from app.tracker.seed import EVIDENCE, seed_applied
from app.tracker.service import (DuplicateVacancyError, InvalidTransition, TrackerError, applied_employers, check_duplicate,
                                 create_or_refresh_draft, update_status)
from tests.conftest import PipeAI
from tests.fixtures.jobs import EXTRACT, RAW

SEED = Path(__file__).resolve().parents[2] / "data" / "profile_seed.json"
APPLIED_SEED = Path(__file__).resolve().parents[2] / "data" / "applied_seed.json"
TODAY = date(2026, 11, 9)


@pytest.fixture(scope="module")
def engine():
    e = create_engine(settings.database_url.rsplit("/", 1)[0] + "/jobhunter_test")
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    yield e
    Base.metadata.drop_all(e)


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        s.execute(text("truncate profile_facts, applications restart identity"))
        s.commit()
        yield s


@pytest.fixture
def pdb(db):
    if not SEED.exists():
        pytest.skip("data/profile_seed.json not present")
    seed_facts(db, json.loads(SEED.read_text()))
    return db


def mk(db, employer="Acme Pty Ltd", title="IT Support Officer", location="Melbourne VIC", url=None, **kw):
    return create_or_refresh_draft(db, employer=employer, title=title, location=location, url=url, source="test", **kw)[0]


def apply(db, a, evidence="Confirmation email: Application received #A123"):
    update_status(db, a.id, "approved")
    return update_status(db, a.id, "applied", evidence=evidence)


# ---------------------------------------------------------------- normalisation
def test_normalisation_recognises_the_same_vacancy_in_different_words():
    assert N.norm_employer("Example Studios Pty Ltd") == N.norm_employer("example studios")
    assert N.norm_title("IT Support Officer (Part-time, 20 hrs/week)") == N.norm_title("it support officer")
    assert N.norm_title("Service Desk Analyst - Full-time - Melbourne CBD") == "service desk analyst"
    assert N.norm_location("Melbourne VIC") == N.norm_location("melbourne, Victoria") == "melbourne"
    assert N.locations_compatible("melbourne", "werribee melbourne") and N.locations_compatible("", "sydney")
    assert not N.locations_compatible("melbourne", "sydney")
    assert N.fingerprint("Acme Ltd", "IT Support (Part-time)", "Melbourne VIC") == N.fingerprint("ACME", "it support", "melbourne")
    assert N.fingerprint("Acme", "IT Support", "Melbourne") != N.fingerprint("Acme", "IT Support", "Sydney")


def test_url_normalisation_ignores_tracking_and_formatting():
    a = N.norm_url("https://www.example.com/jobs/123/?utm_source=x&gclid=1&id=9")
    assert a == N.norm_url("http://example.com/jobs/123?id=9") == "example.com/jobs/123?id=9"
    assert N.norm_url("https://example.com/jobs/124") != N.norm_url("https://example.com/jobs/123") and N.norm_url("") is None


# ---------------------------------------------------------------- drafting
def test_draft_row_has_status_timestamps_history_and_document_paths(db):
    a = mk(db, doc_paths={"resume.pdf": Path("/x/resume.pdf")}, match_score=94.5, gate_status="PASS")
    assert a.status == "drafted" and a.drafted_at and a.approved_at is None and a.applied_at is None
    assert a.doc_paths == {"resume.pdf": "/x/resume.pdf"} and a.match_score == 94.5 and a.gate_status == "PASS"
    assert [h["status"] for h in a.history] == ["drafted"] and a.evidence is None and len(a.fingerprint) == 64


def test_redrafting_the_same_vacancy_reuses_the_row_even_with_different_wording(db):
    a = mk(db, doc_paths={"resume.pdf": "/old"})
    b = mk(db, employer="ACME", title="IT Support Officer (Part-time)", location="Melbourne", doc_paths={"resume.pdf": "/new"})
    assert a.id == b.id and db.query(Application).count() == 1 and b.doc_paths == {"resume.pdf": "/new"}


def test_new_documents_invalidate_an_earlier_approval(db):
    a = mk(db)
    update_status(db, a.id, "approved")
    b = mk(db)
    assert b.id == a.id and b.status == "drafted" and "documents regenerated" in b.history[-1]["note"]


# ---------------------------------------------------------------- duplicates
@pytest.mark.parametrize("employer,title,location", [
    ("Acme Pty Ltd", "IT Support Officer", "Melbourne VIC"),
    ("ACME", "IT Support Officer (Part-time, 20 hrs/week)", "Werribee, Melbourne VIC"),
    ("Acme Limited", "it support officer - full time", None),
])
def test_same_vacancy_already_applied_is_a_hard_block(db, employer, title, location):
    a = apply(db, mk(db))
    chk = check_duplicate(db, employer, title, location, None)
    assert chk.blocking.id == a.id
    with pytest.raises(DuplicateVacancyError) as e:
        create_or_refresh_draft(db, employer=employer, title=title, location=location)
    assert "Duplicate applications are blocked" in str(e.value) and db.query(Application).count() == 1


def test_same_listing_url_blocks_even_when_the_title_changed(db):
    apply(db, mk(db, url="https://jobs.example.com/view/42?utm_campaign=a"))
    with pytest.raises(DuplicateVacancyError):
        create_or_refresh_draft(db, employer="Acme", title="Totally different title", location="Sydney",
                                url="https://www.jobs.example.com/view/42/")


def test_same_employer_different_role_warns_but_does_not_block(db):
    a = apply(db, mk(db))
    chk = check_duplicate(db, "Acme", "Network Technician", "Melbourne", None)
    assert chk.blocking is None and [x.id for x in chk.same_employer] == [a.id]
    b = mk(db, title="Network Technician")
    assert b.id != a.id and db.query(Application).count() == 2


def test_same_title_at_a_different_location_is_not_the_same_vacancy(db):
    apply(db, mk(db, location="Melbourne"))
    assert check_duplicate(db, "Acme", "IT Support Officer", "Sydney NSW", None).blocking is None


def test_different_employer_same_title_is_not_a_duplicate(db):
    apply(db, mk(db))
    chk = check_duplicate(db, "Globex", "IT Support Officer", "Melbourne", None)
    assert chk.blocking is None and chk.same_employer == [] and chk.reusable is None


def test_employer_only_records_can_only_warn(db):
    seed_applied(db, [{"employer": "Example Hospital"}])  # role and location unknown
    chk = check_duplicate(db, "Example Hospital", "IT Support Officer", "Heidelberg VIC", None)
    assert chk.blocking is None and len(chk.same_employer) == 1  # we cannot know it is the same role


def test_seeded_example_recruiters_service_desk_analyst_is_a_hard_block(db):
    seed_applied(db, [{"employer": "Example Recruiters", "title": "Service Desk Analyst"}])  # location unknown
    chk = check_duplicate(db, "Example Recruiters Australia", "Service Desk Analyst (Contract)", "Melbourne VIC", None)
    assert chk.blocking and chk.blocking.employer == "Example Recruiters"
    assert check_duplicate(db, "Example Recruiters", "Network Engineer", "Melbourne", None).blocking is None


def test_unknown_employer_never_collides_or_blocks(db):
    a = create_or_refresh_draft(db, employer="", title="IT Support", location="Melbourne")[0]
    b = create_or_refresh_draft(db, employer="", title="IT Support", location="Melbourne", url="https://x.example/1")[0]
    assert a.id != b.id and a.employer == "(unknown employer)"


def test_a_legacy_row_with_different_wording_still_blocks_approval(db):
    old = apply(db, mk(db, location=None))
    legacy = Application(fingerprint="legacy", employer="Acme", title="IT Support Officer", location="Melbourne", employer_norm="acme",
                         title_norm="it support officer", location_norm="melbourne", source="old", status="drafted", history=[])
    db.add(legacy)
    db.commit()
    with pytest.raises(DuplicateVacancyError) as e:
        update_status(db, legacy.id, "approved")
    assert e.value.existing.id == old.id and db.get(Application, legacy.id).status == "drafted"


# ---------------------------------------------------------------- statuses
def test_full_lifecycle_sets_timestamps_and_history(db):
    a = mk(db)
    update_status(db, a.id, "approved", note="reviewed the documents")
    update_status(db, a.id, "applied", evidence="Seek confirmation email, ref 8841", note="submitted via Seek")
    update_status(db, a.id, "interview")
    a = update_status(db, a.id, "offer")
    assert (a.approved_at and a.applied_at and a.interview_at and a.outcome_at) and a.status == "offer"
    assert [h["status"] for h in a.history] == ["drafted", "approved", "applied", "interview", "offer"]
    assert a.evidence == "Seek confirmation email, ref 8841" and "reviewed the documents" in a.notes and "submitted via Seek" in a.notes


@pytest.mark.parametrize("start,target", [
    ("drafted", "applied"), ("drafted", "interview"), ("approved", "interview"), ("applied", "offer"),
    ("applied", "drafted"), ("interview", "applied"), ("rejected", "interview"), ("offer", "rejected"),
])
def test_invalid_transitions_are_refused(db, start, target):
    a = mk(db)
    path = {"drafted": [], "approved": ["approved"], "applied": ["approved", "applied"], "interview": ["approved", "applied", "interview"],
            "rejected": ["approved", "applied", "rejected"], "offer": ["approved", "applied", "interview", "offer"]}[start]
    for st in path:
        update_status(db, a.id, st, evidence="confirmation ref 12345" if st == "applied" else None)
    with pytest.raises(InvalidTransition):
        update_status(db, a.id, target, evidence="confirmation ref 12345")
    assert db.get(Application, a.id).status == start


def test_unknown_status_same_status_and_missing_id_are_refused(db):
    a = mk(db)
    for bad in ("hired", "DRAFTED"):
        with pytest.raises(InvalidTransition):
            update_status(db, a.id, bad)
    with pytest.raises(InvalidTransition):
        update_status(db, a.id, "drafted")
    with pytest.raises(TrackerError):
        update_status(db, 9999, "approved")


@pytest.mark.parametrize("evidence", [None, "", "   ", "ok"])
def test_applied_requires_real_evidence(db, evidence):
    a = mk(db)
    update_status(db, a.id, "approved")
    with pytest.raises(TrackerError, match="evidence"):
        update_status(db, a.id, "applied", evidence=evidence)
    assert db.get(Application, a.id).status == "approved" and db.get(Application, a.id).applied_at is None


def test_database_itself_rejects_a_submitted_row_without_evidence(db):
    a = mk(db)
    with pytest.raises(IntegrityError):
        db.execute(text("update applications set status='applied' where id=:i"), {"i": a.id})
        db.commit()
    db.rollback()
    with pytest.raises(IntegrityError):
        db.execute(text("update applications set status='banana' where id=:i"), {"i": a.id})
        db.commit()
    db.rollback()


def test_database_enforces_unique_fingerprint(db):
    a = mk(db)
    db.add(Application(fingerprint=a.fingerprint, employer="x", employer_norm="x", source="t", status="drafted", history=[]))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_withdrawn_before_applying_can_be_redrafted_but_after_applying_never(db):
    a = mk(db)
    update_status(db, a.id, "withdrawn", note="changed my mind")
    assert mk(db).status == "drafted" and db.query(Application).count() == 1  # reopened: it was never sent
    b = apply(db, mk(db, title="Help Desk Analyst"))
    update_status(db, b.id, "withdrawn")
    with pytest.raises(DuplicateVacancyError):
        create_or_refresh_draft(db, employer="Acme", title="Help Desk Analyst", location="Melbourne")
    with pytest.raises(InvalidTransition):
        update_status(db, b.id, "drafted")


def test_applied_employers_only_lists_submitted_applications(db):
    mk(db, employer="Draft Only Co")
    apply(db, mk(db, employer="Sent Co"))
    assert applied_employers(db) == ["Sent Co"]


# ---------------------------------------------------------------- seed
def test_seeded_rows_are_applied_with_explicit_evidence_note_and_seed_is_idempotent(db):
    rows = [{"employer": "Example Charity"}, {"employer": "Example Recruiters", "title": "Service Desk Analyst"}]
    assert seed_applied(db, rows) == (2, 0) and seed_applied(db, rows) == (0, 2)
    for a in db.query(Application):
        assert a.status == "applied" and a.evidence == EVIDENCE and a.source == "user-reported" and a.applied_at is None
    assert "no confirmation on file" in EVIDENCE


@pytest.mark.skipif(not APPLIED_SEED.exists(), reason="data/applied_seed.json not present")
def test_the_real_applied_list_seeds_twenty_rows(db):
    assert seed_applied(db, json.loads(APPLIED_SEED.read_text())) == (20, 0)
    names = {a.employer for a in db.query(Application)}
    assert {"Example Studios", "Example Recruiters", "ExampleLabs", "Example Board", "Example Charity", "Example College"} <= names


# ---------------------------------------------------------------- pipeline
async def test_draft_creates_a_drafted_row_and_redraft_reuses_it(pdb, tmp_path):
    r = await draft(RAW["it_support_pt"], pdb, PipeAI(EXTRACT["it_support_pt"]), TODAY, tmp_path, source="adzuna", url="https://x.example/job/1")
    a = r.application
    assert a and a.status == "drafted" and a.source == "adzuna" and a.match_score == r.match.score and a.gate_status == "PASS"
    assert set(a.doc_paths) == {"resume.docx", "resume.pdf", "cover_letter.docx", "cover_letter.pdf"}
    assert all(Path(p).exists() for p in a.doc_paths.values())
    assert json.loads((next(tmp_path.iterdir()) / "match.json").read_text())["tracker_id"] == a.id
    r2 = await draft(RAW["it_support_pt"], pdb, PipeAI(EXTRACT["it_support_pt"]), TODAY, tmp_path, url="https://x.example/job/1")
    assert r2.application.id == a.id and pdb.query(Application).count() == 1


async def test_score_only_and_gate_fail_create_no_row(pdb, tmp_path):
    r = await draft(RAW["it_support_pt"], pdb, PipeAI(EXTRACT["it_support_pt"]), TODAY, tmp_path, docs=False)
    assert r.application is None and pdb.query(Application).count() == 0
    r = await draft(RAW["aps_role"], pdb, PipeAI(EXTRACT["aps_role"]), TODAY, tmp_path)
    assert r.skipped_reason and pdb.query(Application).count() == 0


async def test_draft_is_blocked_once_the_vacancy_has_been_applied_to(pdb, tmp_path):
    r = await draft(RAW["it_support_pt"], pdb, PipeAI(EXTRACT["it_support_pt"]), TODAY, tmp_path)
    apply(pdb, r.application)
    before = sorted(p.name for p in tmp_path.iterdir())
    r2 = await draft(RAW["it_support_pt"], pdb, PipeAI(EXTRACT["it_support_pt"]), TODAY, tmp_path)
    assert "Duplicate vacancy" in r2.skipped_reason and r2.files == {} and r2.application is None
    assert pdb.query(Application).count() == 1 and sorted(p.name for p in tmp_path.iterdir()) == before


async def test_same_employer_different_role_drafts_with_a_warning_and_a_gate_flag(pdb, tmp_path):
    r = await draft(RAW["it_support_pt"], pdb, PipeAI(EXTRACT["it_support_pt"]), TODAY, tmp_path)
    apply(pdb, r.application)
    other = EXTRACT["it_support_pt"].model_copy(update={"title": "Network Technician"})
    r2 = await draft(RAW["it_support_pt"], pdb, PipeAI(other), TODAY, tmp_path)
    assert r2.application and r2.application.id != r.application.id and r2.skipped_reason is None
    assert [a.id for a in r2.duplicates.same_employer] == [r.application.id]
    assert any("possible duplicate" in f for f in r2.match.gate.flags)


# ---------------------------------------------------------------- CLI (test database only)
@pytest.fixture
def clidb(engine, db, monkeypatch):
    monkeypatch.setattr(cli, "get_session", lambda: Session(engine))
    return db


def test_cli_track_list_show_and_update(clidb, capsys):
    seed_applied(clidb, [{"employer": "Example Charity"}, {"employer": "Example Recruiters", "title": "Service Desk Analyst"}])
    a = mk(clidb)
    assert cli.main(["track", "list"]) == 0
    out = capsys.readouterr().out
    assert "Example Charity" in out and "Example Recruiters" in out and "Service Desk Analyst" in out and "applied: 2" in out and "drafted: 1" in out
    assert cli.main(["track", "list", "--status", "drafted"]) == 0
    assert "Acme" in capsys.readouterr().out
    assert cli.main(["track", "update", str(a.id), "approved", "--note", "ok"]) == 0
    assert "now approved" in capsys.readouterr().out
    assert cli.main(["track", "update", str(a.id), "applied", "--evidence", "Email confirmation ref 77123"]) == 0
    assert cli.main(["track", "show", str(a.id)]) == 0
    shown = capsys.readouterr().out
    assert "Email confirmation ref 77123" in shown and "applied" in shown


def test_cli_refuses_applied_without_evidence_invalid_moves_and_unknown_ids(clidb, capsys):
    a = mk(clidb)
    cli.main(["track", "update", str(a.id), "approved"])
    capsys.readouterr()
    assert cli.main(["track", "update", str(a.id), "applied"]) == 2
    assert "needs --evidence" in capsys.readouterr().err
    assert cli.main(["track", "update", str(a.id), "offer"]) == 2
    assert "Cannot move" in capsys.readouterr().err
    assert cli.main(["track", "update", "9999", "approved"]) == 2
    clidb.expire_all()  # the CLI used its own session
    assert clidb.get(Application, a.id).status == "approved"


def test_ampersand_and_the_word_and_are_the_same_in_employers_and_titles():
    assert N.norm_employer("Study & Work Pty Ltd") == N.norm_employer("Study and Work")
    assert N.norm_title("Network & Systems Support") == N.norm_title("Network and Systems Support")
    assert N.fingerprint("Smith & Co", "IT Support", "Melbourne") == N.fingerprint("Smith and Co", "IT Support", "Melbourne")
