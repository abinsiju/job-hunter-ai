# Job source architecture

## Principle
A source is shown as connected only if it **actually returns listings by itself and may do so**. A search link is a shortcut, labelled as one.

## Source labels
| Label | Meaning | Sources today |
|---|---|---|
| API | Documented API with your own keys | Adzuna (`API_CONNECTED` when keys are set) |
| Authorised feed | Documented public employer job feeds | Employer feeds (Workable boards in the watch-list) |
| Public pages | Employer pages that allow reading (robots.txt), found by web search, read politely | Employer career pages (runs in the scheduled hunt) |
| Browser search | A search link you open in your own browser. Nothing is fetched or automated | SEEK, Indeed, Jora |
| Manual import | You paste a listing you found; it is scored and de-duplicated | SEEK, Indeed, Jora |

## Code (`backend/app/sources/`)
- `base.py`: `Status`, `SearchCriteria`, `NormalisedJob` (the shared schema: missing fields stay empty), `SourceInfo`, `SourceResult`, the `JobSourceConnector` protocol (`health_check`, `search_jobs`, `get_job`, `normalise_job`, `get_source_status`).
- `links.py`: search-URL builders that report which filters made it into the link.
- `connectors.py`: `SeekConnector`, `IndeedConnector`, `JoraConnector` (link + manual import, credential-ready), `AdzunaConnector`, `EmployerFeedConnector`, `EmployerPagesConnector`, `build_registry()`.
- `service.py`: `canonical_upsert` (one canonical job + a reference per source, nothing deleted), `manual_import`, `unified_search` (parallel, per-source timeout, a failing source never breaks the others), `source_board` (status for the Sources page).
- `api/v1/sources.py`: `GET /v1/sources`, `POST /v1/sources/search-links`, `POST /v1/sources/{source}/import`, `GET /v1/sources/imports`, `GET /v1/sources/audit`, `PUT /v1/sources/{source}/enabled`, `GET /v1/sources/listing/{id}`. The unified search runs as the background job `source_search`.

## Data (migration `a1d4c7e2f901`)
`listings` gains the shared-schema columns (`source_method`, `permission_status`, `state`, `postcode`, `salary_currency`, `requirements`, `closing_date`, `application_url`, `ats_provider`). New tables: `source_configs` (enabled flag, non-secret config, last success/error/link), `source_refs` (one row per appearance of a job on a source), `search_runs`, `import_history`, `source_audit`. Existing listings were back-filled with their first reference.

## Duplicates
Checked in this order: source job ID, application URL, normalised employer + title + place, then near-identical wording (full ads only, both directions >= 85%). A repeat adds a `source_refs` row to the existing canonical job; fields are only filled where empty, never overwritten.

## Application destination
`original_url` (the listing), `application_url` and `ats_provider` (where applying happens) are stored separately. The application assistant judges the destination under its own rules (`apply/policy.py`): job boards are always refused, employer sites and ATS pages follow the assistant's rules.

## Security
Credentials live in `.env` (never committed, never sent to the browser, never in errors: tested). Imported text is untrusted: control characters stripped, length capped, stored as data, scored by deterministic rules and a constrained extraction. Nothing from a job ad is executed or treated as an instruction.

## Not built (deliberately)
Scrapers or bots for SEEK, Indeed or Jora; automated Indeed Apply; background crawlers for those sites; job-alert email ingestion. Background discovery uses the app's job manager (in-memory progress, results saved to the database) and the scheduled hunt; Redis is available but not required for this.
