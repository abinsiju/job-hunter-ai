# Test report: application assistant (2026-11-11)

Run: `cd backend && uv run pytest tests/test_apply_journey.py` (33 tests, ~2 min). Everything runs against **simulated employer sites** (`tests/sim_sites.py`) served locally. No real website was visited and nothing real was submitted.

## Results
| # | Scenario | Result | What the test proves |
|---|---|---|---|
| 1 | Job page with only an Apply button | PASS | First state is "Apply control found" with evidence; form found after the click; nothing submitted |
| 2 | Apply opens a modal | PASS | MODAL state reached; fields inside the dialog filled |
| 3 | Apply opens a new tab | PASS | NEW_TAB_OPENED; audit links the new tab to the job page |
| 4 | Redirect to an external ATS on another host | PASS | EXTERNAL_APPLICATION_REDIRECT; form filled there |
| 5 | Login required | PASS | Waits, never touches the login, stops with `login_required` |
| 5b | Person signs in during the wait | PASS | Run resumes and fills |
| 6 | Form appears after a delay (skeleton loader) | PASS | Waits for real readiness |
| 7 | Multi-step wizard | PASS | Steps 1-3 each filled, resume/cover attached on step 2, final Submit untouched |
| 8 | Answer reveals more fields | PASS | CONDITIONAL_FIELDS_PENDING, new fields mapped |
| 9 | Required answer missing | PASS | Reported, step not advanced, field highlighted |
| 10 | Upload rejected by the page | PASS | Not reported as success; listed as "upload not confirmed" |
| 11 | CAPTCHA challenge | PASS | Automation stops, CAPTCHA never clicked |
| 12 | Session expires | PASS | Waits, then resumes after sign-in |
| 13 | Already applied | PASS | Stops, nothing filled or sent |
| 14 | Final Submit visible | PASS | APPLICATION_READY_FOR_APPROVAL; Submit never pressed |
| 15 | Submission succeeds | PASS | Confirmation detected, saved as `confirmation.png`, state COMPLETED |
| 16 | Prompt injection on the page | PASS | Ignored and noted; nothing sent to the attacker address |
| 17 | Many Apply buttons | PASS | The job's own button chosen; list/header buttons untouched |
| 17b | Two equally likely Apply buttons | PASS | Asks the person, clicks neither |
| 18 | Form inside an iframe | PASS | Frame found and filled |
| 19 | Autocomplete dropdown | PASS | Suggestion chosen and verified |
| 20 | Page changes while filling | PASS | Pauses, re-looks, finishes on the new page |
| 21 | Custom (non-native) dropdown | PASS | Reported as something the person must choose |
| 22 | Repeated sections | PASS | "Employer (#2)" tracked separately; existing answers kept |
| 23-24 | Conditional dropdown; visa / legal / motivation rules | PASS | No guessing to reveal fields; visa never inferred; criminal question left; motivation uses the verified cover letter |
| - | Unit tests: state machine, classification, Apply/method/Next choice, upload judgement | PASS | Evidence required, illegal jumps refused, ambiguity not guessed |
| - | Audit | PASS | Every state change has evidence; audit never contains your email; trace saved |

Also passing after the change: the older local ATS replicas (Workable, Greenhouse, Lever, SmartRecruiters, shadow DOM, CAPTCHA, login, trap pages) and the form-gate tests; the API, tracker, Gmail and verification suites (see PROJECT_STATUS.md for totals).

## Bugs the tests found (all fixed)
1. A hidden dialog's upload fields were counted as a visible form, so a modal was never recognised.
2. Field markers from the previous step were not cleared, so step 2's upload pointed at step 1's hidden input.
3. The modal state was routed to the Apply-click handler, which clicked Apply a second time.
4. Required-field detection compared a step number with a step object, so a wizard step with a missing required answer was advanced.
5. `check_url` let `https://127.0.0.1` through because the refusal was raised inside a `try` that also caught it.

## Not measured / not tested
- **No real employer site was tested.** Real sites differ; expect surprises.
- Time-to-detect, field-mapping accuracy, upload success and human-intervention rates are **not measured on real sites**. The audit log (`assist_audit.jsonl`) records timestamps and outcomes to measure them once real runs exist.
- Sites that require an account (Workday and most large employers) stop at the sign-in by design.
