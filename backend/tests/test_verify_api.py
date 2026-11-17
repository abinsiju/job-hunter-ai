from datetime import date

import httpx

from app.main import app
from app.verify.fetch import PoliteFetcher
from tests.test_api_v1 import api, db, engine, listing, wait  # noqa: F401  (fixtures and helpers)
from tests.test_verify_resolve import CLEAN, FULL, SNIPPET, gh_board, job


def serve(routes):
    def make():
        def h(req):
            u = str(req.url).split("?")[0]
            for k, v in routes.items():
                if u.endswith(k):
                    return v if isinstance(v, httpx.Response) else httpx.Response(200, json=v)
            return httpx.Response(404, json={})
        async def nosleep(s): pass
        return PoliteFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(h)), delay=0, sleep=nosleep)
    return make


def test_verify_endpoint_stores_both_versions_and_rescored_gate_never_passes_on_a_mismatch(api):
    l = listing(api.db, description=SNIPPET, location="Melbourne, VIC", posted_at=date(2026, 11, 3), url="https://www.adzuna.com.au/land/ad/5544332211")
    app.state.fetcher_factory = serve({"boards-api.greenhouse.io/v1/boards/acme/jobs": gh_board([job()]), "boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme Pty Ltd"}})
    try:
        r = api.post(f"/v1/listings/{l.id}/verify", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["confidence"] == "VERIFIED" and any(d["field"] == "work_rights" for d in body["discrepancies"])
        d = api.get(f"/v1/listings/{l.id}").json()
        v = d["verification"]
        assert v["confidence"] == "VERIFIED" and v["employer_url"].endswith("/jobs/7001") and v["application_route"] == "employer_ats"
        assert v["third_party"]["gate_status"] == "REVIEW" and d["description"] == SNIPPET  # both versions kept
        assert d["gate"] == "FAIL" and d["can_draft"] is False  # the employer's own ad says citizens only: the snippet hid it
        assert any("citizen" in x.lower() for x in d["fail_reasons"])
        row = api.get("/v1/listings?gate=fail").json()["items"][0]
        assert row["confidence"] == "VERIFIED" and row["discrepancy_count"] >= 1 and row["snippet_only"] is False
    finally:
        del app.state.fetcher_factory


def test_pasted_employer_link_that_is_gone_marks_the_job_closed_and_blocks_drafting(api):
    l = listing(api.db, description=SNIPPET)
    app.state.fetcher_factory = serve({"careers.acme.test/jobs/9": httpx.Response(404, text="gone", headers={"content-type": "text/html"})})
    try:
        r = api.post(f"/v1/listings/{l.id}/verify", json={"url": "https://careers.acme.test/jobs/9"})
        assert r.status_code == 200 and r.json()["open_status"] == "closed"
    finally:
        del app.state.fetcher_factory
    assert api.get("/v1/listings?gate=all").json()["items"] == []  # out of every open list
    assert api.get("/v1/listings").json()["counts"]["closed"] == 1
    [c] = api.get("/v1/listings?gate=closed").json()["items"]
    d = api.get(f"/v1/listings/{c['id']}").json()
    assert d["can_draft"] is False and "closed" in d["draft_hint"]
    j = api.post("/v1/jobs", json={"kind": "draft", "params": {"listing_id": l.id}})
    assert j.status_code == 422 and "closed" in j.text


def test_verify_input_is_validated(api):
    l = listing(api.db)
    assert api.post(f"/v1/listings/{l.id}/verify", json={"url": "javascript:alert(1)"}).status_code == 422
    assert api.post("/v1/listings/9999/verify", json={}).status_code == 404
    app.state.fetcher_factory = serve({})
    try:
        r = api.post(f"/v1/listings/{l.id}/verify", json={"url": "https://www.seek.com.au/job/1"}).json()
        assert r["confidence"] == "SNIPPET_ONLY" and any("could not read" in n for n in r["notes"])
    finally:
        del app.state.fetcher_factory


def test_snippet_only_listing_can_never_be_pass(api):
    from app.discovery.triage import _verification_effects
    l = listing(api.db, confidence="SNIPPET_ONLY")
    assert _verification_effects(l, "PASS", [], [])[0] == "REVIEW"
    l.confidence, l.discrepancies = "VERIFIED", [{"severity": "note", "label": "Salary", "third_party": "a", "employer": "b"}]
    assert _verification_effects(l, "PASS", [], [])[0] == "PASS"
    l.discrepancies = [{"severity": "review", "label": "Work rights: citizenship", "third_party": "(not mentioned)", "employer": "Must be a citizen."}]
    st, _, flags = _verification_effects(l, "PASS", [], [])
    assert st == "REVIEW" and "Must be a citizen." in flags[-1] and "(not mentioned)" in flags[-1]
