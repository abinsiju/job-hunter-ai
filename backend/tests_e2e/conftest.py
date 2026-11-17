"""Browser tests. They start their OWN API (8101) and frontend (3101) on the throwaway test database, so they never
touch the real dashboard, database or documents. Run:  cd backend && uv run pytest tests_e2e -q"""
import json
import os
import secrets
import subprocess
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import Listing
from app.db.seed import seed_facts
from app.tracker.seed import seed_applied
from app.tracker.service import create_or_refresh_draft, update_status

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "profile_seed.json"
API, WEB = "http://127.0.0.1:8101", "http://127.0.0.1:3101"
PASSWORD = "e2e-" + secrets.token_urlsafe(14)  # random per run: the test stack never uses a fixed credential
TEST_DB_URL = settings.database_url.rsplit("/", 1)[0] + "/jobhunter_test"


def _wait(url: str, timeout: int = 150, ok=(200, 307, 401)):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status in ok:
                    return
        except urllib.error.HTTPError as e:
            if e.code in ok:
                return
        except Exception:
            pass
        time.sleep(0.7)
    raise RuntimeError(f"{url} did not come up")


def breakdown(tech=20.0):
    cat = lambda p, m, matched=(), missing=(), notes=(): {"points": p, "max_points": m, "matched": list(matched), "missing": list(missing), "notes": list(notes)}
    return {"technical_skills": cat(tech, 30, ["Microsoft 365 ~ M365 (1.00)"], ["Azure", "VMware"]), "experience": cat(20.0, 20), "qualification": cat(15.0, 15),
            "role_relevance": cat(15.0, 15), "location_preferences": cat(8.0, 10, notes=["location tier: Melbourne (4/5)"]), "other_eligibility": cat(7.5, 10)}


def listing(db, **kw):
    now = datetime.now(timezone.utc)
    d = dict(source="adzuna", source_id=str(time.monotonic_ns()), url="https://example.test/ad", title="IT Support Officer", employer="Acme", location="Melbourne",
             description="Support staff with Microsoft 365 and Active Directory. Part-time role.", description_complete=False, gate_status="REVIEW", score=74.5,
             posted_at=date.today() - timedelta(days=2), triaged_at=now, first_seen=now, flags=["only a snippet of the ad was available: read the full ad before applying"],
             fail_reasons=[], breakdown=breakdown(), extract={"employment_type": "part_time", "required_skills": ["Microsoft 365", "Active Directory"],
                                                              "nice_to_have_skills": ["SharePoint"], "qualifications": [], "min_years_experience": None})
    db.add(Listing(**{**d, **kw}))
    db.commit()


def reseed(engine):
    """Reset to the known dataset. A request from the previous test can still be finishing on the server and lock tables in the
    opposite order, which Postgres reports as a deadlock: simply retry."""
    for attempt in range(8):
        try:
            return _reseed(engine)
        except OperationalError:
            time.sleep(0.4 * (attempt + 1))
    return _reseed(engine)


def _reseed(engine):
    with Session(engine) as db:
        db.execute(text("truncate profile_facts, applications, listings restart identity cascade"))
        db.commit()
        seed_facts(db, json.loads(SEED.read_text()))
        listing(db, title="Service Desk Analyst", employer="Acme Pty Ltd", location="Melbourne CBD", gate_status="PASS", score=88.0, flags=[],
                description_complete=True, source="workable:first-focus", url="https://example.test/ad/1", description="Join our service desk team. Microsoft 365, Active Directory.")
        listing(db, title="IT Support Officer", employer="Westside Logistics", location="Werribee, Melbourne", score=74.5, url="https://example.test/ad/2")
        listing(db, title="Help Desk Technician", employer="Globex", location="Sydney", score=66.0, url="https://example.test/ad/3")
        listing(db, title="IT Support Officer APS4", employer="Department of Example", location="Canberra", gate_status="FAIL", score=91.0, flags=[],
                fail_reasons=["requires citizenship"], url="https://example.test/ad/4")
        listing(db, title="IT Support Intern", employer="Study and Work", location="Melbourne", score=80.5, url="https://example.test/ad/5",
                flags=["internship run by a placement/matching agency: these programs often charge the candidate, check for fees before engaging"])
        seed_applied(db, [{"employer": "Example Studios"}, {"employer": "Example Recruiters", "title": "Service Desk Analyst"}])
        create_or_refresh_draft(db, employer="Delta Corp", title="Support Analyst", location="Melbourne", source="adzuna", url="https://example.test/d")
        e, _ = create_or_refresh_draft(db, employer="Echo Ltd", title="Service Desk Officer", location="Geelong", source="manual")
        update_status(db, e.id, "approved")
        f, _ = create_or_refresh_draft(db, employer="Foxtrot Pty Ltd", title="IT Officer", location="Melbourne", source="manual")
        update_status(db, f.id, "approved")
        update_status(db, f.id, "applied", evidence="Confirmation email ref 7781")


@pytest.fixture(scope="session")
def engine():
    if not SEED.exists():
        pytest.skip("data/profile_seed.json not present")
    e = create_engine(TEST_DB_URL)
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    yield e
    Base.metadata.drop_all(e)


@pytest.fixture(scope="session")
def stack(engine, tmp_path_factory):
    reseed(engine)
    gen = tmp_path_factory.mktemp("generated")
    from tests.fake_imap import FakeGmail
    gmail = FakeGmail()  # stands in for Gmail: the real account is never touched by tests
    env = {**os.environ, "GMAIL_ADDRESS": gmail.user, "GMAIL_APP_PASSWORD": gmail.password, "GMAIL_IMAP_HOST": "127.0.0.1", "GMAIL_IMAP_PORT": str(gmail.port),
           "GMAIL_IMAP_SSL": "false", "ASSIST_TEST_HOSTS": "127.0.0.1", "ASSIST_HEADLESS": "true", "DATABASE_URL": TEST_DB_URL, "JWT_SECRET": "e2e-" + "x" * 48, "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": hash_password(PASSWORD),
           "ADZUNA_APP_ID": "", "ADZUNA_APP_KEY": "", "GENERATED_DIR": str(gen), "ACCESS_TOKEN_MINUTES": "480"}
    api = subprocess.Popen(["uv", "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8101"], cwd=ROOT / "backend", env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    web = subprocess.Popen(["npm", "run", "dev", "--", "-H", "127.0.0.1", "-p", "3101"], cwd=ROOT / "frontend",
                           env={**env, "BACKEND_URL": API, "NEXT_DIST_DIR": ".next-e2e"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait(API + "/health")
        _wait(WEB + "/login")
        yield {"api": API, "web": WEB, "generated": gen, "gmail": gmail}
    finally:
        gmail.close()
        for p in (web, api):
            p.terminate()
        for p in (web, api):
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.fixture(scope="session")
def browser(stack):
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def fresh(engine, stack):
    reseed(engine)  # every test starts from the same known data
    return stack


class Ui:
    def __init__(self, browser, web, width=1440, height=900, mobile=False, scheme="light"):
        self.ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile, color_scheme=scheme)
        self.ctx.set_default_timeout(60000)
        self.page = self.ctx.new_page()
        self.web = web
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text[:240]) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)[:240]))

    def login(self, password=PASSWORD):
        self.page.goto(self.web + "/login")
        self.page.fill("#p", password)
        self.page.click("button[type=submit]")

    def signed_in(self):
        self.login()
        self.page.wait_for_url(self.web + "/")
        return self.page

    def close(self):
        self.ctx.close()


@pytest.fixture
def ui(browser, fresh):
    u = Ui(browser, fresh["web"])
    yield u
    u.close()


@pytest.fixture
def phone(browser, fresh):
    u = Ui(browser, fresh["web"], 390, 844, mobile=True)
    yield u
    u.close()
