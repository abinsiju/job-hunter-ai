# Job Hunter AI

A private, local-first assistant for an Australian IT job search. It finds jobs, checks whether you are actually eligible, writes a tailored resume and cover letter from facts you have verified, helps fill in application forms, and tracks every application. **It never applies for you: you always press Submit yourself.**

It runs entirely on your own machine: a FastAPI backend, a Next.js dashboard (installable as a PWA), a local language model through [Ollama](https://ollama.com), and Postgres. No paid APIs are required.

> **Author:** Abin Siju ([@abinsiju](https://github.com/abinsiju)). Designed and directed by me; most of the code was written with an AI coding assistant, which I reviewed and tested.

---

## What it does

| Area | What happens |
|---|---|
| **Discovery** | Pulls jobs from sources that allow it: the [Adzuna](https://developer.adzuna.com) API, employers' own public job feeds (Workable, Greenhouse, Lever, SmartRecruiters, Ashby) and employer career pages that permit reading (`robots.txt` is obeyed). A scheduled "hunt" repeats this. |
| **Source check** | For each job, looks for the employer's own version of the ad, compares it with the job-board copy, flags disagreements (work rights, mandatory requirements, location, hours, salary), and detects closed jobs. Each job carries a confidence label: `VERIFIED`, `THIRD_PARTY_FULL` or `SNIPPET_ONLY`. |
| **Matching** | A language model only *extracts* facts from the ad. Scoring is deterministic Python (skills, experience, qualifications, role fit, location and hours). A separate **eligibility gate** returns `PASS` / `REVIEW` / `FAIL`; work rights are decided by text rules, never by the model. A snippet can never `PASS`. |
| **Documents** | Resume and cover letter (Word and PDF) are built **only from profile facts you have marked verified**. No model writes prose. A fact-checker rejects any sentence that claims something your profile does not support. |
| **Quick draft** | Paste an ad and get both documents in about a second (rules read the ad instead of a model), plus an ATS report: keyword coverage and a read-back of the PDF as an ATS parser sees it. Missing keywords are only suggested; one is added to your profile only when you confirm it is true. |
| **Tracker** | applied / interview / offer / rejected states. The same vacancy is hard-blocked from being applied to twice; the same employer is only a warning. "Applied" requires evidence. |
| **Apply assist** | Draft a Gmail *draft* (never sent), or open the employer's page in a visible browser and fill the form from verified facts, stopping before Submit. Two modes: a Playwright-driven window, and a Chrome extension that fills the form in your own browser. See [AUTOFILL_EXTENSION.md](AUTOFILL_EXTENSION.md). |
| **Sources page** | One search across all sources. SEEK, Indeed and Jora appear as *search link + manual import* sources because their terms and APIs do not allow a personal tool to read them. They are never presented as data connections. See [JOB_SOURCE_ARCHITECTURE.md](JOB_SOURCE_ARCHITECTURE.md). |

### Hard rules (enforced in code and tests)
- Never submits an application or sends an email.
- Never invents qualifications, experience or work rights.
- Never solves or bypasses a CAPTCHA, a login or a rate limit; never scrapes SEEK, Indeed or Jora; never automates Indeed Apply.
- Citizenship / PR / clearance requirements and pay-to-apply ads are excluded; internships with no stated pay are flagged.
- Everything listens on `127.0.0.1` only. See [SECURITY.md](SECURITY.md).

---

## Stack

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2 + Alembic, Pydantic, httpx, Playwright, python-docx, ReportLab, pypdf, argon2 + JWT auth, managed with [uv](https://docs.astral.sh/uv/).
- **Data:** PostgreSQL 16 with pgvector, Redis (Docker Compose, bound to localhost).
- **AI (local):** Ollama with `qwen3:4b` for structured extraction and `nomic-embed-text` for similarity.
- **Frontend:** Next.js 16 (App Router), React 19, TypeScript, a hand-written Apple-style design system, a minimal service worker for the PWA shell.
- **Browser extension:** Manifest V3 (`extension/`).
- **Tests:** pytest (API, matching, documents, assistant against simulated employer sites) and Playwright browser tests.

## Architecture

```
 Browser ── Next.js dashboard (3100) ──proxy /api/v1/*──▶ FastAPI (8000) ──▶ PostgreSQL (pgvector)
    │                                                       │   │            Redis
    │ Chrome extension (your own browser)                   │   └──▶ Ollama (local models)
    └──── 30-minute, single-application code ───────────────┘
                                                            ├─ discovery/   Adzuna, employer feeds, career pages
                                                            ├─ verify/      employer-original lookup, open/closed, discrepancies
                                                            ├─ matching/    extraction → deterministic scoring → eligibility gate
                                                            ├─ documents/   verified-facts-only resume and cover letter + fact-checker
                                                            ├─ fastdraft/   rules-based ad reader, ATS scoring
                                                            ├─ sources/     connectors, canonical jobs, manual import
                                                            ├─ apply/       state-machine assistant (Playwright), form rules, Gmail drafts
                                                            └─ tracker/     applications, duplicate protection, evidence
```

More detail: [ARCHITECTURE.md](ARCHITECTURE.md) (the application assistant), [QUICK_DRAFT.md](QUICK_DRAFT.md), the per-source notes ([SEEK](SEEK_INTEGRATION.md), [Indeed](INDEED_INTEGRATION.md), [Jora](JORA_INTEGRATION.md)) and the test reports ([TEST_REPORT.md](TEST_REPORT.md), [JOB_SOURCE_TEST_REPORT.md](JOB_SOURCE_TEST_REPORT.md)).

---

## Run it locally

Built and used on macOS; the core runs anywhere Docker, Python and Node do. Some helper scripts use the macOS Keychain (`set_gmail.sh`) or launchd (`hunt` schedule example).

**Prerequisites:** Docker, [uv](https://docs.astral.sh/uv/), Node 20+, [Ollama](https://ollama.com).

```bash
# 1. configuration (placeholders only; .env is git-ignored)
cp .env.example .env            # then set POSTGRES_PASSWORD to any password you choose

# 2. database and queue
docker compose up -d

# 3. local models (one-time downloads)
ollama pull nomic-embed-text
ollama pull qwen3:4b

# 4. backend
cd backend
uv sync
uv run python ../scripts/set_password.py        # sets the dashboard password and JWT secret in ../.env
uv run alembic upgrade head
uv run playwright install chromium              # only needed for the assisted form fill
cd ..

# 5. your profile (see below), then load it
cd backend && uv run python -m app.db.seed ../data/my_profile.json && cd ..

# 6. run
scripts/start.sh                                # API on :8000, dashboard on :3100
```

Open <http://127.0.0.1:3100> and sign in as `admin`. Stop with `scripts/stop.sh`; check with `scripts/status.sh`.

### Your profile
Everything the documents and forms say comes from rows in the `profile_facts` table. Create `data/my_profile.json` (the `data/` folder is git-ignored, so your details never leave your machine). A minimal example:

```json
[
  {"category": "identity", "key": "name", "value": "Alex Example", "status": "verified", "visibility": "document", "source": "me", "last_confirmed": "2026-01-01"},
  {"category": "contact", "key": "email", "value": {"email": "alex@example.com"}, "status": "verified", "visibility": "document", "source": "me", "last_confirmed": "2026-01-01"},
  {"category": "contact", "key": "phone", "value": "0400 000 000", "status": "verified", "visibility": "document", "source": "me", "last_confirmed": "2026-01-01"},
  {"category": "contact", "key": "location", "value": "Exampleton, VIC", "status": "verified", "visibility": "document", "source": "me", "last_confirmed": "2026-01-01"},
  {"category": "skill", "key": "Active Directory", "value": {"level": "strong"}, "status": "verified", "visibility": "document", "source": "me", "last_confirmed": "2026-01-01"},
  {"category": "skill", "key": "Microsoft 365", "value": {"level": "working"}, "status": "verified", "visibility": "document", "source": "me", "last_confirmed": "2026-01-01"}
]
```

A fact is only used in documents when `status` is `verified` **and** `visibility` is `document`; `forms_only` facts (address, standard form answers) are used for application forms but never printed on a resume. More fact categories (employment, education, projects, referees, work rights, standard answers) are read by `backend/app/documents/factbook.py` and `backend/app/apply/plan.py`.

### Optional
- **Adzuna search:** add free `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` to `.env`.
- **Gmail drafts:** `scripts/set_gmail.sh` stores a Gmail app password in the macOS Keychain. The app only creates drafts; it has no code that sends mail.
- **Scheduled hunt:** `scripts/hunt.sh` (see `scripts/com.jobhunter.hunt.plist.example`).
- **Autofill in your own Chrome:** load the `extension/` folder unpacked; see [AUTOFILL_EXTENSION.md](AUTOFILL_EXTENSION.md).

### Tests
```bash
cd backend
uv run pytest                    # API, matching, documents, assistant, sources, quick draft
uv run pytest tests_e2e          # browser tests (start their own servers on :8101 / :3101)
```
A group of tests was written against the author's private profile (`data/profile_seed.json`) and skips itself when that file is missing; keep your own profile under a different name (as above) and those tests stay skipped. The test database `jobhunter_test` must exist (`docker compose exec postgres createdb -U "$POSTGRES_USER" jobhunter_test`).

---

## Project layout

```
backend/app/        FastAPI app and all domain logic (see the architecture diagram)
backend/migrations/ Alembic migrations
backend/tests/      unit and API tests, simulated employer sites (tests/sim_sites.py)
backend/tests_e2e/  Playwright browser tests (UI, apply assistant, 20 UX-principle checks)
frontend/           Next.js dashboard (app/, components/, lib/)
extension/          Chrome extension for filling forms in your own browser
scripts/            start/stop/status, password setup, hunt, Gmail setup
```

## Honest limits
- It is a single-user tool for one person's job search, not a hosted product.
- The apply assistant and the extension are tested against simulated employer sites and a few real forms, not every employer system. Sites that need an account (Workday and most large employers) stop at the sign-in for you, by design.
- SEEK, Indeed and Jora are link and manual-import sources only, by design.
- ATS scores are predictions from common ranking methods, not a promise of rank.

No licence has been chosen yet; all rights reserved by default.
