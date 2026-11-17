"""Claims that are false and must never appear in generated documents or profile rows.

Canonical rules live in code so they hold even if the database is reset; the `guardrail` rows in profile_facts mirror
them (and can add more). Patterns are case-insensitive regexes.
"""
import re

FORBIDDEN_TERMS = [
    "Network & Systems Administrator",
    "multi-site network",
    "Warehouse Operations & IT Support",
    "ExampleBrand",  # never worked there
]

_AI = r"(?:ml|a\.?i\.?|machine[- ]learning|artificial intelligence)"
_CONTRACT = r"(?:sub-?contract\w*|contractor\w*|contract(?:ing)? work)"
FORBIDDEN_PATTERNS = [
    r"\boodie\b",
    # ML/AI subcontracting or contractor work never happened, in either word order
    rf"\b{_AI}\b[^.\n;]{{0,60}}\b{_CONTRACT}",
    rf"\b{_CONTRACT}[^.\n;]{{0,60}}\b{_AI}\b",
]


class ForbiddenClaimError(ValueError):
    pass


def find_forbidden(text: str, extra_terms: list[str] | None = None, extra_patterns: list[str] | None = None) -> list[str]:
    """Return the offending terms / matched snippets found in `text` (empty list = clean)."""
    hits = [t for t in dict.fromkeys(FORBIDDEN_TERMS + (extra_terms or []))
            if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text, re.I)]  # whole-word: "ExampleBrand" must not match "goodies"
    for pat in dict.fromkeys(FORBIDDEN_PATTERNS + (extra_patterns or [])):
        m = re.search(pat, text, re.I)
        if m and not any(m.group(0).lower() == h.lower() for h in hits):
            hits.append(m.group(0))
    return hits
