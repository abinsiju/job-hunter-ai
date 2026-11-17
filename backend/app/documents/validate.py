"""Post-generation checks. Anything the writer produces must pass before a document exists."""
import re

from sqlalchemy.orm import Session

from app.documents.facts import check_document_text
from app.matching.schema import JobExtract
from app.matching.skills import ALIASES, norm

class GenerationRejected(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("Generated documents failed the fact checks: " + "; ".join(problems[:6]))
        self.problems = problems


# Work rights are internal-only: they must never appear in a resume or cover letter.
WORK_RIGHTS_RE = re.compile(r"\b(visa|work rights?|citizen\w*|permanent residen\w*|sponsor\w*|485|right to work)\b", re.I)
ALLOWED_WORDS = {"i", "dear", "hiring", "manager", "kind", "regards", "sincerely", "thank", "thanks", "australia",
                 "australian", "victoria", "vic", "english"}


def canon(text: str) -> str:
    """Lower-case and rewrite known aliases to one form, so 'M365' and 'Microsoft 365' compare equal."""
    t = text.lower()
    for alias, target in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", target, t)
    return t


def skill_supported(skill: str, facts_text: str) -> bool:
    k = norm(skill)
    return bool(k) and bool(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", canon(facts_text)))


def _words(text: str) -> set[str]:
    return {w.strip(".,;:'\"()-") for w in re.findall(r"[a-z0-9+#&./'-]+", text.lower())}


def _non_initial_capitalised(text: str) -> list[str]:
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        toks = re.findall(r"[A-Za-z0-9+#&./'-]+", sentence)
        out += [t for t in toks[1:] if t[0].isupper()]
    return out


def validate_generated(session: Session, text: str, facts_text: str, ad_text: str, job: JobExtract) -> list[str]:
    """Return a list of rule violations (empty = acceptable)."""
    problems = [f"forbidden claim: {h}" for h in check_document_text(session, text)]
    problems += [f"work rights/visa must not be mentioned: '{m.group(0)}'" for m in WORK_RIGHTS_RE.finditer(text)]

    corpus = facts_text + "\n" + ad_text + "\n" + " ".join(filter(None, [job.title, job.employer, job.location]))
    for n in dict.fromkeys(re.findall(r"\d+(?:[.,]\d+)*", text)):
        if n not in corpus:
            problems.append(f"number not in facts or job ad: {n}")
    known = _words(corpus) | ALLOWED_WORDS
    for tok in dict.fromkeys(_non_initial_capitalised(text)):
        if tok.strip(".,;:'\"()-").lower() not in known and tok.lower() not in known:
            problems.append(f"name/term not in facts or job ad: {tok}")

    low = canon(text)
    for s in dict.fromkeys(job.required_skills + job.nice_to_have_skills):
        k = norm(s)
        if k and re.search(rf"(?<!\w){re.escape(k)}(?!\w)", low) and not skill_supported(s, facts_text):
            problems.append(f"claims skill the profile does not verify: {s}")
    return problems
