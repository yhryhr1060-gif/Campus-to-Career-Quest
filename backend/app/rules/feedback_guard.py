"""Rule checks on writing-feedback suggestions. No model is involved in these decisions.

A suggestion should correct language, not change what the learner said. These checks compare
"protected" content (numbers, days, months, clock words and negation) between the quoted words
and the suggested replacement. They are deliberately conservative: a flagged suggestion is shown
as uncertain for the learner to judge, never silently applied or presented as a clear error.
"""
from __future__ import annotations

import difflib
import re

_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                 "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12"}
_DATE_WORDS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
               "january", "february", "march", "april", "may", "june", "july", "august", "september",
               "october", "november", "december", "today", "tonight", "tomorrow", "yesterday",
               "noon", "midday", "midnight", "am", "pm"}
# "may" is also a modal verb; it only counts as a month next to a number ("May 3", "3 May").
_MONTH_MAY = re.compile(r"\bmay\s+\d|\d(?:st|nd|rd|th)?\s+(?:of\s+)?may\b", re.IGNORECASE)
_SAFE_NEG = re.compile(r"\b(no later than|not later than|no problem|not a problem|no longer than)\b", re.IGNORECASE)
_NEG = re.compile(r"\b(not|never|cannot|unable|no|nobody|nothing|neither|nor|without)\b|n['’]t\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|\d+(?::\d+)?")


def protected_tokens(text: str) -> set[str]:
    """Numbers and date/time words that a language correction must not change."""
    found: set[str] = set()
    lowered = text.lower()
    for token in _WORD.findall(lowered):
        token = token.replace("’", "'")
        if token[0].isdigit():
            found.add(token)
        elif token in _NUMBER_WORDS:
            found.add(_NUMBER_WORDS[token])
        elif token == "may":
            if _MONTH_MAY.search(lowered):
                found.add("may")
        elif token in _DATE_WORDS:
            found.add(token)
        elif token in ("a.m", "p.m"):
            found.add(token.replace(".", ""))
    for match in re.finditer(r"\b(a\.m\.|p\.m\.)", lowered):
        found.add(match.group(1).replace(".", ""))
    return found


def has_negation(text: str) -> bool:
    return bool(_NEG.search(_SAFE_NEG.sub(" ", text)))


def meaning_flags(quote: str, suggestion: str, *, allowed_context: str = "") -> list[str]:
    """Return rule flags for a suggestion that may change the learner's meaning.

    allowed_context is text whose facts the suggestion may reuse without inventing anything:
    the rest of the learner's draft and the chapter's public task facts.
    """
    flags: list[str] = []
    before, after = protected_tokens(quote), protected_tokens(suggestion)
    if before - after:
        flags.append("changes_fact")          # a date/number in the learner's words was removed or replaced
    added = after - before
    if added and not added <= protected_tokens(allowed_context):
        flags.append("adds_unstated_fact")    # a date/number that the learner and the task never stated
    if has_negation(quote) != has_negation(suggestion):
        flags.append("changes_negation")
    return flags


def is_large_rewrite(quote: str, suggestion: str) -> bool:
    """True when a correction replaces far more than it needs to (not a minimal edit)."""
    a, b = _WORD.findall(quote.lower()), _WORD.findall(suggestion.lower())
    if len(a) < 3 or not b:
        return False
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    return ratio < 0.5 and len(b) > len(a) + 5
