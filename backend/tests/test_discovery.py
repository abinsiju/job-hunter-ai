import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import discover_cli
from app.core.config import settings
from app.db.base import Base
from app.db.models import Application, Listing
from app.db.seed import seed_facts
from app.discovery.adzuna import AdzunaSource
from app.discovery.base import RawListing, SourceError, html_to_text
from app.discovery.triage import ranked, triage_listing, triage_pending, upsert_listings
from app.discovery.workable import WorkableSource
from app.matching.gate import evaluate_gate
from app.matching.schema import JobExtract, Salary
from app.tracker.service import create_or_refresh_draft, update_status
from tests.conftest import FakeAI, PipeAI
from tests.fixtures.jobs import EXTRACT

SEED = Path(__file__).resolve().parents[2] / "data" / "profile_seed.json"
TODAY = date(2026, 11, 9)
KEY = "test-key-not-real"


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


# ------------------------------------------------------------------ fixtures shaped like the real feeds
def adzuna_payload(n=2, **over):
    base = {"__CLASS__": "Adzuna::API::Response::Job", "id": "1001", "title": "<strong>IT</strong> Support Officer",
            "company": {"display_name": "Westside Logistics"}, "location": {"display_name": "Werribee, Melbourne", "area": ["Australia", "Victoria"]},
            "description": "Support staff with <strong>Microsoft 365</strong> and Active Directory ...", "created": "2026-11-05T01:02:03Z",
            "redirect_url": "https://www.adzuna.com.au/land/ad/1001?se=abc", "salary_min": 62000, "salary_max": 70000, "salary_is_predicted": "0",
            "contract_time": "part_time", "contract_type": "permanent", "category": {"label": "IT Jobs", "tag": "it-jobs"}}
    return [{**base, "id": str(1001 + i), **over} for i in range(n)]


def adzuna_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def workable_feed():
    def job(code, city, country="Australia", title="Service Desk Analyst", et="Full-time", state="Victoria"):
        return {"title": title, "shortcode": code, "employment_type": et, "department": "Technology", "url": f"https://apply.workable.com/j/{code}",
                "published_on": "2026-10-28", "country": country, "city": city, "state": state,
                "description": "<p>Join our <b>service desk</b>.</p><ul><li>Microsoft 365</li><li>Active Directory</li></ul><p>Must have working rights.</p>"}
    return {"name": "First Focus", "description": "x", "jobs": [
        job("AAA111", "Melbourne"), job("AAA111", "Melbourne"),  # duplicate row for the same location
        job("AAA111", "Sydney", state="New South Wales"),         # same job, other location: a different listing
        job("BBB222", "Pasig", country="Philippines", state="Metro Manila"),
        job("CCC333", "Melbourne", title="Senior Data Engineer", et="Contract")]}


# ------------------------------------------------------------------ Adzuna
async def test_adzuna_builds_the_documented_request_and_parses_listings():
    seen = {}

    def handler(req):
        seen["url"], seen["params"] = req.url.path, dict(req.url.params)
        return httpx.Response(200, json={"results": adzuna_payload(2)})

    out = await AdzunaSource("APPID", KEY, client=adzuna_client(handler), delay=0).search("it support", "Melbourne", per_page=20, max_days_old=7, part_time=True)
    assert seen["url"] == "/v1/api/jobs/au/search/1"
    p = seen["params"]
    assert p["app_id"] == "APPID" and p["app_key"] == KEY and p["what"] == "it support" and p["where"] == "Melbourne"
    assert p["results_per_page"] == "20" and p["max_days_old"] == "7" and p["part_time"] == "1" and p["content-type"] == "application/json"
    a = out[0]
    assert (a.source, a.source_id, a.title, a.employer, a.location) == ("adzuna", "1001", "IT Support Officer", "Westside Logistics", "Werribee, Melbourne")
    assert a.description == "Support staff with Microsoft 365 and Active Directory ..." and a.posted_at == date(2026, 11, 5)
    assert a.description_complete is False and a.employment_hint == "part_time" and (a.salary_min, a.salary_period) == (62000, "year")


async def test_adzuna_predicted_salary_is_never_used():
    def handler(req):
        return httpx.Response(200, json={"results": adzuna_payload(1, salary_is_predicted="1", salary_min=20000, salary_max=22000)})
    a = (await AdzunaSource("i", KEY, client=adzuna_client(handler), delay=0).search("x"))[0]
    assert a.salary_min is None and a.salary_max is None and a.salary_period == "unknown"


async def test_adzuna_contract_type_wins_over_contract_time():
    def handler(req):
        return httpx.Response(200, json={"results": adzuna_payload(1, contract_type="contract", contract_time="full_time")})
    assert (await AdzunaSource("i", KEY, client=adzuna_client(handler), delay=0).search("x"))[0].employment_hint == "contract"


async def test_adzuna_paginates_until_a_short_page_and_caps_at_pages():
    pages = []

    def handler(req):
        pages.append(req.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json={"results": adzuna_payload(2 if len(pages) < 3 else 1)})

    out = await AdzunaSource("i", KEY, client=adzuna_client(handler), delay=0).search("x", pages=5, per_page=2)
    assert pages == ["1", "2", "3"] and len(out) == 5
    pages.clear()
    await AdzunaSource("i", KEY, client=adzuna_client(handler), delay=0).search("x", pages=1, per_page=2)
    assert pages == ["1"]


@pytest.mark.parametrize("status,hint", [(401, "check ADZUNA"), (403, "check the keys"), (429, "rate limited"), (500, "HTTP 500")])
async def test_adzuna_errors_are_clear_and_never_leak_the_key(status, hint, caplog):
    caplog.set_level(logging.DEBUG)
    src = AdzunaSource("APPID", KEY, client=adzuna_client(lambda r: httpx.Response(status)), delay=0)
    with pytest.raises(SourceError) as e:
        await src.search("x")
    assert hint in str(e.value) and KEY not in str(e.value) and "APPID" not in str(e.value) and KEY not in caplog.text


async def test_adzuna_network_failure_does_not_leak_the_url():
    def handler(req):
        raise httpx.ConnectError("boom", request=req)
    with pytest.raises(SourceError) as e:
        await AdzunaSource("APPID", KEY, client=adzuna_client(handler), delay=0).search("x")
    assert "ConnectError" in str(e.value) and KEY not in str(e.value) and "http" not in str(e.value)


def test_adzuna_requires_keys_and_names_the_env_vars():
    for args in (("", ""), ("id", ""), ("", "key")):
        with pytest.raises(SourceError, match="ADZUNA_APP_ID and ADZUNA_APP_KEY"):
            AdzunaSource(*args)


# ------------------------------------------------------------------ Workable
async def test_workable_keeps_australia_dedupes_per_location_and_converts_html():
    src = WorkableSource("first-focus", "First Focus", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=workable_feed()))))
    out = await src.search()
    assert [l.source_id for l in out] == ["AAA111:Melbourne", "AAA111:Sydney", "CCC333:Melbourne"]  # Philippines dropped, duplicate row dropped
    l = out[0]
    assert l.source == "workable:first-focus" and l.employer == "First Focus" and l.location == "Melbourne, Victoria"
    assert l.description_complete is True and l.employment_hint == "full_time" and l.posted_at == date(2026, 10, 28)
    assert "- Microsoft 365" in l.description and "<" not in l.description and "Must have working rights." in l.description
    assert [x.source_id for x in await src.search(city="Sydney")] == ["AAA111:Sydney"]
    assert [x.source_id for x in await src.search(title_contains=["service desk"])] == ["AAA111:Melbourne", "AAA111:Sydney"]
    assert out[2].employment_hint == "contract"


async def test_workable_errors_name_the_account_not_a_secret():
    src = WorkableSource("nope", "X", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(SourceError, match="HTTP 404 for account 'nope'"):
        await src.search()


def test_html_to_text():
    assert html_to_text("<p>A &amp; B</p><ul><li>one</li><li>two</li></ul>") == "A & B\n\n- one\n- two"
    assert html_to_text(None) == "" and html_to_text("plain") == "plain"


# ------------------------------------------------------------------ triage
def raw(**kw):
    d = dict(source="adzuna", source_id="1", url="https://x.example/1", title="IT Support Officer", employer="Westside Logistics",
             location="Werribee, Melbourne", description="Support staff with Microsoft 365 and Active Directory.", description_complete=False,
             posted_at=date(2026, 11, 5), employment_hint="part_time")
    return RawListing(**{**d, **kw})


def ai_for(job_key="it_support_pt", **update):
    return PipeAI(EXTRACT[job_key].model_copy(update=update))


def test_upsert_inserts_new_refreshes_known_and_keeps_their_triage(db):
    assert upsert_listings(db, [raw(), raw(source_id="2")]) == (2, 0)
    l = db.query(Listing).filter_by(source_id="1").one()
    l.score, l.gate_status = 50.0, "PASS"
    db.commit()
    assert upsert_listings(db, [raw(), raw(source_id="3")]) == (1, 1)
    assert db.query(Listing).count() == 3 and db.query(Listing).filter_by(source_id="1").one().score == 50.0


async def test_a_snippet_listing_can_never_pass_but_a_full_ad_can(db):
    upsert_listings(db, [raw(source_id="snip", description_complete=False), raw(source_id="full", description_complete=True)])
    snip, full = [db.query(Listing).filter_by(source_id=i).one() for i in ("snip", "full")]
    await triage_listing(db, ai_for(), snip, TODAY)
    await triage_listing(db, ai_for(), full, TODAY)
    assert full.gate_status == "PASS" and snip.gate_status == "REVIEW"
    assert any("only a snippet" in f for f in snip.flags) and not any("snippet" in f for f in full.flags)
    assert snip.score == full.score and snip.triaged_at and snip.breakdown["technical_skills"]["max_points"] == 30


async def test_a_gate_fail_stays_fail_even_for_a_snippet(db):
    upsert_listings(db, [raw(description="Australian citizens only. Baseline security clearance.")])
    l = db.query(Listing).one()
    await triage_listing(db, PipeAI(EXTRACT["aps_role"]), l, TODAY)
    assert l.gate_status == "FAIL" and any("citizenship" in r for r in l.fail_reasons)


async def test_source_fields_override_the_models_guesses(db):
    upsert_listings(db, [raw(employer="Real Employer", location="Exampleton VIC", title="Help Desk Analyst", description_complete=True)])
    l = db.query(Listing).one()
    await triage_listing(db, ai_for(employer="Wrong Co", location="Sydney", title="Wrong"), l, TODAY)
    assert (l.extract["employer"], l.extract["location"], l.extract["title"]) == ("Real Employer", "Exampleton VIC", "Help Desk Analyst")


async def test_listing_for_an_already_applied_vacancy_is_a_hard_fail(db):
    a, _ = create_or_refresh_draft(db, employer="Westside Logistics", title="IT Support Officer", location="Werribee, Melbourne")
    update_status(db, a.id, "approved")
    update_status(db, a.id, "applied", evidence="Confirmation email ref 5512")
    upsert_listings(db, [raw(description_complete=True)])
    l = db.query(Listing).one()
    await triage_listing(db, ai_for(), l, TODAY)
    assert l.gate_status == "FAIL" and any(f"already applied to this vacancy (#{a.id}" in r for r in l.fail_reasons)
    assert l not in ranked(db) and l in ranked(db, include_fail=True)


async def test_a_below_minimum_wage_listing_is_flagged_but_a_predicted_salary_is_not(db):
    upsert_listings(db, [raw(source_id="low", salary_min=20, salary_max=20, salary_period="hour", description_complete=True),
                         raw(source_id="pred", description_complete=True)])  # source dropped the predicted salary
    low, pred = [db.query(Listing).filter_by(source_id=i).one() for i in ("low", "pred")]
    await triage_listing(db, ai_for(salary=Salary()), low, TODAY)
    await triage_listing(db, ai_for(salary=Salary()), pred, TODAY)
    assert any("below the national minimum" in f for f in low.flags) and not any("minimum" in f for f in pred.flags)


async def test_extraction_failure_leaves_the_listing_untriaged_for_a_retry(db):
    class Bad(FakeAI):
        async def chat(self, *a, **k):
            return "not json"
    upsert_listings(db, [raw()])
    l = db.query(Listing).one()
    await triage_listing(db, Bad(), l, TODAY)
    assert l.triaged_at is None and l.score is None
    assert len(await triage_pending(db, ai_for(), TODAY)) == 1 and db.query(Listing).one().triaged_at  # retried and scored


async def test_triage_pending_respects_the_limit_and_skips_scored(db):
    upsert_listings(db, [raw(source_id=str(i)) for i in range(5)])
    assert len(await triage_pending(db, ai_for(), TODAY, limit=3)) == 3
    assert len(await triage_pending(db, ai_for(), TODAY, limit=10)) == 2
    assert await triage_pending(db, ai_for(), TODAY) == []


def test_ranking_puts_pass_above_review_regardless_of_score_and_hides_fail_and_untriaged(db):
    for i, (st, sc) in enumerate([("REVIEW", 99.0), ("PASS", 60.0), ("PASS", 80.0), ("FAIL", 100.0), ("REVIEW", 70.0)]):
        db.add(Listing(source="t", source_id=str(i), url="u", title=f"{st}-{sc:g}", gate_status=st, score=sc, triaged_at=TODAY and __import__("datetime").datetime.now()))
    db.add(Listing(source="t", source_id="new", url="u", title="untriaged"))
    db.commit()
    assert [l.title for l in ranked(db)] == ["PASS-80", "PASS-60", "REVIEW-99", "REVIEW-70"]
    assert [l.title for l in ranked(db, include_fail=True)][-1] == "FAIL-100" or "FAIL-100" in [l.title for l in ranked(db, include_fail=True)]
    assert "untriaged" not in [l.title for l in ranked(db, include_fail=True)]


def test_the_snippet_rule_is_a_gate_property(db):
    from app.matching.loader import load_candidate_profile
    p = load_candidate_profile(db)
    job = EXTRACT["it_support_pt"]
    assert evaluate_gate(job, p, TODAY, text_complete=True).status == "PASS"
    g = evaluate_gate(job, p, TODAY, text_complete=False)
    assert g.status == "REVIEW" and any("snippet" in f for f in g.flags)
    cit = JobExtract(title="x", work_rights_requirements=["citizenship"])
    assert evaluate_gate(cit, p, TODAY, text_complete=False).status == "FAIL"


# ------------------------------------------------------------------ the watch-list file
PAGES = json.loads((Path(__file__).resolve().parents[1] / "app" / "discovery" / "career_pages.json").read_text())


def test_career_pages_only_automate_verified_feeds():
    assert PAGES["verified_on"] and len(PAGES["pages"]) >= 8
    for p in PAGES["pages"]:
        assert p["url"].startswith("https://") and p["mode"] in ("feed", "manual", "excluded") and p["kind"] in ("msp", "recruiter")
        if p["mode"] == "feed":
            assert p["robots"] == "allows" and p["platform"] == "workable" and p["workable_slug"]
        if p["mode"] == "excluded":
            assert "blocked" in p["robots"] and "Blocks automated access" in p["note"]
    kmtech = next(p for p in PAGES["pages"] if p["name"] == "KMTech")
    assert kmtech["mode"] == "excluded"  # the site returns 403 to automated clients: never fetched
    assert [p["name"] for p in PAGES["pages"] if p["mode"] == "feed"] == ["First Focus"]


# ------------------------------------------------------------------ CLI (test database + mocked sources only)
@pytest.fixture
def clidb(engine, db, monkeypatch):
    monkeypatch.setattr(discover_cli, "get_session", lambda: Session(engine))
    return db


def run(argv):
    from app import cli
    return cli.main(argv)


def test_cli_search_without_keys_explains_how_to_get_them(clidb, monkeypatch, capsys):
    monkeypatch.setattr(settings, "adzuna_app_id", "")
    monkeypatch.setattr(settings, "adzuna_app_key", "")
    assert run(["discover", "search", "--what", "it support"]) == 2
    err = capsys.readouterr().err
    assert "ADZUNA_APP_ID" in err and "developer.adzuna.com" in err and clidb.query(Listing).count() == 0


def test_cli_search_end_to_end_with_mocked_adzuna(clidb, monkeypatch, capsys):
    monkeypatch.setattr(settings, "adzuna_app_id", "I")
    monkeypatch.setattr(settings, "adzuna_app_key", KEY)
    monkeypatch.setattr(discover_cli, "AdzunaSource", lambda i, k: AdzunaSource(i, k, client=adzuna_client(lambda r: httpx.Response(200, json={"results": adzuna_payload(2)})), delay=0))
    monkeypatch.setattr(discover_cli, "get_provider", lambda: ai_for())
    assert run(["discover", "search", "--what", "it support", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "2 new" in out and "Scored 2" in out and "IT Support Officer" in out and "REVIEW" in out and "snippet only" in out and KEY not in out
    assert run(["discover", "search", "--what", "it support"]) == 0
    assert "0 new, 2 already known" in capsys.readouterr().out


def test_cli_list_show_and_draft_refusal_for_snippets(clidb, monkeypatch, capsys):
    clidb.add(Listing(source="adzuna", source_id="9", url="https://www.adzuna.com.au/land/ad/9", title="Service Desk Analyst", employer="Acme",
                      location="Melbourne", description="snippet", description_complete=False, gate_status="REVIEW", score=71.0,
                      flags=["only a snippet of the ad was available"], triaged_at=__import__("datetime").datetime.now(),
                      breakdown={"technical_skills": {"points": 20.0, "max_points": 30}}))
    clidb.commit()
    assert run(["discover", "list"]) == 0
    out = capsys.readouterr().out
    assert "Service Desk Analyst" in out and "adzuna*" in out and "can never show as PASS" in out
    assert run(["discover", "show", "1"]) == 0 and "only a snippet" in capsys.readouterr().out
    assert run(["discover", "draft", "1"]) == 2
    err = capsys.readouterr().err
    assert "only has a snippet" in err and "https://www.adzuna.com.au/land/ad/9" in err and "draft AD.txt" in err
    assert run(["discover", "show", "999"]) == 2


def test_cli_draft_from_a_full_ad_listing_writes_documents_and_tracks_it(clidb, monkeypatch, capsys, tmp_path):
    upsert_listings(clidb, [raw(source="workable:first-focus", source_id="AAA111:Melbourne", url="https://apply.workable.com/j/AAA111",
                                description="Join our service desk. Microsoft 365 and Active Directory.", description_complete=True)])
    monkeypatch.setattr(discover_cli, "get_provider", lambda: ai_for())
    assert run(["discover", "draft", "1", "--out", str(tmp_path)]) == 0
    a = clidb.query(Application).one()
    assert a.status == "drafted" and a.source == "workable:first-focus" and a.source_url == "https://apply.workable.com/j/AAA111"
    assert all(Path(p).exists() for p in a.doc_paths.values()) and "NOT submitted" in capsys.readouterr().out


def test_internship_without_stated_pay_is_flagged_to_check_it_is_paid():
    from app.matching.gate import pay_flags
    assert any("check that it is paid" in f for f in pay_flags(JobExtract(title="IT Support Intern")))
    assert any("check that it is paid" in f for f in pay_flags(JobExtract(title="Support Analyst", employment_type="internship")))
    assert any("check that it is paid" in f for f in pay_flags(JobExtract(title="Work Experience Placement - IT")))
    assert pay_flags(JobExtract(title="IT Support Intern", salary=Salary(min=30, period="hour"))) == []  # pay stated and above the minimum
    assert pay_flags(JobExtract(title="Internal Systems Officer")) == []  # 'internal' is not 'intern'
    assert any("unpaid" in f for f in pay_flags(JobExtract(title="IT Intern", unpaid=True)))


def test_repeated_postings_of_the_same_vacancy_collapse_in_the_ranked_list(db):
    from datetime import datetime
    now = datetime.now()
    for i, (emp, title, loc, sc) in enumerate([("Workplace Injury Co", "Senior Systems Support Officer", "Melbourne", 74.7),
                                               ("Workplace Injury Co.", "Senior Systems Support Officer (Full-time)", "Melbourne VIC", 74.7),
                                               ("Workplace Injury Co", "Senior Systems Support Officer", "Sydney", 70.0),
                                               ("Other Co", "Service Desk Analyst", "Melbourne", 60.0)]):
        db.add(Listing(source="t", source_id=str(i), url="u", title=title, employer=emp, location=loc, gate_status="REVIEW", score=sc, triaged_at=now))
    db.commit()
    rows = ranked(db)
    assert [(l.employer, l.location) for l in rows] == [("Workplace Injury Co", "Melbourne"), ("Workplace Injury Co", "Sydney"), ("Other Co", "Melbourne")]
    assert rows[0].similar == 1 and rows[1].similar == 0 and rows[2].similar == 0  # Sydney is a different vacancy: not collapsed
    assert len(ranked(db, collapse=False)) == 4


def test_pay_quoted_per_month_or_week_becomes_a_yearly_figure_instead_of_crashing_triage():
    from app.discovery.triage import annual_salary
    assert annual_salary(5000, 6000, "month").min == 60000 and annual_salary(5000, 6000, "month").period == "year"
    assert annual_salary(1500, None, "week").min == 78000 and annual_salary(30, 35, "hour").period == "hour"
    assert annual_salary(1, 2, "century") is None and annual_salary(None, None, "month").min is None
