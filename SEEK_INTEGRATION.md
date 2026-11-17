# SEEK integration

Status in the app: **Search link only + manual import.** It is never shown as a data connection.

## 1. What is technically possible
SEEK offers an API through its developer programme ([developer.seek.com](https://developer.seek.com/introduction)): job posting and applicant-management interfaces for recruitment software, and "Apply with SEEK" OAuth 2.0 flows (authorization-code and client-credentials). Access starts with a request-access form and SEEK review. I found no programmatic job-search interface for candidates; third-party sources say SEEK's docs state this, but I could not confirm the exact wording on SEEK's own pages.

## 2. What is contractually permitted
SEEK's API terms ([API Terms of Use](https://sg.employer.seek.com/partners/terms-of-use)) say credentials are issued to approved partners and that SEEK need not approve any product. Automated browsing or scraping of listings is not something a personal tool may rely on. Your brief also rules it out.

## 3. What requires approval
Any API credentials: submit the [integration request](https://au.employer.seek.com/partners/integration-request/). It is aimed at employer-side recruitment software, so a personal job-search app may not qualify. **No request has been made** and none will be without your approval.

## 4. What is implemented
- `SeekConnector` (`backend/app/sources/connectors.py`), registered as `seek`.
- **Search link:** built from your criteria (keywords, title, city or suburb, date posted, employment type, salary). Filters SEEK's link cannot carry (distance, experience, work rights) are listed as "not carried".
- **Manual import:** you copy a listing from your own browser and paste its text (and optionally its link). The link must be a seek.com.au address. The job ID is read from `/job/<id>`.
- Credential slots `SEEK_CLIENT_ID` / `SEEK_CLIENT_SECRET` in `.env`. They are stored, shown as "Stored (not used)", and **nothing is called with them**.
- Imports are normalised into the shared job schema, de-duplicated against jobs from other sources, scored and gated like any other job, and tagged `source_method=manual_import`, `permission_status=user_supplied`.

## 5. What remains unavailable
Fetching SEEK listings by machine, background refresh, expiry checks against SEEK, and applying through SEEK. The app never opens SEEK in an automated browser, so none of these exist.

## 6. How to enable the full integration later
1. You decide whether to apply for access (the form above).
2. If approved, put the credentials in `.env`.
3. Implement `SeekConnector._api_search` and `get_job` against the endpoints SEEK documents for your approval, and change `get_source_status` to report `API_CONNECTED` only after a successful `health_check`. The interface, tests and Sources page already treat API sources differently from link sources.
