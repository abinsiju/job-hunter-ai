# Architecture: the application assistant (`backend/app/apply/`)

## What it does
You press **Apply** on a job. A visible browser window opens on the employer's page. The assistant works out *where it is in the application journey*, takes one verified step, looks again, and repeats: find the Apply control, follow it (same page, modal, new tab or another site), wait while you sign in or solve a CAPTCHA, fill each step from your verified profile, attach your documents, and stop before the final Submit. It then notices the confirmation page and records it as evidence.

## Components
| Component | File | Responsibility |
|---|---|---|
| Browser session manager | `workflow.py` (`Run`) | Browser lifecycle, tabs and popups, which page is "current", relationship between the job page and the application page, tracing, deadlines |
| Page intelligence | `page_intel.py` | `SNAPSHOT_JS` (what is on the page) and pure functions: `classify` (which stage), `pick_apply_action`, `pick_method`, `find_nav`. No clicking, no browser needed to test |
| Navigation policy | `policy.py` | Which addresses may be opened: employer sites and any ATS yes; job boards, non-https (tests excepted) and private addresses never |
| Form detection | `engine.py` (`SCAN_JS`, `CUSTOM_JS`) | Fields incl. shadow DOM, labels from `aria-*`/`label`/`fieldset`, radio groups, selects, files, autocomplete, custom dropdowns |
| Question classifier | `questions.py` | PERSONAL / EMPLOYMENT / EDUCATION / TECHNICAL / WORK_RIGHTS / VISA / LEGAL / DEMOGRAPHIC / SALARY / MOTIVATION / BEHAVIOURAL, each with a policy: `fill`, `review` or `never` |
| Candidate mapping | `plan.py`, `engine.decide()` | Verified profile facts only -> field values; unknown stays blank |
| Workflow engine | `workflow.py` (`journey`, `step_form`, `fill_step`) | State machine driver, step navigation, conditional re-scan, recovery, approval hand-over |
| Document upload manager | `uploads.py` | Validate the file before, judge the page after: a chosen file is not an upload |
| Review manager | `workflow.py` (`hand_over`) | Summary of filled / needs-you / read-before-submit / uploads, screenshot, banner |
| Audit and logging | `audit.py`, `states.py` | Append-only `assist_audit.jsonl` (labels, states, evidence: never your values), Playwright `trace.zip` (mode 600), screenshots |

## State machine (`states.py`)
28 logical states, grouped:
- **Entry:** JOB_PAGE_OPENED, PAGE_LOADING, JOB_DETAILS_VISIBLE, APPLICATION_ACTION_REQUIRED
- **Gates:** APPLICATION_METHOD_SELECTION, APPLICATION_MODAL_OPEN, EXTERNAL_APPLICATION_REDIRECT, NEW_TAB_OPENED, LOGIN_REQUIRED, ACCOUNT_CREATION_REQUIRED, EMAIL_VERIFICATION_REQUIRED, APPLICATION_INTRODUCTION
- **Form:** APPLICATION_FORM_LOADING, APPLICATION_FORM_DETECTED, APPLICATION_STEP_ACTIVE, CONDITIONAL_FIELDS_PENDING, DOCUMENT_UPLOAD_REQUIRED, ELIGIBILITY_QUESTIONS, SCREENING_QUESTIONS
- **End:** REVIEW_REQUIRED, APPLICATION_READY_FOR_APPROVAL, SUBMISSION_CONFIRMED, APPLICATION_COMPLETED
- **Stops:** APPLICATION_ERROR, CAPTCHA_OR_SECURITY_CHALLENGE, SESSION_EXPIRED, UNKNOWN_PAGE_STATE, USER_INTERVENTION_REQUIRED

Rules: `Machine.move(to, evidence)` refuses an empty evidence list and illegal jumps (e.g. COMPLETED only after SUBMISSION_CONFIRMED). **A click is never evidence.** After a click the engine watches up to 8 s for something *seen*: a new tab, a different address, or a changed page. If nothing changes it does not press again (max two presses per control) and hands over.

`classify()` order puts things that need *you* first: CAPTCHA -> expired session -> "already applied" -> confirmation -> loading -> login/account/verification -> dialogs -> form -> frame -> method choice -> introduction -> Apply control -> job details -> unknown.

## What it may and may not click
- **Opener:** an Apply control chosen by `pick_apply_action` (clearest by position: in the main job area, not in a header/nav, not one of a list of other jobs, not a submit button of a form). Equally good rivals -> it asks you, never guesses.
- **Method:** only "manual / company website / without account / with my resume" options; never LinkedIn, Indeed, SEEK, Google, Apple, Facebook or "sign in".
- **Next:** only on a page with a visible step indicator and never on the last step. A "Continue" with no indicator could be the final submit, so it is left to you.
- **Never:** Submit, Finish, Send application, sign-in, create account, CAPTCHA, consent/declaration boxes. A submit-event guard blocks submission while the assistant works and is removed afterwards so *you* can submit.

## Answers
Deterministic rules, no model. PERSONAL/EMPLOYMENT/EDUCATION/TECHNICAL from verified facts. Work rights are answered truthfully ("No" to unrestricted work rights) and listed under "read before you submit". Visa/sponsorship is never inferred. Legal declarations, criminal history, salary, demographic and behavioural questions are left to you with the reason shown. Motivation questions get your verified cover letter, flagged for review. Existing values on the page are never overwritten.

## Conditional and multi-step forms
`fill_step` scans, fills what is new, and scans again (max 8 rounds) until nothing new appears; each control has a signature (step, label, type, occurrence) so repeated sections ("Employer (#2)") are tracked separately. Required blanks or failed uploads stop auto-advance: the step is handed to you, and the engine carries on when you press Next yourself.

## Recovery
Page changed during fill -> pause, re-look, continue. Session expired / login / CAPTCHA / email verification -> wait (default 10 min) then resume; nothing already typed is overwritten. Browser closed -> clean exit. Unrecognised page -> nothing is clicked. Stalled page -> hand over. Never retries anything that could submit twice.

## Security
Page text is untrusted data: nothing from a page is sent to a model, run, or obeyed; injection-like text is only noted. Navigation policy applies to every page, tab and frame. See SECURITY.md.
