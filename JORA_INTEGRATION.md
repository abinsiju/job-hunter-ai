# Jora integration

Status in the app: **Search link only + manual import.** Never shown as a data connection.

## 1. What is technically possible
Jora aggregates job ads and publishes search pages. I found no public API, no job-seeker feed and no partner programme. Job ads reach Jora through employers and aggregators, not through a data service offered to individuals.

## 2. What is contractually permitted
Jora's [terms](https://au.jora.com/cms/terms-of-service) prohibit data mining, robots and screen scraping of the site, so nothing automated may read it without written permission. The [advertising terms](https://au.jora.com/cms/advertising-terms-of-use) cover advertisers, not job seekers.

## 3. What requires approval
Written permission from Jora for any automated reading, or a feed arrangement. None exists; none has been requested.

## 4. What is implemented
- `JoraConnector`, registered as `jora`.
- **Search link** (keywords, location, radius, date posted). Employment type, salary and work rights are listed as "not carried" because Jora's link cannot carry them reliably.
- **Manual import** from your own browser (jora.com address required; the job ID is read from the `/job/...` link).
- `JORA_FEED_URL` slot for a feed Jora might one day grant in writing: stored, never used.

## 5. What remains unavailable
Everything automated: fetching listings, freshness checks, de-duplication against Jora beyond what you import.

## 6. How to enable the full integration later
If Jora grants a feed or written permission, set `JORA_FEED_URL`, implement the feed reader in `JoraConnector.search_jobs` (normalise into `NormalisedJob`, keep `source_method="feed"`, `permission_status="written_permission"`), and report `FEED_CONNECTED` only after `health_check` succeeds. Attribution to Jora must be kept on each listing.
