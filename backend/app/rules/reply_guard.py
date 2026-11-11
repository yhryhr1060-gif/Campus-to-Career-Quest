"""Last check on Prof. Chen's own words before they are shown.

The model is told never to accept a plan, but if its reply still sounds like an acceptance of a
specific time while the rule engine has not moved to `proposed`/`confirmed`, the reply is replaced
with a neutral clarifying question. Agreements are only ever stated by the server's own lines.
"""
from __future__ import annotations

import re

_ACCEPT = re.compile(
    r"\b(agreed|it's a deal|deal\b|that's fine|that is fine|that works|that'll work|sounds good|fine by me|"
    r"okay then|ok then|alright then|i accept|i can accept|i'll accept|you can (?:hand|submit|send)|"
    r"you may (?:hand|submit|send)|no problem|i approve|approved)",
    re.IGNORECASE,
)
_TIME = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|today|next week|"
    r"\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.)|\d{1,2}:\d{2}|noon|midnight)\b",
    re.IGNORECASE,
)


def sounds_like_acceptance(reply: str) -> bool:
    return bool(_ACCEPT.search(reply) and _TIME.search(reply))
