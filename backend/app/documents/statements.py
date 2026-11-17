"""First-person evidence sentences built ONLY from verified facts. No model writes any of this text.

Each sentence is a fixed frame around the user's own phrases (kept verbatim), so nothing can be invented.
"""
from dataclasses import dataclass

from app.documents.factbook import FactBook

# Plain words that are safe to lower-case when a duty phrase moves into the middle of a sentence. Anything else
# (Brevo, M365, UX ...) keeps its capitals; an unknown word just stays capitalised, which is never wrong.
LOWERABLE = {"staff", "office", "platform", "access", "data", "assisted", "built", "wrote", "produced", "designed",
             "delivered", "led", "ran", "made", "chose"}
IRREGULAR_PAST = {"built", "wrote", "led", "ran", "made", "chose"}
SKIP_IN_LETTER = ("delivered via",)  # reads badly as "I delivered via ...": resume only


def lower_first(s: str) -> str:
    return s[0].lower() + s[1:] if s.split(" ", 1)[0].lower() in LOWERABLE else s


def is_verb_phrase(s: str) -> bool:
    w = s.split(" ", 1)[0].lower()
    return w in IRREGULAR_PAST or (w.endswith("ed") and w.isalpha())


@dataclass
class Group:
    id: str
    kind: str  # experience | project
    intro: str
    sentences: list[tuple[str, str]]  # (VERB|NOUN, phrase): "I <verb phrase>." or "my responsibilities included <noun phrase>."


def build_groups(book: FactBook) -> list[Group]:
    groups: list[Group] = []
    for key, e in book.in_category("employment").items():
        duties = e.get("duties_beyond_core_role") or e.get("duties") or []
        if e.get("duties_beyond_core_role"):
            intro = f"At {e['employer']}, alongside my {e['title']} duties,"
        else:
            intro = f"As a {e['title']} at {e['employer']},"
        sents = [("VERB" if is_verb_phrase(d) else "NOUN", lower_first(d)) for d in duties]
        groups.append(Group(f"employment:{key}", "experience", intro, sents))
    t = book.get("project", "capstone_network_console")
    if t:
        intro = (f"In my Example University capstone project ({t['client']} as the client, a university project and not employment), "
                 f"as UX Designer in a team of {t['team_size']},")
        sents = [("VERB", lower_first(c)) for c in t["contributions"]
                 if is_verb_phrase(c) and not c.lower().startswith(SKIP_IN_LETTER)]
        groups.append(Group("project:exampletel", "project", intro, sents))
    v = book.get("project", "venuehub")
    if v:
        intro = f"In VenueHub, a full-stack venue booking app built by a {v['document_wording']} using {', '.join(v['stack'])},"
        sents = [("VERB", lower_first(c)) for c in v["contributions"] if is_verb_phrase(c)]
        groups.append(Group("project:venuehub", "project", intro, sents))
    return groups


def render_group(g: Group, picked: list[tuple[str, str]]) -> str:
    """Noun-phrase duties merge into one 'responsibilities included' sentence; verb phrases follow as 'I ...'."""
    nouns = [t for m, t in picked if m == "NOUN"]
    verbs = [t for m, t in picked if m == "VERB"]
    parts = []
    if nouns:
        parts.append(f"{g.intro} my responsibilities included {'; '.join(nouns)}.")
    for n, v in enumerate(verbs):
        parts.append(f"I also {v}." if (parts or n) else f"{g.intro} I {v}.")
    return " ".join(parts)


def summary_text(book: FactBook) -> str:
    edu = book.get("education", "uni_bit")
    bits = []
    if edu:
        bits.append(f"{edu['degree']} student at {edu['institution']} (expected completion {edu['expected_completion'][:4]}).")
    emp = book.in_category("employment")
    jig = next((e for e in emp.values() if e.get("duties_beyond_core_role")), None)
    if jig:
        bits.append(f"Provided staff IT support and network configuration assistance at {jig['employer']} while working as a {jig['title']}.")
    cons = next((e for e in emp.values() if not e.get("duties_beyond_core_role")), None)
    if cons:
        bits.append(f"{cons['title']} at {cons['employer']}.")
    return " ".join(bits)
