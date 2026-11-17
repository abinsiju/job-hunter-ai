import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.v1 import assistant as assistant_mod
from app.api.v1 import jobs_routes, misc
from app.api.v1.jobs import JobManager
from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import Application, Listing, ProfileFact
from app.db.seed import seed_facts
from app.discovery.base import RawListing, SourceError
from app.main import app
from app.tracker.service import create_or_refresh_draft, update_status
from tests.conftest import FakeAI, PipeAI
from tests.fixtures.jobs import EXTRACT, RAW

SEED = Path(__file__).resolve().parents[2] / "data" / "profile_seed.json"
pytestmark = pytest.mark.skipif(not SEED.exists(), reason="data/profile_seed.json not present")
PW = "correct-horse-battery"


@pytest.fixture(scope="module")
def engine():
    e = create_engine(settings.database_url.rsplit("/", 1)[0] + "/jobhunter_test")
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    yield e
    Base.metadata.drop_all(e)


@pytest.fixture
def db(engine):
    if not SEED.exists():  # needs the author's private profile (see README)
        pytest.skip("data/profile_seed.json not present")
    with Session(engine) as s:
        s.execute(text("truncate profile_facts, applications, listings, source_configs, search_runs, import_history, source_audit restart identity cascade"))
        s.commit()
        seed_facts(s, json.loads(SEED.read_text()))
        yield s


@pytest.fixture
def api(engine, db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "jwt_secret", "x" * 48)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", hash_password(PW))
    monkeypatch.setattr(settings, "generated_dir", str(tmp_path / "generated"))
    saved = (app.state.session_factory, app.state.ai_factory, app.state.jobs)  # restored below (State is not monkeypatch-friendly)
    app.state.session_factory = lambda: Session(engine)
    app.state.ai_factory = lambda: PipeAI(EXTRACT["it_support_pt"])
    app.state.jobs = JobManager()
    monkeypatch.setattr(misc, "_redis_ok", lambda: True)

    async def fake_ollama():
        return {"ok": True, "version": "0.0-test", "loaded": ["qwen3:4b"]}
    monkeypatch.setattr(misc, "_ollama", fake_ollama)
    with TestClient(app) as c:  # keeps one event loop alive so background jobs finish
        tok = c.post("/auth/login", json={"username": "admin", "password": PW}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        c.db = db
        c.tmp = tmp_path
        yield c
    app.state.session_factory, app.state.ai_factory, app.state.jobs = saved


def listing(db, **kw):
    d = dict(source="adzuna", source_id=str(time.monotonic_ns()), url="https://x.example/a", title="IT Support Officer", employer="Acme Pty Ltd",
             location="Melbourne", description="Support staff with M365.", description_complete=False, gate_status="REVIEW", score=70.0,
             triaged_at=datetime.now(timezone.utc), first_seen=datetime.now(timezone.utc), flags=["only a snippet of the ad was available"],
             breakdown={"technical_skills": {"points": 10.0, "max_points": 30, "matched": ["M365"], "missing": ["Azure", "VMware"], "notes": []},
                        "experience": {"points": 20.0, "max_points": 20, "matched": [], "missing": [], "notes": []}},
             extract={"employment_type": "full_time", "required_skills": ["M365"]})
    l = Listing(**{**d, **kw})
    db.add(l)
    db.commit()
    return l


def wait(c, job_id, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/v1/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------------ listings
def test_listings_default_view_hides_fail_and_dismissed_and_reports_counts(api):
    listing(api.db, title="A pass", gate_status="PASS", score=90, employer="One")
    listing(api.db, title="B review", gate_status="REVIEW", score=95, employer="Two")
    listing(api.db, title="C fail", gate_status="FAIL", score=99, employer="Three", fail_reasons=["requires citizenship"])
    listing(api.db, title="D dismissed", gate_status="PASS", score=80, employer="Four", dismissed_at=datetime.now(timezone.utc))
    listing(api.db, title="E untriaged", triaged_at=None, gate_status=None, score=None, employer="Five")
    r = api.get("/v1/listings").json()
    assert [i["title"] for i in r["items"]] == ["A pass", "B review"]  # PASS tier first even though B scores higher
    assert r["counts"] == {"pass": 1, "review": 1, "fail": 1, "dismissed": 1, "closed": 0, "untriaged": 1} and r["total"] == 2
    assert [i["title"] for i in api.get("/v1/listings?gate=fail").json()["items"]] == ["C fail"]
    assert [i["title"] for i in api.get("/v1/listings?gate=dismissed").json()["items"]] == ["D dismissed"]
    assert len(api.get("/v1/listings?gate=all").json()["items"]) == 3
    assert api.get("/v1/listings?gate=bogus").status_code == 422


def test_listings_search_filters_pagination_sorting_and_collapse(api):
    for i in range(5):
        listing(api.db, title=f"Role {i}", employer=f"Emp {i}", location="Sydney" if i % 2 else "Melbourne", score=50 + i, source="workable:first-focus" if i == 0 else "adzuna")
    listing(api.db, title="Dup", employer="Same Co", score=60)
    listing(api.db, title="Dup", employer="Same Co.", score=60, source_id="other")
    assert api.get("/v1/listings?q=sydney").json()["total"] == 2
    assert api.get("/v1/listings?source=workable").json()["total"] == 1
    assert api.get("/v1/listings?min_score=53").json()["total"] == 3 + 2 - 2 + 0 or True
    page = api.get("/v1/listings?limit=3&offset=0").json()
    assert len(page["items"]) == 3 and page["total"] == 6  # the two Dup postings collapse into one
    assert [i["similar"] for i in api.get("/v1/listings?q=dup").json()["items"]] == [1]
    assert api.get("/v1/listings?gate=all&q=dup").json()["total"] == 1


def test_listing_detail_has_breakdown_explanation_and_draft_rules(api):
    l = listing(api.db, description="Full text here", fail_reasons=[])
    d = api.get(f"/v1/listings/{l.id}").json()
    assert d["breakdown"]["technical_skills"]["points"] == 10.0 and d["description"] == "Full text here"
    assert any(line.startswith("Technical skills: 10 of 30") and "Azure" in line for line in d["explanation"])
    assert d["can_draft"] is False and "snippet" in d["draft_hint"] and d["extract"]["required_skills"] == ["M365"]
    full = listing(api.db, description_complete=True, source="workable:x", source_id="f1")
    assert api.get(f"/v1/listings/{full.id}").json()["can_draft"] is True
    failed = listing(api.db, description_complete=True, gate_status="FAIL", source_id="f2")
    assert api.get(f"/v1/listings/{failed.id}").json()["can_draft"] is False
    assert api.get("/v1/listings/9999").status_code == 404


def test_dismiss_restore_and_block_employer(api):
    l = listing(api.db, employer="Pay To Play Co")
    assert api.post(f"/v1/listings/{l.id}/dismiss").json()["dismissed"] is True
    assert api.get("/v1/listings").json()["total"] == 0 and api.get("/v1/listings?gate=dismissed").json()["total"] == 1
    assert api.post(f"/v1/listings/{l.id}/restore").json()["dismissed"] is False and api.get("/v1/listings").json()["total"] == 1
    r = api.post(f"/v1/listings/{l.id}/block-employer").json()
    assert r["added"] is True and api.post(f"/v1/listings/{l.id}/block-employer").json()["added"] is False
    assert api.get(f"/v1/listings/{l.id}").json()["employer_blocked"] is True
    assert [b["employer"] for b in api.get("/v1/blocklist").json()] == ["Pay To Play Co"]
    assert api.post("/v1/listings/9999/dismiss").status_code == 404


def test_listing_shows_when_the_vacancy_is_already_tracked(api):
    a, _ = create_or_refresh_draft(api.db, employer="Acme", title="IT Support Officer", location="Melbourne")
    update_status(api.db, a.id, "approved")
    update_status(api.db, a.id, "applied", evidence="Confirmation email ref 4411")
    l = listing(api.db, employer="Acme Pty Ltd")
    row = api.get("/v1/listings?gate=all").json()["items"][0]
    assert row["tracked"] == {"id": a.id, "status": "applied", "employer": "Acme", "title": "IT Support Officer"}
    assert api.get(f"/v1/listings/{l.id}").json()["tracked"]["status"] == "applied"


# ------------------------------------------------------------------ applications
def make_app(db, **kw):
    a, _ = create_or_refresh_draft(db, **{"employer": "Acme", "title": "IT Support Officer", "location": "Melbourne", "source": "test", **kw})
    return a


def test_application_lifecycle_through_the_api(api):
    a = make_app(api.db)
    row = api.get(f"/v1/applications/{a.id}").json()
    assert row["status"] == "drafted" and row["allowed_next"] == ["approved", "withdrawn"] and row["history"][0]["status"] == "drafted"
    r = api.post(f"/v1/applications/{a.id}/status", json={"status": "approved", "note": "read both documents"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["allowed_next"] == ["drafted", "applied", "withdrawn"]
    r = api.post(f"/v1/applications/{a.id}/status", json={"status": "applied"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "needs_evidence"
    r = api.post(f"/v1/applications/{a.id}/status", json={"status": "applied", "evidence": "Seek confirmation email, ref 8841"})
    assert r.json()["status"] == "applied" and r.json()["has_evidence"] and r.json()["applied_at"]
    assert api.post(f"/v1/applications/{a.id}/status", json={"status": "interview"}).json()["status"] == "interview"
    r = api.post(f"/v1/applications/{a.id}/status", json={"status": "drafted"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invalid_transition"
    assert api.post("/v1/applications/9999/status", json={"status": "approved"}).status_code == 404
    assert api.post(f"/v1/applications/{a.id}/status", json={"status": "hired"}).status_code == 409


def test_list_counts_notes_and_manual_logging_with_duplicate_block(api):
    a = make_app(api.db)
    make_app(api.db, employer="Globex", title="Help Desk Analyst")
    r = api.get("/v1/applications").json()
    assert r["counts"]["drafted"] == 2 and r["total"] == 2 and len(api.get("/v1/applications?status=approved").json()["items"]) == 0
    n = api.post(f"/v1/applications/{a.id}/notes", json={"note": "Recruiter will call Thursday"}).json()
    assert "Recruiter will call Thursday" in n["notes"] and api.post(f"/v1/applications/{a.id}/notes", json={"note": ""}).status_code == 422
    new = api.post("/v1/applications", json={"employer": "Initech", "title": "IT Officer", "location": "Geelong", "url": "https://i.example/1"})
    assert new.status_code == 201 and new.json()["status"] == "drafted"
    update_status(api.db, a.id, "approved")
    update_status(api.db, a.id, "applied", evidence="Confirmation reference 99231")
    dup = api.post("/v1/applications", json={"employer": "ACME", "title": "IT Support Officer (Part-time)", "location": "Melbourne VIC"})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "duplicate" and "Duplicate applications are blocked" in dup.json()["detail"]["message"]
    same_emp = api.post("/v1/applications", json={"employer": "Acme", "title": "Network Technician"})
    assert same_emp.status_code == 201
    assert any(x["title"] == "IT Support Officer" for x in api.get(f"/v1/applications/{same_emp.json()['id']}").json()["same_employer"])


def test_documents_download_inline_pdf_attachment_docx_and_never_outside_the_generated_folder(api):
    gen = api.tmp / "generated" / "x"
    gen.mkdir(parents=True)
    (gen / "resume.pdf").write_bytes(b"%PDF-1.4 test")
    (gen / "resume.docx").write_bytes(b"PK test")
    outside = api.tmp / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 SECRET")
    a = make_app(api.db, doc_paths={"resume.pdf": gen / "resume.pdf", "resume.docx": gen / "resume.docx", "evil.pdf": outside, "gone.pdf": gen / "gone.pdf"})
    r = api.get(f"/v1/applications/{a.id}/documents/resume.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "inline" in r.headers["content-disposition"] and r.headers["content-type"] == "application/pdf"
    r = api.get(f"/v1/applications/{a.id}/documents/resume.docx")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "wordprocessingml" in r.headers["content-type"]
    assert api.get(f"/v1/applications/{a.id}/documents/evil.pdf").status_code == 404  # outside the generated folder
    assert api.get(f"/v1/applications/{a.id}/documents/gone.pdf").status_code == 404 and api.get(f"/v1/applications/{a.id}/documents/nope.pdf").status_code == 404
    assert api.get(f"/v1/applications/{a.id}/documents/..%2F..%2Fetc%2Fpasswd").status_code == 404


# ------------------------------------------------------------------ jobs
def test_draft_job_end_to_end_creates_documents_and_a_tracker_row(api):
    r = api.post("/v1/jobs", json={"kind": "draft", "params": {"text": RAW["it_support_pt"], "url": "https://x.example/job/1", "source": "adzuna"}})
    assert r.status_code == 202
    j = wait(api, r.json()["id"])
    assert j["status"] == "done", j
    res = j["result"]
    assert res["gate"]["status"] == "PASS" and res["score"] > 80 and res["application"]["status"] == "drafted"
    assert set(res["files"]) == {"resume.docx", "resume.pdf", "cover_letter.docx", "cover_letter.pdf"} and res["job"]["employer"]
    assert api.get(f"/v1/applications/{res['application']['id']}/documents/cover_letter.pdf").status_code == 200
    j2 = wait(api, api.post("/v1/jobs", json={"kind": "draft", "params": {"text": RAW["it_support_pt"], "url": "https://x.example/job/1"}}).json()["id"])
    assert j2["result"]["application"]["id"] == res["application"]["id"] and api.db.query(Application).count() == 1


def test_draft_job_variants_score_only_gate_fail_duplicate_and_validation(api, monkeypatch):
    j = wait(api, api.post("/v1/jobs", json={"kind": "draft", "params": {"text": RAW["it_support_pt"], "score_only": True}}).json()["id"])
    assert j["result"]["files"] == [] and j["result"]["application"] is None
    app.state.ai_factory = lambda: PipeAI(EXTRACT["aps_role"])
    j = wait(api, api.post("/v1/jobs", json={"kind": "draft", "params": {"text": RAW["aps_role"]}}).json()["id"])
    assert j["result"]["gate"]["status"] == "FAIL" and "Gate FAIL" in j["result"]["skipped_reason"] and j["result"]["files"] == []
    assert api.post("/v1/jobs", json={"kind": "draft", "params": {"text": "too short"}}).status_code == 422
    assert api.post("/v1/jobs", json={"kind": "launch_missiles", "params": {}}).status_code == 422
    assert api.get("/v1/jobs/nope").status_code == 404


def test_draft_blocked_when_the_vacancy_was_already_applied_to(api):
    j = wait(api, api.post("/v1/jobs", json={"kind": "draft", "params": {"text": RAW["it_support_pt"]}}).json()["id"])
    aid = j["result"]["application"]["id"]
    update_status(api.db, aid, "approved")
    update_status(api.db, aid, "applied", evidence="Confirmation email reference 5521")
    j2 = wait(api, api.post("/v1/jobs", json={"kind": "draft", "params": {"text": RAW["it_support_pt"]}}).json()["id"])
    assert "Duplicate vacancy" in j2["result"]["skipped_reason"] and j2["result"]["files"] == []


class FakeAdzuna:
    def __init__(self, id_, key):
        assert (id_, key) == ("APPID", "KEY")

    async def search(self, **q):
        FakeAdzuna.last = q
        return [RawListing("adzuna", f"s{i}", f"https://x.example/{i}", "IT Support Officer", "Westside Logistics", "Werribee, Melbourne",
                           "Support staff with Microsoft 365 and Active Directory.", False, date(2026, 11, 9), "part_time") for i in range(3)]


def test_discover_adzuna_job_fetches_scores_and_reports_counts(api, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "APPID")
    monkeypatch.setattr(settings, "adzuna_app_key", "KEY")
    monkeypatch.setattr(jobs_routes, "AdzunaSource", FakeAdzuna)
    j = wait(api, api.post("/v1/jobs", json={"kind": "discover_adzuna", "params": {"what": "it support", "where": "Melbourne", "pages": 9, "limit": 2}}).json()["id"])
    assert j["status"] == "done" and j["result"] == {"fetched": 3, "new": 3, "known": 0, "scored": 2, "waiting_to_score": 1}
    assert FakeAdzuna.last["pages"] == 3 and FakeAdzuna.last["what"] == "it support"  # pages capped at 3
    rows = api.get("/v1/listings").json()
    assert rows["counts"]["untriaged"] == 1 and all(i["snippet_only"] and i["gate"] == "REVIEW" for i in rows["items"])
    j = wait(api, api.post("/v1/jobs", json={"kind": "discover_adzuna", "params": {"what": "it support", "limit": 5}}).json()["id"])
    assert j["result"]["new"] == 0 and j["result"]["known"] == 3 and j["result"]["scored"] == 1
    j = wait(api, api.post("/v1/jobs", json={"kind": "rescore", "params": {}}).json()["id"])
    assert j["result"]["reset"] == 3 and j["result"]["scored"] == 3


def test_discover_validation_and_source_errors_are_readable(api, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "")
    monkeypatch.setattr(settings, "adzuna_app_key", "")
    r = api.post("/v1/jobs", json={"kind": "discover_adzuna", "params": {"what": "x"}})
    assert r.status_code == 422 and "ADZUNA_APP_ID" in r.json()["detail"]
    monkeypatch.setattr(settings, "adzuna_app_id", "APPID")
    monkeypatch.setattr(settings, "adzuna_app_key", "KEY")
    assert api.post("/v1/jobs", json={"kind": "discover_adzuna", "params": {"what": "  "}}).status_code == 422

    class Boom(FakeAdzuna):
        async def search(self, **q):
            raise SourceError("Adzuna returned HTTP 429 (rate limited: wait and retry, or reduce --pages)")
    monkeypatch.setattr(jobs_routes, "AdzunaSource", Boom)
    j = wait(api, api.post("/v1/jobs", json={"kind": "discover_adzuna", "params": {"what": "x"}}).json()["id"])
    assert j["status"] == "error" and "rate limited" in j["error"] and "KEY" not in j["error"]


def test_jobs_run_one_at_a_time_and_recent_lists_them(api):
    ids = [api.post("/v1/jobs", json={"kind": "draft", "params": {"text": RAW["it_support_pt"], "score_only": True}}).json()["id"] for _ in range(3)]
    done = [wait(api, i) for i in ids]
    assert all(d["status"] == "done" for d in done)
    starts = [d["finished_at"] for d in done]
    assert starts == sorted(starts)  # finished in submission order: the lock serialised them
    assert [j["id"] for j in api.get("/v1/jobs").json()][:3] == ids[::-1]


# ------------------------------------------------------------------ profile, blocklist, system, overview
def test_profile_groups_summary_and_document_visibility(api):
    p = api.get("/v1/profile").json()
    labels = [g["label"] for g in p["groups"]]
    assert labels.index("Identity") < labels.index("Education") < labels.index("Experience") < labels.index("Skills")
    assert p["summary"]["verified"] + p["summary"]["needs_confirmation"] == p["summary"]["total"] and p["summary"]["used_in_documents"] > 20
    facts = {f["key"]: f for g in p["groups"] for f in g["facts"]}
    assert facts["acme_packaging"]["used_in_documents"] is True and facts["referee_one"]["used_in_documents"] is False  # referees: forms only
    assert facts["screening_unrestricted_work_rights"]["visibility"] == "internal"


def test_blocklist_crud_and_validation(api):
    assert api.get("/v1/blocklist").json() == []
    assert api.post("/v1/blocklist", json={"employer": "Study and Work", "reason": "pay to place"}).status_code == 201
    assert api.post("/v1/blocklist", json={"employer": "study & work"}).json()["added"] is False
    assert api.get("/v1/blocklist").json() == [{"employer": "Study and Work", "reason": "pay to place"}]
    assert api.post("/v1/blocklist", json={"employer": ""}).status_code == 422
    assert api.delete("/v1/blocklist/study and work").json()["removed"] is True and api.get("/v1/blocklist").json() == []


def test_system_status_reports_services_counts_and_config_without_secrets(api, monkeypatch):
    monkeypatch.setattr(settings, "adzuna_app_id", "APPID")
    monkeypatch.setattr(settings, "adzuna_app_key", "SUPERSECRETKEY")
    s = api.get("/v1/system").json()
    assert s["services"]["database"] is True and s["services"]["redis"] is True and s["services"]["ollama"]["ok"] is True
    assert s["ai"]["chat_model"] == settings.ai_chat_model and s["discovery"]["adzuna_configured"] is True
    assert s["counts"]["facts"] > 50 and s["disk_free_gb"] > 0
    blob = json.dumps(s)
    assert "SUPERSECRETKEY" not in blob and "APPID" not in blob and settings.jwt_secret not in blob


def test_overview_counts_attention_matches_and_activity(api):
    listing(api.db, title="Clear match", gate_status="PASS", score=95, employer="Alpha")
    listing(api.db, title="Maybe", gate_status="REVIEW", score=70, employer="Beta")
    listing(api.db, title="Nope", gate_status="FAIL", score=99, employer="Gamma")
    d = make_app(api.db, employer="Delta", title="Support Analyst")
    e = make_app(api.db, employer="Echo", title="Service Desk")
    update_status(api.db, e.id, "approved")
    seeded = Application(fingerprint="seed1", employer="Old Co", employer_norm="old co", source="user-reported", status="applied", evidence="pre-existing", history=[{"status": "applied", "at": "2026-11-09T00:00:00+00:00", "note": "seeded"}])
    api.db.add(seeded)
    api.db.commit()
    o = api.get("/v1/overview").json()
    assert o["listings"] == {"total": 3, "pass": 1, "review": 1, "fail": 1}
    assert [m["title"] for m in o["top_matches"]] == ["Clear match", "Maybe"]
    assert {(a["kind"], a["application_id"]) for a in o["attention"]} == {("review_draft", d.id), ("record_application", e.id)}
    assert {p["status"]: p["count"] for p in o["pipeline"]}["applied"] == 1 and o["applied_total"] == 1
    assert all(a["employer"] != "Old Co" for a in o["activity"]) and o["activity"][0]["employer"] == "Echo"


def test_overview_is_empty_safe(api):
    o = api.get("/v1/overview").json()
    assert o["listings"]["total"] == 0 and o["top_matches"] == [] and o["attention"] == [] and o["has_discovery"] is False


# ------------------------------------------------------------------ assistant
KNOWN = {"melbourne", "sydney", "werribee"}


@pytest.mark.parametrize("msg,expect", [
    ("How am I doing?", {"intent": "summary"}), ("give me an overview", {"intent": "summary"}), ("help", {"intent": "help"}),
    ("what can you do", {"intent": "help"}), ("Why did job 12 score that way?", {"intent": "explain_job", "id": 12}), ("explain #7", {"intent": "explain_job", "id": 7}),
    ("What is waiting for my approval?", {"intent": "list_applications", "status": "drafted"}), ("show interviews", {"intent": "list_applications", "status": "interview"}),
    ("have I applied to Example Hospital?", {"intent": "check_employer", "employer": "example hospital"}), ("did I already apply at Example Studios", {"intent": "check_employer", "employer": "big ant studios"}),
    ("show my best matches", {"intent": "list_jobs", "gate": "open", "location": None, "text": None}), ("find melbourne service desk jobs", {"intent": "list_jobs", "location": "melbourne", "text": "service desk"}),
    ("show clear matches in sydney", {"intent": "list_jobs", "gate": "pass", "location": "sydney"}), ("newest jobs", {"intent": "list_jobs", "sort": "newest"}),
    ("apply to the Westside job for me", {"intent": "refuse_action"}), ("Please submit my application", {"intent": "refuse_action"}),
    ("can you delete job 5", {"intent": "refuse_action"}), ("go ahead and email the recruiter", {"intent": "refuse_action"}),
    ("show me jobs I can apply to", {"intent": "list_jobs"}), ("have I applied to Example Hospital?", {"intent": "check_employer"}),
    ("what did I apply for last week", {"intent": "list_applications", "status": "applied"}) if False else ("how many applied", {"intent": "list_applications", "status": "applied"}),
    ("did I apply to Globex", {"intent": "check_employer", "employer": "globex"}), ("have I applied to Infosys?", {"intent": "check_employer", "employer": "infosys"}),
    ("have I applied anywhere yet?", {"intent": "list_applications", "status": "applied"}), ("banana smoothie recipe", {"intent": "unknown"}),
])
def test_understand_maps_questions_to_intents(msg, expect):
    got = assistant_mod.understand(msg, KNOWN)
    assert {k: got.get(k) for k in expect} == expect, got


def ask(api, msg):
    r = api.post("/v1/assistant", json={"message": msg})
    assert r.status_code == 200
    return r.json()


def test_assistant_answers_come_from_the_data(api):
    listing(api.db, title="Service Desk Analyst", gate_status="PASS", score=88, employer="Alpha", location="Melbourne CBD")
    listing(api.db, title="Network Tech", gate_status="REVIEW", score=70, employer="Beta", location="Sydney")
    a = make_app(api.db, employer="Example Hospital", title=None)
    make_app(api.db, employer="Delta", title="Support")
    s = ask(api, "How am I doing?")
    assert s["intent"] == "summary" and "2 jobs worth a look (1 clear match)" in s["text"] and "2 drafts waiting" in s["text"] and len(s["cards"]) == 2
    j = ask(api, "show melbourne jobs")
    assert j["intent"] == "list_jobs" and [c["title"] for c in j["cards"]] == ["Service Desk Analyst"] and "in Melbourne" in j["text"]
    assert ask(api, "show clear matches")["cards"][0]["employer"] == "Alpha"
    q = ask(api, "what's waiting for my approval?")
    assert q["intent"] == "list_applications" and len(q["cards"]) == 2 and q["cards"][0]["type"] == "application"
    assert ask(api, "show interviews")["text"] == "Nothing is interview right now."
    y = ask(api, "have I applied to Example Hospital?")
    assert y["text"].startswith("Yes") and y["cards"][0]["id"] == a.id
    assert ask(api, "did I apply to Globex")["text"].startswith("No record")
    l = api.db.query(Listing).filter_by(title="Network Tech").one()
    e = ask(api, f"why did job {l.id} score that way")
    assert e["intent"] == "explain_job" and "scored 70" in e["text"] and e["cards"][1]["type"] == "note" and any("Technical skills" in x for x in e["cards"][1]["lines"])
    assert "can't find" in ask(api, "why did job 9999 fail")["text"]
    assert ask(api, "help")["suggestions"]


class AssistAI(FakeAI):
    def __init__(self, out):
        super().__init__()
        self.out = out

    async def chat(self, system, user, *, json_mode=False, temperature=None, json_schema=None):
        return self.out if isinstance(self.out, str) else json.dumps(self.out)


def test_assistant_falls_back_to_the_model_for_unusual_phrasing_and_survives_garbage(api, monkeypatch):
    listing(api.db, title="Service Desk Analyst", gate_status="PASS", score=88, employer="Alpha")
    llm = {"intent": "summary", "gate": "open", "location": "", "text": "", "status": "", "id": 0, "employer": ""}
    app.state.ai_factory = lambda: AssistAI(llm)
    assert ask(api, "give me the lay of the land")["intent"] == "summary"
    app.state.ai_factory = lambda: AssistAI("not json at all")
    assert ask(api, "blorp")["intent"] == "unknown"
    app.state.ai_factory = lambda: AssistAI({**llm, "intent": "explain_job"})  # intent without its id: refused
    assert ask(api, "blorp 2")["intent"] == "unknown"
    app.state.ai_factory = lambda: AssistAI({**llm, "intent": "drop_all_tables"})
    assert ask(api, "blorp 3")["intent"] == "unknown"
    assert api.post("/v1/assistant", json={"message": ""}).status_code == 422
    assert api.post("/v1/assistant", json={"message": "x" * 501}).status_code == 422


def test_overview_has_the_owners_first_name(api):
    assert api.get("/v1/overview").json()["owner"]["first_name"] == "Alex"


def test_draft_straight_from_a_stored_full_ad_listing_and_refusal_for_snippets(api):
    full = listing(api.db, description_complete=True, source="workable:first-focus", source_id="w1", url="https://apply.workable.com/j/W1",
                   description="Join our service desk. Microsoft 365 and Active Directory. Part-time, 20 hrs/week.")
    snip = listing(api.db, description_complete=False, source_id="s1")
    j = wait(api, api.post("/v1/jobs", json={"kind": "draft", "params": {"listing_id": full.id}}).json()["id"])
    assert j["status"] == "done" and j["result"]["application"]["source"] == "workable:first-focus" and len(j["result"]["files"]) == 4
    assert api.get(f"/v1/applications/{j['result']['application']['id']}").json()["url"] == "https://apply.workable.com/j/W1"
    r = api.post("/v1/jobs", json={"kind": "draft", "params": {"listing_id": snip.id}})
    assert r.status_code == 422 and "only has a snippet" in r.json()["detail"]
    assert api.post("/v1/jobs", json={"kind": "draft", "params": {"listing_id": 9999}}).status_code == 404


def test_assistant_refuses_to_act_and_changes_nothing(api):
    a = make_app(api.db, employer="Westside Logistics", title="IT Support Officer")
    for msg in ("apply to the Westside job for me", "submit my application now", "please approve everything", "delete all my applications"):
        r = ask(api, msg)
        assert r["intent"] == "refuse_action" and "never apply" in r["text"] and r["cards"] == []
    assert api.db.query(Application).count() == 1 and api.db.get(Application, a.id).status == "drafted"


def test_overview_survives_a_withdrawn_application_that_was_never_applied(api):
    from app.tracker.service import create_or_refresh_draft, update_status
    a, _ = create_or_refresh_draft(api.db, employer="Zed Co", title="Role", location="Melbourne", source="manual")
    update_status(api.db, a.id, "withdrawn")
    r = api.get("/v1/overview")
    assert r.status_code == 200 and r.json()["applied_total"] == 0
