"""How well will an applicant tracking system (ATS) match this resume to this ad? Honest, explainable arithmetic:
  coverage = weight of the ad's top keywords that appear in the resume (aliases count: M365 = Microsoft 365)   -> 80% of the score
  readable = the PDF reads back as text, in order, under standard headings, on 1-2 pages                          -> 20% of the score
(TF-IDF similarity is still reported for information, but it is not in the score: comparing a short resume with a long ad keeps it tiny whatever you do.)
This PREDICTS how common keyword-ranking ATS will treat you. It is not a guarantee of rank: no tool can promise that. A keyword you do not truly have is only
reported, never added; reaching a high score honestly means confirming the skills you really do have."""
import io
import math
import re
from collections import Counter

from app.fastdraft.vocab import GENERIC, IT_TERMS, STOP
from app.matching.skills import ALIASES

TOKEN = re.compile(r"[a-z0-9][a-z0-9+#./]*")
STANDARD_HEADINGS = ["summary", "skills", "education", "experience"]


def canon(term: str) -> str:
    t = re.sub(r"[^a-z0-9+#./ ]", " ", term.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return ALIASES.get(t, t)


def tokens(text: str) -> list[str]:
    return [t.strip(".") for t in TOKEN.findall(text.lower()) if t.strip(".")]


def ad_keywords(ad_text: str, job_title: str, vocab_terms: list[str], corpus: list[str] | None = None, top: int = 25, exclude: set[str] | None = None) -> list[tuple[str, float]]:
    """(keyword, weight): named tools first, the job title's words next, then the ad's most distinctive words (TF-IDF over your stored ads)."""
    out: dict[str, float] = {}
    for t in vocab_terms:
        out[canon(t)] = 3.0
    for w in tokens(job_title):
        if w not in STOP and len(w) > 2:
            out.setdefault(w, 2.0)
    docs = [set(tokens(d)) for d in (corpus or [])[:300]] + [set(tokens(ad_text))]
    tf = Counter(w for w in tokens(ad_text) if w not in STOP and w not in GENERIC and w not in (exclude or set()) and len(w) > 3 and not w.isdigit())
    tf = Counter({w: c for w, c in tf.items() if c >= 2})  # a word the ad repeats is a theme; a word it says once is usually just prose
    n = len(docs)
    scored = sorted(((c * math.log((n + 1) / (1 + sum(w in d for d in docs))) + c * 0.2, w) for w, c in tf.items()), reverse=True)
    for sc, w in scored[:6]:
        out.setdefault(w, min(1.0, 0.3 + sc / 8))
    return sorted(out.items(), key=lambda kv: -kv[1])[:top]


def has(text_canon: str, kw: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text_canon))


def canon_text(text: str) -> str:
    t = re.sub(r"[^a-z0-9+#./ ]", " ", text.lower())
    t = re.sub(r"\s+", " ", t)
    for alias, target in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):  # "m365" and "office 365" both read as "microsoft 365"
        t = re.sub(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", target, t)
    return t


def tfidf_cosine(a: str, b: str, corpus: list[str]) -> float:
    docs = [set(tokens(d)) for d in corpus[:300]] + [set(tokens(a)), set(tokens(b))]
    n = len(docs)
    idf = lambda w: math.log((n + 1) / (1 + sum(w in d for d in docs))) + 1
    va, vb = Counter(w for w in tokens(a) if w not in STOP), Counter(w for w in tokens(b) if w not in STOP)
    wa, wb = {w: c * idf(w) for w, c in va.items()}, {w: c * idf(w) for w, c in vb.items()}
    dot = sum(wa[w] * wb.get(w, 0) for w in wa)
    na, nb = math.sqrt(sum(x * x for x in wa.values())), math.sqrt(sum(x * x for x in wb.values()))
    return dot / (na * nb) if na and nb else 0.0


def resume_text(resume) -> str:
    parts = [resume.name, *resume.contact]
    for s in resume.sections:
        parts.append(s.title)
        if s.text:
            parts.append(s.text)
        for e in s.entries:
            parts += [e.heading, e.sub or "", *e.bullets]
    return "\n".join(p for p in parts if p)


def format_check(pdf_bytes: bytes) -> dict:
    """Read the PDF back the way an ATS parser does: is the text there, in order, under standard headings, on at most 2 pages?"""
    issues: list[str] = []
    try:
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join((p.extract_text() or "") for p in r.pages)
        pages = len(r.pages)
    except Exception as e:  # noqa: BLE001
        return {"parsable": False, "pages": 0, "headings_found": [], "issues": [f"could not read the PDF text ({type(e).__name__})"]}
    low = text.lower()
    found = [h for h in STANDARD_HEADINGS if re.search(rf"^\s*{h}\b", low, re.M)]
    order = [low.find(h) for h in found]
    if len(text.strip()) < 200:
        issues.append("very little text could be read from the PDF")
    if "(cid:" in text or "�" in text:
        issues.append("some characters were not readable")
    if pages > 2:
        issues.append(f"{pages} pages (ATS and recruiters prefer 1 to 2)")
    if len(found) < 3:
        issues.append("standard section headings were not all found")
    if order != sorted(order):
        issues.append("sections are not in a normal reading order")
    return {"parsable": not issues or issues == [f"{pages} pages (ATS and recruiters prefer 1 to 2)"], "pages": pages, "headings_found": found, "issues": issues}


def ats_report(job, ad_text: str, resume, verified_skills: list[str], pdf_bytes: bytes | None, corpus: list[str], extra_terms: list[str] | None = None) -> dict:
    terms = job.required_skills + job.nice_to_have_skills
    kws = ad_keywords(ad_text, job.title, terms + (extra_terms or []), corpus, exclude=set(tokens(job.employer or "")))  # the employer's own name is not a skill
    rt = resume_text(resume)
    rc = canon_text(rt)
    matched, missing = [], []
    for kw, w in kws:
        (matched if has(rc, canon(kw)) else missing).append((kw, w))
    total = sum(w for _, w in kws) or 1.0
    coverage = sum(w for _, w in matched) / total
    similarity = tfidf_cosine(ad_text, rt, corpus)
    fmt = format_check(pdf_bytes) if pdf_bytes else None
    readable = 1.0 if (fmt and fmt["parsable"] and len(fmt["headings_found"]) >= 3) else 0.5 if fmt else 0.0
    score = round(100 * (0.8 * coverage + 0.2 * readable))
    skill_missing = [kw for kw, _ in missing if canon(kw) in {canon(t) for t in IT_TERMS} or kw in {canon(t) for t in terms}]
    verified = {canon(s) for s in verified_skills}
    not_verified = [k for k in skill_missing if canon(k) not in verified]  # asked for, and NOT among your verified skills
    weight = dict(missing)
    # the honest path to the goal: the missing skills worth the most, and the score you would have IF you truly have them
    goal = 80
    path, cov, cur = [], sum(w for _, w in matched), score
    for k in sorted(not_verified, key=lambda k: -weight.get(k, 0)):
        if cur >= goal:
            break
        cov += weight.get(k, 0)
        cur = round(100 * (0.8 * min(1.0, cov / total) + 0.2 * readable))
        path.append({"keyword": k, "score_if_added": cur})
    potential = round(100 * (0.8 * min(1.0, (sum(w for _, w in matched) + sum(weight.get(k, 0) for k in not_verified)) / total) + 0.2 * readable))
    return {"score": score, "coverage_pct": round(100 * coverage), "similarity_pct": round(100 * similarity), "keywords_total": len(kws),
            "matched": [k for k, _ in matched], "missing": [k for k, _ in missing], "missing_skills": not_verified, "format": fmt,
            "target": {"goal": goal, "reached": score >= goal, "potential_score": potential, "reachable_with_true_skills": potential >= goal, "add_if_true": path},
            "method": "80% weighted keyword coverage + 20% readable-by-ATS; a prediction, not a guarantee of rank",
            "note": "Keywords you do not have are never added to your resume. Add one only if it is true, by confirming it here."}
