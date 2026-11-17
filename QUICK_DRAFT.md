# Quick draft: resume and cover letter in seconds

**Quick draft** (sidebar) takes a pasted job ad and returns a tailored resume and cover letter (Word and PDF) with an ATS report. Measured on a real ad: **about 1 second** (the full Draft studio takes ~24 s because a language model reads the ad). The ceiling is 10 seconds; past that it stops and says so.

## How it is fast
The only slow step was the language model reading the ad (~22 s). Quick draft reads the ad with rules instead (`app/fastdraft/extract.py`): title, employer, location, employment type, hours, named tools (an IT vocabulary plus your own skills), required versus "desirable", and the gate facts (work rights, fees, unpaid, licence) from the same text rules the full pipeline treats as authoritative. Scoring (local embeddings, ~1 s) and document building (0.1 s) are unchanged. If the local embedding model is not answering, it falls back to keyword matching rather than miss the deadline.

## Same safety as the full draft
Only verified profile facts appear. A citizenship, PR, clearance, fee or duplicate ad makes **no documents**. The fact-checker still reviews every sentence (it caught an earlier version of this feature: see below). The draft appears in your Tracker, and pasting the same ad again refreshes that draft.

## "ATS" and "make me first": what is real
- **What an ATS does:** parses your resume to text, then ranks it by how well it matches the ad, mostly keyword match and similarity (TF-IDF / BM25-style), then a recruiter reads the top.
- **What this does for you, honestly:** (1) layout an ATS can read: single column, standard headings (Summary, Skills, Education, Experience), plain text, 1 page; the PDF is read back with a parser and checked. (2) The ad's own words: if the ad says "Office 365" and your verified skill is "M365" the resume shows "M365 (Office 365)", so a keyword match works without claiming anything new. (3) The target role ("Applying for: ...") is in the header, because ATS match on title. (4) Your most relevant verified skills first.
- **The score:** 70% weighted keyword coverage + 30% TF-IDF similarity, from the ad's named tools, its title words and its repeated themes. It is a **prediction, not a guarantee of rank**: nobody can promise "first".
- **What it will not do:** add a keyword you do not have. Missing skills the ad asks for are listed. If one is true, click it, say your level, and it is stored as verified by your statement; the next draft includes it and the score rises. If it is not true, it stays off.

## Where things are
API `POST /v1/quick-draft`, `POST /v1/quick-draft/confirm-skill`; code `backend/app/fastdraft/`; page `frontend/app/(app)/quick/`; tests `tests/test_fastdraft.py`, `tests_e2e/test_quick_ui.py`.

## Limits
- A rule-based reader is cruder than a language model on unusual ads (odd layouts, skills written in sentences). Draft studio remains for those.
- The ATS score uses common methods, not any specific vendor's closed algorithm.
- If the ad names a skill in words my vocabulary does not know, it is not counted until it is in your profile.
