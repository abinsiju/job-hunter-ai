# Indeed integration

Status in the app: **Search link only + manual import.** Never shown as a data connection.

## 1. What is technically possible
Indeed's documented APIs ([docs.indeed.com](https://docs.indeed.com/)) are employer and applicant-tracking-system interfaces: the Job Sync API (create, update, expire and check postings; a GraphQL API), Indeed Apply, disposition sync, send-candidates and partner console tooling. I found nothing documented for candidate-side job search or a public job feed for individuals. (Indeed's own pages disagree about whether Job Sync is open to direct employers; it is an employer-side posting API either way.)

## 2. What is contractually permitted
Indeed prohibits automating Indeed Apply with scripts or bots outside its official vendors and tooling (see its [legal terms](https://au.indeed.com/legal) and the [Indeed Apply terms](https://docs.indeed.com/legal-terms/indeed-apply)). Scraping is not permitted for a personal tool.

## 3. What requires approval
Partner status for any API. None of them is a job-seeker discovery API, so applying would not give this app what it needs. No request has been made.

## 4. What is implemented
- `IndeedConnector`, registered as `indeed`.
- **Search link** (keywords, location, radius, date posted, job type, and salary as a search word). Hourly salary and work rights are listed as "not carried".
- **Manual import** from your own browser (the link must be an indeed.com address; the job ID is read from `jk=`).
- Credential slots `INDEED_CLIENT_ID` / `INDEED_CLIENT_SECRET`: stored, never used.
- **No automation of Indeed Apply of any kind.** `apply/policy.py` refuses `indeed.com` and `au.indeed.com` for the application assistant, and a test (`test_assist_refuses_other_sites_and_starts_nothing`) checks it.
- If a listing sends you to the employer's own site, import it with the employer's application link: the destination is stored separately (`application_url`, `ats_provider`) and judged under its own rules.

## 5. What remains unavailable
Fetching Indeed jobs by machine, job-alert ingestion (not built: it would need your mailbox rules and Indeed's alert format), freshness checks, Indeed Apply automation (prohibited).

## 6. How to enable the full integration later
Only if Indeed publishes a permitted job-discovery interface for your use: add credentials, implement `_api_search`, and report `API_CONNECTED` after `health_check`. Applying through Indeed would remain manual.
