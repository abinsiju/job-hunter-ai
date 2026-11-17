# Autofill in your own browser

For forms that show a security check (Cloudflare and similar) in the assistant's window, or whenever you would rather work in your normal Chrome.

## What it is
A small Chrome extension (`extension/`). You open the employer's application page yourself, press **Fill**, and it fills the form from your verified profile using exactly the same rules as the assistant (`app/apply/resolver.py`), then attaches your resume and cover letter. **You** solve any security check, review everything and press Submit.

## What it never does
Click a button or link (the only click it makes is on an answer radio), submit, solve or touch a security check, sign in, create an account, or run on a page you did not choose: there are no automatic content scripts, it runs only when you press Fill. It cannot work on SEEK, Indeed, Jora or any job board (Job Hunter refuses those addresses) or on a private network address. If the form itself says citizenship, PR or clearance is required, it stops without filling anything.

## Set up (once)
1. In Chrome open `chrome://extensions`, turn on **Developer mode**, press **Load unpacked**, choose the `extension` folder (the Tracker card shows its full path).

## Each time
1. In Job Hunter open the application in the Tracker and press **Make a 30-minute autofill code** (needs the two documents; works for drafts and approved applications).
2. Open the employer's application page in Chrome.
3. Click the **Job Hunter Autofill** icon, paste the code, press **Fill this page**.
4. Orange fields are yours to answer. Answers marked "read before you submit" (right to work, pay, police check, Covid, years of experience) are listed in the popup summary.

## Security design
- The code is a signed token that lasts 30 minutes and is bound to **one application**. It is accepted only by `/autofill/plan` and `/autofill/file/{resume|cover_letter}`. It is **not** a login: every other route refuses it, and a login token is refused by the autofill routes (tested).
- The page never sees the code: the extension's background worker talks to `http://127.0.0.1:<port>` (this Mac only) on the page's behalf.
- Only the form's field descriptions and visible page text go to Job Hunter on this Mac. Nothing leaves your machine. Page text is untrusted data and is never treated as an instruction.
- Documents are served only for the application the code belongs to, only these two, only as PDFs.
- `extension/scan.js` is generated from the assistant's own scanner (`scripts/build_extension.py`); a test fails if it drifts.

## Limits
- It fills forms in the top page; a form inside a cross-origin iframe may need Chrome to grant that frame access.
- Custom (non-native) dropdowns and widgets that do not accept programmatic input are reported as "left for you".
- Loaded unpacked, Chrome shows a "developer mode extension" notice. Publishing it to the Chrome Web Store is not needed and not planned.
