"""What kind of question is this, and may it be answered automatically? Deterministic rules: no model is asked about a person's rights or declarations."""
import re

# category -> (regex over label + options, policy)
#   fill    = answer from verified profile facts if there is one, otherwise leave for the person
#   review  = may be answered from a verified fact, but the person must read it before submitting
#   never   = never answered automatically (the person's own declaration or choice)
RULES = [
    ("LEGAL", r"criminal|convict|police check|background check|declar(e|ation)|i (agree|certify|confirm|consent|acknowledge)|terms (and|&) conditions|privacy (policy|statement)|consent|bankrupt|"
              r"working with children|wwcc|misconduct|dismiss|conflict of interest|true and correct|accurate and complete", "never"),
    ("DEMOGRAPHIC", r"gender|\bsex\b|ethnic|aboriginal|torres strait|indigenous|\brace\b|racial|cultur(al|ally)|religio|disab|veteran|sexual|pronoun|marital|date of birth|\bdob\b|\bage\b|"
                    r"first nations|language spoken|born in|country of birth", "never"),
    ("SALARY", r"salary|remuneration|pay (expectation|rate)|expected (pay|rate)|hourly rate|compensation|rate of pay", "never"),
    ("VISA", r"sponsor|visa|immigration|work permit|subclass|\b(485|482|500)\b", "review"),
    ("WORK_RIGHTS", r"work rights|right to work|entitled to work|authori[sz]ed to work|work in australia|citizen|permanent resident|residency|\bpr\b|clearance", "review"),
    ("MOTIVATION", r"why (do you want|are you interested|would you like|us|this)|motivat|interest(ed)? in (this|the) (role|position|job|company)|tell us (about yourself|why)|cover letter|"
                   r"anything else (you would|you'd) like|additional information|about you", "review"),
    ("BEHAVIOURAL", r"describe a (time|situation)|give an example|tell us about a time|situation.*(task|action|result)|challenge you|how did you handle|star (method|format)", "never"),
    ("EDUCATION", r"degree|university|qualification|education|course|institution|graduat|gpa|study", "fill"),
    ("EMPLOYMENT", r"current employer|previous employer|employer|job title|years of experience|work history|employment|notice period", "fill"),
    ("TECHNICAL", r"skills?|technolog|software|programming|certif|operating system|networking|cloud|database", "fill"),
    ("PERSONAL", r"name|e-?mail|phone|mobile|address|suburb|city|state|postcode|post code|country|licen[cs]e|location", "fill"),
]
COMPILED = [(c, re.compile(p, re.I), pol) for c, p, pol in RULES]


def classify_question(label: str, options: list | None = None) -> tuple[str, str]:
    """(category, policy). `options` is part of the question (an option like 'Yes, I need sponsorship' makes it a visa question)."""
    text = f"{label or ''} {' '.join(o['text'] if isinstance(o, dict) else str(o) for o in (options or [])[:8])}"
    for cat, rx, pol in COMPILED:
        if rx.search(label or "") or (cat in ("VISA", "WORK_RIGHTS", "LEGAL") and rx.search(text)):
            return cat, pol
    return "OTHER", "fill"
