import json
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.apply import gmail
from app.apply.emails import find_application_emails, valid_address
from app.apply.gmail import Credentials, GmailImapMailer, MailError, build_message
from app.apply.platforms import NotAllowed, classify_url, find_ats_links
from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import Application
from app.db.seed import seed_facts
from app.main import app
from app.tracker.service import create_or_refresh_draft, update_status
from tests.conftest import PipeAI
from tests.fake_imap import FakeGmail
from tests.fixtures.jobs import EXTRACT, RAW

SEED = Path(__file__).resolve().parents[2] / "data" / "profile_seed.json"
PW = "correct-horse-battery"


# ───────────────────────── finding the address in the ad ─────────────────────────
def test_application_email_is_found_and_ranked_by_how_the_ad_talks_about_it():
    ad = ("Questions? Reach our office at info@acme.com.au or visit us. To apply, send your resume and cover letter to careers@acme.com.au. "
          "Do not reply to noreply@acme.com.au. Our privacy officer: privacy@acme.com.au. Example only: jane@example.com. Mine: me@gmail.com")
    got = find_application_emails(ad, exclude=("ME@gmail.com",))
    assert [e["address"] for e in got] == ["careers@acme.com.au", "info@acme.com.au"]  # apply-cue first; noreply/privacy/example/own address dropped
    assert got[0]["near_apply_cue"] is True and got[1]["near_apply_cue"] is False
    assert find_application_emails("No contact details here.") == []
    assert [e["address"] for e in find_application_emails("Email jobs@x.io.")] == ["jobs@x.io"]  # trailing full stop is not part of it


@pytest.mark.parametrize("addr,ok", [("a@b.co", True), ("first.last+tag@sub.example.com.au", True), ("a@b", False), ("a b@c.com", False),
                                     ("a@b.com\nBcc: evil@x.com", False), ("a@b.com, c@d.com", False), ("<a@b.com>", False), ("", False), ("x" * 250 + "@a.com", False)])
def test_address_validation_blocks_header_injection(addr, ok):
    assert valid_address(addr) is ok


# ───────────────────────── which sites assisted fill may touch ─────────────────────────
@pytest.mark.parametrize("url,platform", [
    ("https://apply.workable.com/first-focus/j/ABC123/apply/", "workable"), ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse"),
    ("https://job-boards.greenhouse.io/acme/jobs/9", "greenhouse"), ("https://jobs.lever.co/acme/uuid/apply", "lever"),
    ("https://jobs.smartrecruiters.com/Acme/123-role", "smartrecruiters"), ("https://careers.smartrecruiters.com/Acme", "smartrecruiters")])
def test_only_the_four_ats_platforms_are_allowed(url, platform):
    assert classify_url(url) == platform


@pytest.mark.parametrize("url,why", [
    ("https://www.seek.com.au/job/123", "Seek"), ("https://au.linkedin.com/jobs/view/1", "Linkedin"), ("https://au.indeed.com/viewjob?jk=1", "Indeed"),
    ("https://www.adzuna.com.au/land/ad/1", "Adzuna"), ("https://acme.com/careers/apply", "Workable, Greenhouse"), ("http://apply.workable.com/x", "https"),
    ("https://apply.workable.com.evil.example/x", "Workable, Greenhouse"), ("https://evilapply.workable.com/x", "Workable, Greenhouse"),
    ("javascript:alert(1)", "valid web address"), ("ftp://jobs.lever.co/x", "valid web address"), ("", "valid web address"), ("https://127.0.0.1/x", "Workable, Greenhouse")])
def test_everything_else_is_refused_with_a_readable_reason(url, why):
    with pytest.raises(NotAllowed) as e:
        classify_url(url)
    assert why in str(e.value)


def test_find_ats_links_keeps_only_allowed_pages():
    ad = "Apply at https://jobs.lever.co/acme/123/apply. or https://www.seek.com.au/job/9 or https://acme.com/x or https://apply.workable.com/acme/j/1/."
    assert find_ats_links(ad) == ["https://jobs.lever.co/acme/123/apply", "https://apply.workable.com/acme/j/1/"]


# ───────────────────────── building and saving a Gmail draft ─────────────────────────
def test_message_has_the_right_headers_and_attachments(tmp_path):
    (tmp_path / "r.pdf").write_bytes(b"%PDF-1.4 resume")
    (tmp_path / "c.pdf").write_bytes(b"%PDF-1.4 letter")
    m = build_message(sender="me@gmail.com", to="careers@acme.com", subject="Application – IT Support – Alex Example", body="Hello",
                      attachments=[("Alex Example - Resume.pdf", tmp_path / "r.pdf"), ("Alex Example - Cover Letter.pdf", tmp_path / "c.pdf")])
    assert m["To"] == "careers@acme.com" and m["From"] == "me@gmail.com" and "Application" in str(m["Subject"])
    parts = list(m.iter_attachments())
    assert [p.get_filename() for p in parts] == ["Alex Example - Resume.pdf", "Alex Example - Cover Letter.pdf"] and all(p.get_content_type() == "application/pdf" for p in parts)
    assert parts[0].get_payload(decode=True) == b"%PDF-1.4 resume"


def test_credentials_never_reveal_the_password():
    c = Credentials("me@gmail.com", "super-secret-app-pw")
    assert "super-secret" not in repr(c) and "super-secret" not in str(c) and "<hidden>" in repr(c)


def test_credentials_come_from_the_environment_then_the_keychain(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "env@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "envpw")
    assert gmail.credentials().address == "env@gmail.com" and gmail.credentials(with_password=False).password == ""
    monkeypatch.delenv("GMAIL_ADDRESS")
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = '    "acct"<blob>="kc@gmail.com"\n' if "-w" not in cmd else "kc-secret\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(gmail.subprocess, "run", fake_run)
    assert gmail.credentials(with_password=False).address == "kc@gmail.com" and len(calls) == 1 and "-w" not in calls[0]  # no password read for a status check
    c = gmail.credentials()
    assert (c.address, c.password) == ("kc@gmail.com", "kc-secret")
    monkeypatch.setattr(gmail.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 44, "", ""))
    assert gmail.credentials() is None


@pytest.fixture
def imap(monkeypatch):
    srv = FakeGmail()
    monkeypatch.setattr(settings, "gmail_imap_host", "127.0.0.1")
    monkeypatch.setattr(settings, "gmail_imap_port", srv.port)
    monkeypatch.setattr(settings, "gmail_imap_ssl", False)
    yield srv
    srv.close()


def _msg(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 x" * 50)
    return build_message(sender="me@gmail.com", to="careers@acme.com", subject="Application", body="Body text", attachments=[("A.pdf", tmp_path / "a.pdf")])


def test_a_draft_is_appended_to_the_drafts_folder_and_nothing_else_happens(imap, tmp_path):
    folder = GmailImapMailer(Credentials("me@gmail.com", "app-password-1234")).create_draft(_msg(tmp_path))
    assert folder == "[Gmail]/Drafts"
    assert len(imap.drafts) == 1
    where, flags, stored = imap.drafts[0]
    assert where == "[Gmail]/Drafts" and "\\Draft" in flags
    assert stored["To"] == "careers@acme.com" and stored.get_body(("plain",)).get_content().strip() == "Body text"
    assert [p.get_filename() for p in stored.iter_attachments()] == ["A.pdf"]
    assert set(imap.commands) <= {"LOGIN", "LIST", "APPEND", "LOGOUT", "CAPABILITY"}, imap.commands  # login, find Drafts, save, leave. That is all.


def test_wrong_app_password_is_a_clear_error_and_leaks_nothing(imap, tmp_path):
    with pytest.raises(MailError) as e:
        GmailImapMailer(Credentials("me@gmail.com", "WRONG-pw-9999")).create_draft(_msg(tmp_path))
    assert e.value.code == "gmail_auth_failed" and "app password" in str(e.value) and "WRONG-pw-9999" not in str(e.value) and imap.drafts == []


def test_unreachable_gmail_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "gmail_imap_host", "127.0.0.1")
    monkeypatch.setattr(settings, "gmail_imap_port", 1)  # nothing listens here
    monkeypatch.setattr(settings, "gmail_imap_ssl", False)
    with pytest.raises(MailError) as e:
        GmailImapMailer(Credentials("me@gmail.com", "x")).create_draft(_msg(tmp_path))
    assert e.value.code == "gmail_unreachable"


def test_there_is_no_code_in_this_app_that_can_send_email():
    """The app password could technically also send, so the guarantee is that no code path does."""
    root = Path(__file__).resolve().parents[1] / "app"
    bad = [str(p.relative_to(root)) for p in root.rglob("*.py") if re.search(r"smtplib|\bSMTP\b|sendmail|send_message|\.send\(", p.read_text())
           ]
    assert bad == [], f"possible sending code in: {bad}"


# ───────────────────────── the API ─────────────────────────
class FakeMailer:
    account = "me@gmail.com"

    def __init__(self, fail: MailError | None = None):
        self.drafts, self.fail = [], fail

    def create_draft(self, message):
        if self.fail:
            raise self.fail
        self.drafts.append(message)
        return "[Gmail]/Drafts"


@pytest.fixture(scope="module")
def engine():
    if not SEED.exists():
        pytest.skip("data/profile_seed.json not present")
    e = create_engine(settings.database_url.rsplit("/", 1)[0] + "/jobhunter_test")
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    yield e
    Base.metadata.drop_all(e)


@pytest.fixture
def api(engine, monkeypatch, tmp_path):
    with Session(engine) as s:
        s.execute(text("truncate profile_facts, applications, listings restart identity cascade"))
        s.commit()
        seed_facts(s, json.loads(SEED.read_text()))
    monkeypatch.setattr(settings, "jwt_secret", "x" * 48)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", hash_password(PW))
    monkeypatch.setattr(settings, "generated_dir", str(tmp_path / "generated"))
    saved = {k: getattr(app.state, k) for k in ("session_factory", "ai_factory", "mailer_factory", "mail_account", "assist_spawn")}
    mailer = FakeMailer()
    app.state.session_factory = lambda: Session(engine)
    app.state.ai_factory = lambda: PipeAI(EXTRACT["it_support_pt"])
    app.state.mailer_factory = lambda: mailer
    app.state.mail_account = lambda: mailer.account
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + c.post("/auth/login", json={"username": "admin", "password": PW}).json()["access_token"]
        c.mailer, c.tmp, c.engine = mailer, tmp_path, engine
        yield c
    for k, v in saved.items():
        setattr(app.state, k, v)


def make_app(api, *, docs=True, meta=None, **kw):
    gen = api.tmp / "generated" / "x"
    gen.mkdir(parents=True, exist_ok=True)
    paths = {}
    if docs:
        for n in ("resume.pdf", "cover_letter.pdf"):
            (gen / n).write_bytes(b"%PDF-1.4 " + n.encode())
            paths[n] = gen / n
    meta = meta if meta is not None else {"cover_letter": {"greeting": "Dear Hiring Manager,", "paragraphs": ["I am writing to apply for the IT Support Officer position at Acme."], "signoff": "Kind regards,", "subject": "Application"},
                                          "apply_emails": [{"address": "careers@acme.com", "near_apply_cue": True}, {"address": "info@acme.com", "near_apply_cue": False}], "apply_links": []}
    with Session(api.engine) as s:
        a, _ = create_or_refresh_draft(s, **{"employer": "Acme", "title": "IT Support Officer (Part-time, 20 hrs/week)", "location": "Melbourne", "source": "test",
                                             "doc_paths": paths, "draft_meta": meta, **kw})
        return a.id


def test_email_info_describes_the_draft_before_anything_is_created(api):
    aid = make_app(api)
    i = api.get(f"/v1/applications/{aid}/email").json()
    assert i["configured"] is True and i["account"] == "me@gmail.com" and i["can_draft"] is True and i["draft"] is None
    assert [c["address"] for c in i["candidates"]] == ["careers@acme.com", "info@acme.com"]
    assert i["subject"] == "Application – IT Support Officer – Alex Example"
    assert i["attachments"] == ["Alex Example - Resume.pdf", "Alex Example - Cover Letter.pdf"]
    assert i["body"].startswith("Dear Hiring Manager,\n\nI am writing to apply for the IT Support Officer position at Acme.") and "Kind regards,\nAlex Example" in i["body"]
    assert "attached" in i["body"] and "–" not in i["body"] and i["compose_url"].startswith("https://mail.google.com/mail/?view=cm&fs=1&to=careers%40acme.com")
    assert api.mailer.drafts == []  # asking never creates anything


def test_creating_the_draft_saves_it_moves_to_approved_and_sends_nothing(api):
    aid = make_app(api)
    r = api.post(f"/v1/applications/{aid}/email-draft", json={"to": "careers@acme.com"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["application"]["status"] == "approved" and out["draft"]["to"] == "careers@acme.com" and out["draft"]["folder"] == "[Gmail]/Drafts"
    assert len(api.mailer.drafts) == 1
    msg = api.mailer.drafts[0]
    assert msg["To"] == "careers@acme.com" and msg["From"] == "me@gmail.com" and "Application" in str(msg["Subject"])
    assert [p.get_filename() for p in msg.iter_attachments()] == ["Alex Example - Resume.pdf", "Alex Example - Cover Letter.pdf"]
    assert "Bcc" not in msg and "Cc" not in msg
    d = api.get(f"/v1/applications/{aid}").json()
    assert d["status"] == "approved" and "Gmail draft created for careers@acme.com (not sent)" in d["history"][-1]["note"]
    assert api.get(f"/v1/applications/{aid}/email").json()["draft"]["to"] == "careers@acme.com"
    # the person sends it themselves, then records it with proof
    r = api.post(f"/v1/applications/{aid}/status", json={"status": "applied", "evidence": "Sent from Gmail to careers@acme.com, subject Application"})
    assert r.json()["status"] == "applied"


def test_a_second_draft_for_an_approved_application_is_allowed_and_keeps_status(api):
    aid = make_app(api)
    api.post(f"/v1/applications/{aid}/email-draft", json={"to": "careers@acme.com"})
    assert api.post(f"/v1/applications/{aid}/email-draft", json={"to": "info@acme.com"}).status_code == 200
    assert len(api.mailer.drafts) == 2 and api.get(f"/v1/applications/{aid}").json()["status"] == "approved"


@pytest.mark.parametrize("to", ["not-an-email", "a@b.com\nBcc: evil@x.com", "a@b.com, c@d.com", "", "<a@b.com>"])
def test_bad_or_injected_addresses_are_refused_and_nothing_is_created(api, to):
    aid = make_app(api)
    r = api.post(f"/v1/applications/{aid}/email-draft", json={"to": to})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_address"
    assert api.mailer.drafts == [] and api.get(f"/v1/applications/{aid}").json()["status"] == "drafted"


def test_refusals_for_missing_documents_wrong_status_and_unset_gmail(api):
    no_docs = make_app(api, docs=False, employer="NoDocs")
    r = api.post(f"/v1/applications/{no_docs}/email-draft", json={"to": "a@b.com"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_documents"
    assert api.get(f"/v1/applications/{no_docs}/email").json()["can_draft"] is False
    done = make_app(api, employer="Done Co")
    with Session(api.engine) as s:
        update_status(s, done, "approved")
        update_status(s, done, "applied", evidence="Confirmation email reference 5511")
    r = api.post(f"/v1/applications/{done}/email-draft", json={"to": "a@b.com"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "wrong_status"
    ok = make_app(api, employer="Setup Co")
    app.state.mailer_factory = lambda: None
    app.state.mail_account = lambda: None
    r = api.post(f"/v1/applications/{ok}/email-draft", json={"to": "a@b.com"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "gmail_not_configured" and "set_gmail.sh" in r.json()["detail"]["message"]
    assert api.get(f"/v1/applications/{ok}/email").json()["configured"] is False
    assert api.post("/v1/applications/9999/email-draft", json={"to": "a@b.com"}).status_code == 404


def test_gmail_failures_are_reported_and_leave_the_status_alone(api):
    aid = make_app(api)
    api.mailer.fail = MailError("Gmail refused the sign-in.", "gmail_auth_failed")
    r = api.post(f"/v1/applications/{aid}/email-draft", json={"to": "careers@acme.com"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "gmail_auth_failed"
    assert api.get(f"/v1/applications/{aid}").json()["status"] == "drafted" and api.get(f"/v1/applications/{aid}/email").json()["draft"] is None


async def test_drafting_a_job_records_the_apply_addresses_links_and_cover_letter(engine, tmp_path):
    from app.pipeline import draft
    ad = RAW["it_support_pt"] + "\nTo apply, send your resume and cover letter to careers@westside.com.au. Or apply at https://jobs.lever.co/westside/123/apply. Not this one: https://www.seek.com.au/job/1"
    with Session(engine) as s:
        s.execute(text("truncate profile_facts, applications, listings restart identity cascade"))
        s.commit()
        seed_facts(s, json.loads(SEED.read_text()))
        r = await draft(ad, s, PipeAI(EXTRACT["it_support_pt"]), date(2026, 11, 9), tmp_path, url="https://apply.workable.com/westside/j/ABC/")
        meta = r.application.draft_meta
    assert [e["address"] for e in meta["apply_emails"]] == ["careers@westside.com.au"] and meta["apply_emails"][0]["near_apply_cue"]
    assert meta["apply_links"] == ["https://apply.workable.com/westside/j/ABC/", "https://jobs.lever.co/westside/123/apply"]
    assert meta["cover_letter"]["greeting"] == "Dear Hiring Manager," and meta["cover_letter"]["paragraphs"][0].startswith("I am writing to apply")


# ───────────────────────── assisted form fill: the API ─────────────────────────
import os
import signal
import time

from app.db.models import ProfileFact

WORKABLE_URL = "https://apply.workable.com/acme/j/ABC123/apply/"


class FakeRunner:
    """Stands in for the browser process: records what it was given and writes a status file like the real runner would."""

    def __init__(self, state="ready", pid=None, screenshot=None):
        self.calls, self.state, self.pid, self.screenshot = [], state, pid, screenshot

    def __call__(self, plan_path, status_path):
        plan = json.loads(Path(plan_path).read_text())
        self.calls.append(plan)
        pid = self.pid or os.getpid()
        if self.screenshot:
            (Path(status_path).parent / self.screenshot).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        Path(status_path).write_text(json.dumps({"state": self.state, "message": "Filled", "platform": plan["platform"], "filled": [{"label": "First name", "kind": "text", "source": "identity/name"}],
                                                 "left_blank": [{"label": "LinkedIn", "required": True, "type": "text"}], "captcha": False, "login_required": False,
                                                 "screenshot": self.screenshot, "submit_clicked": False, "stopped_before_submit": True}))
        return pid


def with_runner(api, **kw):
    r = FakeRunner(**kw)
    app.state.assist_spawn = r
    return r


def test_assist_info_suggests_only_an_allowed_page(api):
    ok = make_app(api, source_url=None) if False else make_app(api, meta={"apply_links": ["https://www.seek.com.au/job/1", WORKABLE_URL], "apply_emails": []})
    i = api.get(f"/v1/applications/{ok}/assist").json()
    assert i["suggested_url"] == WORKABLE_URL and i["can_start"] is True and i["running"] is False and i["status"] is None
    assert set(i["platforms"]) == {"Workable", "Greenhouse", "Lever", "Smartrecruiters"}
    emp = make_app(api, employer="EmployerSite", meta={"apply_links": ["https://acme.com/careers/apply"], "apply_emails": []})
    assert api.get(f"/v1/applications/{emp}/assist").json()["suggested_url"] == "https://acme.com/careers/apply"  # an employer's own site is allowed since the 2026-11-11 policy change
    none = make_app(api, employer="NoLink", meta={"apply_links": ["https://www.seek.com.au/job/1", "https://127.0.0.1/x"], "apply_emails": []})
    assert api.get(f"/v1/applications/{none}/assist").json()["suggested_url"] is None  # job boards and private addresses never


@pytest.mark.parametrize("url", ["https://www.seek.com.au/job/9", "https://au.linkedin.com/jobs/view/1", "https://au.indeed.com/viewjob?jk=1",
                                 "https://www.adzuna.com.au/land/ad/1", "http://apply.workable.com/x", "https://192.168.1.5/apply", "javascript:alert(1)"])
def test_assist_refuses_other_sites_and_starts_nothing(api, url):
    aid = make_app(api)
    runner = with_runner(api)
    r = api.post(f"/v1/applications/{aid}/assist", json={"url": url})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "site_not_allowed" and runner.calls == []
    assert api.get(f"/v1/applications/{aid}").json()["status"] == "drafted"


def test_assist_start_builds_the_plan_from_verified_facts_only_and_approves(api):
    aid = make_app(api, meta={"apply_links": [], "apply_emails": [], "cover_letter": {"greeting": "Dear Hiring Manager,", "paragraphs": ["I am writing to apply."], "signoff": "Kind regards,"}})
    runner = with_runner(api)
    r = api.post(f"/v1/applications/{aid}/assist", json={"url": WORKABLE_URL})
    assert r.status_code == 200, r.text
    assert r.json()["status"]["state"] == "ready" and r.json()["status"]["stopped_before_submit"] is True and r.json()["status"]["submit_clicked"] is False
    plan = runner.calls[0]
    with Session(api.engine) as s:
        get = lambda c, k: s.query(ProfileFact).filter_by(category=c, key=k, status="verified").one().value
        name, phone = get("identity", "name"), get("contact", "phone")
        email = get("contact", "email")["email"]
        unconfirmed = {f.key for f in s.query(ProfileFact).filter_by(status="needs_confirmation")}
    assert plan["person"]["full_name"] == name and plan["person"]["first_name"] == name.split()[0] and plan["person"]["phone"] == phone and plan["person"]["email"] == email
    assert plan["person"]["city"] == "Exampleton" and plan["person"]["state"] == "VIC" and plan["platform"] == "workable" and plan["url"] == WORKABLE_URL
    assert {x["id"]: (x["answer"], x["source"]) for x in plan["answers"]} == {
        "unrestricted_work_rights": ("No", "eligibility/screening_unrestricted_work_rights"), "citizen_or_pr": ("No", "work_rights/current"),
        "drivers_licence": ("Yes", "license/vic_drivers_licence"), "studying": ("Yes", "education/uni_bit")}  # the only answers the filler is ever given
    assert set(plan["files"]) == {"resume", "cover_letter"} and plan["files"]["resume"].endswith("resume.pdf")
    assert plan["cover_letter_text"].startswith("Dear Hiring Manager,") and "I am writing to apply." in plan["cover_letter_text"]
    assert not (unconfirmed & set(json.dumps(plan)))  # nothing unconfirmed leaks into the plan
    plan_file = Path(plan["files"]["resume"]).parent / "assist_plan.json"
    assert oct(plan_file.stat().st_mode & 0o777) == "0o600"  # it holds your contact details
    d = api.get(f"/v1/applications/{aid}").json()
    assert d["status"] == "approved" and "Assisted form fill started on workable (stops before Submit)" in d["history"][-1]["note"]


def test_assist_refuses_when_already_open_wrong_status_or_no_documents(api):
    aid = make_app(api)
    with_runner(api)
    assert api.post(f"/v1/applications/{aid}/assist", json={"url": WORKABLE_URL}).status_code == 200
    again = api.post(f"/v1/applications/{aid}/assist", json={"url": WORKABLE_URL})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_running"
    assert api.get(f"/v1/applications/{aid}/assist").json()["can_start"] is False
    nd = make_app(api, docs=False, employer="NoDocs")
    r = api.post(f"/v1/applications/{nd}/assist", json={"url": WORKABLE_URL})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_documents"
    done = make_app(api, employer="Done Co")
    with Session(api.engine) as s:
        update_status(s, done, "approved")
        update_status(s, done, "applied", evidence="Confirmation reference 777111")
    r = api.post(f"/v1/applications/{done}/assist", json={"url": WORKABLE_URL})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "wrong_status"
    assert api.post(f"/v1/applications/{make_app(api, employer='NoUrl', meta={'apply_links': [], 'apply_emails': []})}/assist", json={}).json()["detail"]["code"] == "no_url"


def test_a_window_that_was_closed_is_reported_as_closed_and_can_be_reopened(api):
    aid = make_app(api)
    dead = subprocess.Popen(["true"])
    dead.wait()
    with_runner(api, pid=dead.pid)
    api.post(f"/v1/applications/{aid}/assist", json={"url": WORKABLE_URL})
    st = api.get(f"/v1/applications/{aid}/assist").json()
    assert st["running"] is False and st["status"]["state"] == "closed" and st["can_start"] is True


def test_stop_ends_the_browser_process(api):
    aid = make_app(api)
    child = subprocess.Popen(["sleep", "30"])
    try:
        with_runner(api, pid=child.pid)
        api.post(f"/v1/applications/{aid}/assist", json={"url": WORKABLE_URL})
        assert api.get(f"/v1/applications/{aid}/assist").json()["running"] is True
        out = api.post(f"/v1/applications/{aid}/assist/stop").json()
        assert out["status"]["state"] == "closed"
        assert child.wait(timeout=5) == -signal.SIGTERM
    finally:
        child.kill()


def test_the_filled_form_screenshot_becomes_a_document_served_inline(api):
    aid = make_app(api)
    with_runner(api, screenshot="filled_form.png")
    api.post(f"/v1/applications/{aid}/assist", json={"url": WORKABLE_URL})
    assert "filled_form.png" not in api.get(f"/v1/applications/{aid}").json()["documents"]
    api.get(f"/v1/applications/{aid}/assist")  # reading the status registers the screenshot
    assert "filled_form.png" in api.get(f"/v1/applications/{aid}").json()["documents"]
    r = api.get(f"/v1/applications/{aid}/documents/filled_form.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "inline" in r.headers["content-disposition"] and r.content.startswith(b"\x89PNG")
