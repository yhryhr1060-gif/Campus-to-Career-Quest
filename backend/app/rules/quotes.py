"""Quote checking: a quote counts only if it really appears in the source text.

Matching ignores case, runs of whitespace and curly-vs-straight quotes/apostrophes;
everything else must match character for character.
"""
from __future__ import annotations

import re

_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "–": "-"})


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_TRANS)).strip().lower()


def quote_in(quote: str | None, source: str | None) -> bool:
    if not quote or not source:
        return False
    q = normalize(quote)
    return bool(q) and q in normalize(source)


def sentence_containing(quote: str, text: str) -> str:
    """Return the sentence of `text` that contains `quote` (falls back to the whole text)."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    q = normalize(quote)
    for s in sentences:
        if q and q in normalize(s):
            return s
    return text
