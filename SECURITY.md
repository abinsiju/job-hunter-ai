# Security

## App password
- The login password lives in the macOS Keychain, service `job-hunter-ai`.
- Retrieve it (to clipboard, not the terminal): `security find-generic-password -s job-hunter-ai -w | pbcopy`
- `.env` holds only the argon2 hash (`ADMIN_PASSWORD_HASH`) and `JWT_SECRET`. `.env` is mode 600 and gitignored.
- Rotate: change the Keychain item, then run `cd backend && uv run python ../scripts/set_password.py --from-keychain`. Or run the script without the flag to type a new one.

## Exposure
- Postgres, Redis and Ollama bind to 127.0.0.1 only.
- Every API route except `/health` and `/auth/login` requires a JWT. OpenAPI/docs are disabled.
- Login locks out after 5 failures in 5 minutes (in-memory, per client).
- Never put the password or tokens in logs, commits or chat.

## Gmail app password (apply assist)
- Used only to create *drafts* over IMAP (`imap.gmail.com:993`). The code has no way to send mail. Stored in the macOS Keychain, service `job-hunter-gmail`; saved by `scripts/set_gmail.sh`, which reads it interactively so it never appears in the shell history or process list.
- Revoke it any time at https://myaccount.google.com/apppasswords. A Gmail app password can read the whole mailbox, so treat it like the login password.

## Assisted form fill and source checks
- Form fill opens only Workable, Greenhouse, Lever and SmartRecruiters pages, in a visible window, and stops before Submit. It never touches CAPTCHAs or logins and never opens SEEK, LinkedIn or Indeed. The plan file it reads holds your contact details and is mode 600.
- Source checks read only public employer feeds and pages that `robots.txt` allows, with an honest user agent and a one-request-per-second limit. They never log in, never solve CAPTCHAs, never retry a site that said no.

## Assistant navigation (changed 2026-11-11)
- The assistant may follow your Apply path to employer sites and ATS pages. It refuses job boards, non-https pages (tests excepted) and private addresses on **every** page, tab and frame, and stops if a page moves somewhere not allowed.
- It never signs in, creates an account, solves a CAPTCHA, ticks a declaration or presses Submit. Page text is untrusted: nothing on a page is sent to a model or treated as an instruction.
- `assist_audit.jsonl` records labels, states and evidence, never your values. `trace.zip` (Playwright) does contain page contents including what was typed: it is mode 600, stays on this Mac, and can be deleted with the application's folder.
