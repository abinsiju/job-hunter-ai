from datetime import date

from app.verify.posting import (Posting, judge_open, parse_date, posting_from_workday, postings_from_html, workday_json_url)

TODAY = date(2026, 11, 10)
LD = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Service Desk Analyst","description":"<p>Support staff.</p><ul><li>M365</li></ul><p>Must be an Australian citizen.</p>",
 "datePosted":"2026-11-02","validThrough":"2026-12-01T00:00:00Z","employmentType":["FULL_TIME"],"identifier":{"@type":"PropertyValue","value":"REQ-123"},
 "hiringOrganization":{"@type":"Organization","name":"AustralianSuper"},"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Melbourne","addressRegion":"VIC","addressCountry":"AU"}},
 "baseSalary":{"@type":"MonetaryAmount","currency":"AUD","value":{"@type":"QuantitativeValue","minValue":80000,"maxValue":95000,"unitText":"YEAR"}},"url":"https://careers.example.com/apply/123"}
</script></head><body><h1>Service Desk Analyst</h1></body></html>"""


def test_jobposting_json_ld_is_read_into_the_common_shape():
    [p] = postings_from_html(LD, "https://careers.example.com/job/123")
    assert (p.title, p.employer, p.locations, p.job_id, p.employment) == ("Service Desk Analyst", "AustralianSuper", ["Melbourne, VIC"], "REQ-123", "FULL_TIME")
    assert (p.posted, p.valid_through) == (date(2026, 11, 2), date(2026, 12, 1)) and (p.salary_min, p.salary_max, p.salary_period) == (80000, 95000, "year")
    assert "Must be an Australian citizen." in p.text and "- M365" in p.text and "<" not in p.text
    assert p.apply_url == "https://careers.example.com/apply/123" and p.source == "json-ld" and p.remote is False


def test_graph_lists_remote_jobs_and_junk_json_are_handled():
    html = ('<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},{"@type":"JobPosting","title":"Dev","jobLocationType":"TELECOMMUTE",'
            '"hiringOrganization":"Acme","description":"x"}]}</script><script type="application/ld+json">{not json</script>')
    [p] = postings_from_html(html, "https://x.example/j")
    assert p.title == "Dev" and p.remote is True and p.employer == "Acme" and p.locations == []
    assert postings_from_html("<html>no structured data</html>", "u") == [] and postings_from_html('<script type="application/ld+json">[]</script>', "u") == []


def test_dates_in_every_shape_the_feeds_use():
    assert parse_date("2026-11-02") == date(2026, 11, 2) and parse_date("2026-11-02T10:00:00+11:00") == date(2026, 11, 2)
    assert parse_date("2026-11-02T10:00:00Z") == date(2026, 11, 2) and parse_date(1762041600000) == date(2025, 11, 2) and parse_date("garbage") is None and parse_date(None) is None


def mk(**kw): return Posting(title="x", url="u", **kw)


def test_open_unless_the_page_says_otherwise():
    assert judge_open(mk(valid_through=date(2026, 12, 1)), "Apply now", 200, TODAY).open is True
    assert judge_open(mk(), "", 200, TODAY).open is True


def test_closed_signals_each_mark_the_posting_closed_with_a_reason():
    p = judge_open(mk(), "", 404, TODAY)
    assert p.open is False and "HTTP 404" in p.closed_reason
    assert judge_open(mk(), "", 410, TODAY).open is False
    p = judge_open(mk(valid_through=date(2026, 11, 1)), "", 200, TODAY)
    assert p.open is False and "2026-11-01" in p.closed_reason
    for text in ["Sorry, this job is no longer available.", "This position has been filled.", "Applications are now closed", "The vacancy has expired",
                 "We are no longer accepting applications.", "This job has expired", "This role has been closed"]:
        q = judge_open(mk(), f"Header. {text} Footer.", 200, TODAY)
        assert q.open is False and "says the job is closed" in q.closed_reason, text


def test_ordinary_text_does_not_trigger_the_closed_detector():
    for text in ["Our close-knit team", "Applications close 30 November 2026", "You will help close tickets", "We are hiring now. Position available."]:
        assert judge_open(mk(), text, 200, TODAY).open is True, text


def test_workday_links_map_to_the_json_the_career_site_reads():
    assert workday_json_url("https://acme.wd3.myworkdayjobs.com/en-US/External/job/Melbourne-VIC/Service-Desk-Analyst_R1234") == \
        "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/External/job/Melbourne-VIC/Service-Desk-Analyst_R1234"
    assert workday_json_url("https://acme.wd3.myworkdayjobs.com/Careers/job/Sydney/Role_R9") == "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers/job/Sydney/Role_R9"
    assert workday_json_url("https://acme.com/jobs/1") is None and workday_json_url("https://acme.wd3.myworkdayjobs.com/en-US/External") is None


def test_workday_json_becomes_a_posting_and_can_report_closed():
    data = {"jobPostingInfo": {"title": "Analyst", "jobDescription": "<p>Do things</p>", "location": "Melbourne", "startDate": "2026-11-03", "jobReqId": "R1", "canApply": False,
                               "externalUrl": "https://acme.wd3.myworkdayjobs.com/x"}, "hiringOrganization": {"name": "Acme"}}
    p = posting_from_workday(data, "u")
    assert (p.title, p.employer, p.job_id, p.posted, p.open) == ("Analyst", "Acme", "R1", date(2026, 11, 3), False) and "can no longer be applied" in p.closed_reason
    assert posting_from_workday({}, "u") is None
