"""Standard answers you confirmed once (profile fact form_answers/standard), turned into form-filling rules.
A question is matched by wording; a multiple-choice question is answered only if one of its options clearly says what you said. Otherwise it is left to you."""
import re

SOURCE = "form_answers/standard"
YES = [r"^yes\b"]
HEALTH_BAD = r"\bnot\b|\bun(vaccinated)?\b|partial|\bno\b|never|refuse|exempt"


def _weeks(v: str) -> list[str]:
    m = re.search(r"(\d+)", v or "")
    return [rf"\b{m.group(1)}\s*weeks?\b|\b{['zero','one','two','three','four'][min(int(m.group(1)), 4)]}\s*weeks?\b"] if m else []


def build_qa(std: dict, work_visa: str | None = None) -> list[dict]:
    """Rules from the confirmed answers. `review` marks answers the person must read before submitting."""
    qa: list[dict] = []

    def add(id_, pattern, choose, text, number=None, review=False, exclude=None):
        qa.append({"id": id_, "pattern": pattern, "choose": choose, "text": text, "number": number, "review": review, "exclude": exclude, "source": SOURCE})

    if std.get("start"):
        s = str(std["start"])
        add("start", r"how soon (can|could) you start|when (can|could) you start|earliest (start|you can)|available to start|start date|notice to start|availability to start",
            [r"right away|immediate|asap|straight away|\bnow\b"] if re.search(r"right|immed|now|asap", s, re.I) else _weeks(s), s)
    if std.get("notice") is not None:
        n = str(std["notice"])
        add("notice", r"how much notice|notice (period|are you|required|you (must|need|are required))|notice to (give|your)",
            [r"^(none|no notice|n/?a|not applicable|immediate|0\b|nil)"] if re.match(r"none|no|nil|0|n/?a", n, re.I) else _weeks(n),
            "None (not currently employed)" if re.match(r"none|no|nil|0|n/?a", n, re.I) else n, 0 if re.match(r"none|no|nil|0|n/?a", n, re.I) else None)
    if std.get("covid_vaccination"):
        v = str(std["covid_vaccination"])
        add("covid", r"covid|vaccinat", [r"fully|double|two doses|boosted|up to date|vaccinated"] if re.match(r"fully", v, re.I) else [re.escape(v.lower())], v, review=True, exclude=HEALTH_BAD if re.match(r"fully", v, re.I) else None)
    if std.get("english"):
        v = str(std["english"])
        add("english", r"english( language)? (skills?|proficiency|level|ability|fluency)|rate your english|your english",
            [r"fluent", r"excellent", r"very good", r"advanced", r"proficient"] if re.match(r"fluent|excellent", v, re.I) else [re.escape(v.lower())], v)
    if std.get("police_check"):
        v = str(std["police_check"])
        add("police_check", r"police (check|certificate|clearance)|national police|criminal history check", YES if re.match(r"yes", v, re.I) else [r"^no\b"], v, review=True)
    if std.get("public_holidays"):
        v = str(std["public_holidays"])
        add("public_holidays", r"public holidays?", YES if re.match(r"yes", v, re.I) else [r"^no\b"], v)
    if std.get("customer_service_experience"):
        v = str(std["customer_service_experience"])
        add("customer_service", r"customer service experience|experience (in|with) customer service|customer[- ]facing experience", YES if re.match(r"yes", v, re.I) else [r"^no\b"], v)
    if std.get("technical_support_experience"):
        v = str(std["technical_support_experience"])
        add("technical_support", r"(technical|it|tech|desktop|help ?desk|service desk) support experience|experience (in|with) (technical |it |desktop )?support|help ?desk experience",
            YES if re.match(r"yes", v, re.I) else [r"^no\b"], v)
    lo, hi = std.get("hourly_rate_min"), std.get("hourly_rate_max")
    if lo:
        txt = f"${lo:g} - ${hi:g} per hour" if hi else f"${lo:g} per hour"
        add("hourly_rate", r"hourly rate|rate (are you|you are|do you) (seeking|expect|looking)|expected (hourly )?(pay|rate)|base (hourly )?rate|pay rate|rate of pay (you|are)|hourly (pay|wage)|desired (hourly|rate)", [], txt, number=lo, review=True)
    label = std.get("right_to_work_label") or ("Student visa" if work_visa and "student" in work_visa.lower() else None)
    if label:
        add("right_to_work", r"right to work|work(ing)? rights|entitled to work|work entitlement|visa (status|type)|best describes your (right|work)",
            [r"^student( visa)?\b", r"\bstudent\b"], str(label), review=True, exclude=r"citizen|permanent|partial|holiday")
    if std.get("it_support_years") is not None:
        y = std["it_support_years"]
        add("it_years", r"(years?|yrs?)\W.{0,40}experience.{0,80}(it |technical|support|help ?desk|service (desk|cent(re|er))|analyst)|experience.{0,40}(it support|help ?desk|service (desk|cent(re|er))).{0,40}(years?|yrs?)",
            [], f"{y:g}", number=y, review=True)
    return qa


def pick_option(options: list, entry: dict):
    """The option that says what the entry says, or None (never a guess). `options` are strings (select) or {'text','ref'} dicts (radio)."""
    texts = [(o["text"] if isinstance(o, dict) else str(o)).strip() for o in options]
    ex = re.compile(entry["exclude"], re.I) if entry.get("exclude") else None
    for rx in entry.get("choose") or []:
        for o, t in zip(options, texts):
            if re.search(rx, t, re.I) and not (ex and ex.search(t)):
                return o
    return None


# ── understanding a question worded differently from the usual: embeddings match the QUESTION to a confirmed answer; the answer itself never comes from a model ──
EXAMPLES = {
    "start": "How soon can you start working?", "notice": "How much notice do you need to give your current employer?",
    "covid": "What is your Covid-19 vaccination status?", "english": "How would you rate your English language skills?",
    "police_check": "Do you hold a current national police check for employment?", "public_holidays": "Are you available to work on public holidays?",
    "customer_service": "Do you have customer service experience?", "technical_support": "Do you have technical support experience?",
    "hourly_rate": "What hourly rate are you expecting?", "right_to_work": "What is your right to work in Australia?",
    "it_years": "How many years of IT support experience do you have?",
}
THRESHOLD, MARGIN = 0.80, 0.03


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return num / den if den else 0.0


class SemanticMatcher:
    """`embed(texts) -> vectors`. Matches a question to the closest confirmed answer, only when clearly closest."""

    def __init__(self, embed, qa: list[dict]):
        self.embed, self.qa, self._vecs = embed, qa, None

    def match(self, label: str) -> dict | None:
        if not self.embed or not label.strip():
            return None
        try:
            if self._vecs is None:
                ids = [e for e in self.qa if e["id"] in EXAMPLES]
                self._vecs = list(zip(ids, self.embed([EXAMPLES[e["id"]] for e in ids])))
            v = self.embed([label[:200]])[0]
        except Exception:  # noqa: BLE001  (Ollama down: fall back to the exact rules only)
            return None
        scored = sorted(((_cos(v, vec), e) for e, vec in self._vecs), key=lambda x: -x[0])
        if scored and scored[0][0] >= THRESHOLD and (len(scored) == 1 or scored[0][0] - scored[1][0] >= MARGIN):
            return scored[0][1]
        return None
