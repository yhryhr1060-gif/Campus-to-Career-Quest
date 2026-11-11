"""A last safety check before a spoken "yes" becomes a confirmed agreement.

This is NOT how proposals are understood — the model labels polarity and mode, and conditional or
question proposals are kept as proposals. This check runs at the final confirm step, on the
whole learner turn. If it finds a negation, a condition, a revision or a hedge, the agreement is
not confirmed; Prof. Chen asks once more and the Confirm button is always available.
"""
from __future__ import annotations

import re

# Phrases that contain "no"/"not" but are not refusals.
_SAFE = re.compile(
    r"\b(no later than|not later than|no problem|not a problem|no worries|not at all|no doubt)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(not|no|never|cannot|unable|nope)\b|n't\b|\bcan not\b",
    re.IGNORECASE,
)
_CONDITION = re.compile(r"\b(if|unless|provided that|as long as)\b", re.IGNORECASE)
_HEDGE = re.compile(
    r"\b(maybe|perhaps|probably|possibly|might|i think|i guess|i suppose|not sure|hopefully)\b",
    re.IGNORECASE,
)
_REVISION = re.compile(
    r"\b(but|however|except|actually|instead|rather|wait|hold on|on second thought|"
    r"changed my mind|change my mind|take that back|scratch that)\b",
    re.IGNORECASE,
)
_EXPLICIT_YES = re.compile(
    r"^\s*(?:(?:well|thanks|thank you)[,!.]?\s+)?"
    r"(?:yes|yeah|yep|yup|sure|ok|okay|alright|all right|absolutely|definitely|"
    r"that works|sounds good|deal|agreed|correct|that's right|that is right|"
    r"that's correct|that is correct|exactly|perfect|great|fine|"
    r"i (?:agree|confirm|accept)|i can confirm)\b",
    re.IGNORECASE,
)


def is_explicit_confirmation(text: str) -> bool:
    """Require the learner's own affirmative statement, not just a model-labelled quote."""
    return bool(_EXPLICIT_YES.search(text.replace("’", "'")))


def confirm_blockers(text: str) -> list[str]:
    s = _SAFE.sub(" ", text.replace("’", "'"))
    found = []
    if _NEGATION.search(s):
        found.append("negation")
    if _CONDITION.search(s):
        found.append("condition")
    if _HEDGE.search(s):
        found.append("hedge")
    if _REVISION.search(s):
        found.append("revision")
    if "?" in s:
        found.append("question")
    return found
