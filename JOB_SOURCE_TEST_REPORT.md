# Job source test report

All tests use mocked network responses. **No real request was made to SEEK, Indeed or Jora**, and no scraping test was run. Run: `cd backend && uv run pytest tests/test_sources.py` (34 tests) and `uv run pytest tests_e2e/test_sources_ui.py` (3 browser tests).

| Area | Test | Result |
|---|---|---|
| Honesty | SEEK, Indeed and Jora report `SEARCH_LINK_ONLY`, `real_data_connection=false`, methods browser search + manual import; employer feeds report `FEED_CONNECTED` | PASS |
| Honesty | Stored credentials do not turn a link source into a connection; `search_jobs` returns only a link and `fetched_data=false` | PASS |
| Links | Filters carried into each link, and filters that cannot be carried are listed (distance, hourly salary, employment type, work rights, experience) | PASS |
| Successful search | Adzuna results normalised; missing salary, postcode, closing date, requirements stay empty | PASS |
| Empty results | Not an error | PASS |
| API unavailable | HTTP 500/503 -> `unavailable` | PASS |
| Invalid credentials | HTTP 401/403 -> `invalid_credentials`; the key values never appear in the error | PASS |
| Rate limiting | HTTP 429 -> `rate_limited` | PASS |
| Missing keys | Adzuna `DISABLED`, not "connected" | PASS |
| Duplicates | Same job from Adzuna and a SEEK import -> one canonical job, two references, nothing deleted; repeat import -> `known` | PASS |
| Duplicates | Near-identical wording with a different title -> merged; different jobs -> not merged | PASS |
| Expired jobs | Past closing date -> `closed` with a reason; future date stays open | PASS |
| Missing description | Empty description handled | PASS |
| Invalid URLs | `javascript:`, wrong site, private address, embedded login, non-URL -> rejected, nothing stored; results with bad links or no title are skipped and counted | PASS |
| Untrusted text | Control characters stripped, 30,000-character cap, injection text stored as data | PASS |
| Destination | Application URL and ATS provider stored separately from the listing URL | PASS |
| Partial failure | One failing source (and one that hangs, and one that raises) never breaks the others; errors recorded per source | PASS |
| Search all sources | Summary lists returned-data / link-only / failed sources | PASS |
| API | Login required; statuses honest; links; import; imports history; enable/disable; background search job | PASS |
| Browser | Sources page labels, link-only results, import and wrong-link refusal | PASS (3 tests) |

## Known limits
- Search-link parameters for SEEK, Indeed and Jora are best-effort and not verified against the live sites (checking would mean visiting them with a bot). Keywords and location are the dependable parts; the page tells you which filters it could not carry.
- Job-alert email ingestion is not built.
- A single unrelated assistant test (`test_a_profile_without_an_email...`) failed once while the machine was under heavy load and passes alone.
