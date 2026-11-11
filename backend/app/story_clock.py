"""The story clock. All dates are interpreted against the chapter's fixed clock (e.g. Wed 14:00),
never against the computer's real time.

A StoryTime is (day, minute):
  day    — days since Monday 00:00 of the story week (Mon=0 … Sun=6, next Mon=7, next Wed=9)
  minute — minutes after midnight, or None when the learner gave no clock time ("Sunday evening").

Codes are the normalised strings the model and the YAML use:
  "FRI 18:00", "SUN", "NEXT WED", "NEXT WED 09:30", "TODAY 17:00", "TOMORROW"
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DAY_CODES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_CODE_RE = re.compile(
    r"^\s*(NEXT\s+)?(MON|TUE|WED|THU|FRI|SAT|SUN|TODAY|TOMORROW)(?:\s+(\d{1,2}):(\d{2}))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StoryTime:
    day: int
    minute: int | None = None

    @property
    def concrete(self) -> bool:
        return self.minute is not None

    def total(self) -> int:
        if self.minute is None:
            raise ValueError("vague time has no total")
        return self.day * 1440 + self.minute

    def with_time(self, hhmm: str) -> "StoryTime":
        h, m = hhmm.split(":")
        return StoryTime(self.day, int(h) * 60 + int(m))

    def code(self) -> str:
        week, wd = divmod(self.day, 7)
        prefix = "NEXT " if week >= 1 else ""
        base = f"{prefix}{DAY_CODES[wd]}"
        if self.minute is None:
            return base
        return f"{base} {self.minute // 60:02d}:{self.minute % 60:02d}"

    def storage(self) -> dict[str, int | None]:
        """Absolute story coordinates; display codes must not be parsed as new utterances."""
        return {"day": self.day, "minute": self.minute}

    def short(self) -> str:
        """'Fri 18:00' — used on sticky notes and cards."""
        week, wd = divmod(self.day, 7)
        name = DAY_NAMES[wd][:3]
        prefix = "next " if week >= 1 else ""
        t = "" if self.minute is None else f" {self.minute // 60:02d}:{self.minute % 60:02d}"
        return f"{prefix}{name}{t}"

    def spoken(self) -> str:
        """'6 pm on Sunday' — how Prof. Chen restates a time."""
        week, wd = divmod(self.day, 7)
        day = ("next " if week >= 1 else "") + DAY_NAMES[wd]
        if self.minute is None:
            return day
        h, m = divmod(self.minute, 60)
        if h == 12 and m == 0:
            clock = "noon"
        elif h == 0 and m == 0:
            clock = "midnight"
        else:
            suffix = "am" if h < 12 else "pm"
            h12 = h % 12 or 12
            clock = f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"
        return f"{clock} on {day}"


def resolve_weekday(weekday: int, now: StoryTime, explicit_next: bool) -> int:
    """Turn a weekday (0=Mon) into a story day index relative to `now`.

    "Friday" said on Wednesday -> this Friday. "Monday" -> next Monday.
    "Wednesday" said on Wednesday -> next Wednesday (today is said as "today").
    "next Friday" -> Friday of next week.
    """
    this_week = (now.day // 7) * 7 + weekday
    if explicit_next:
        return this_week + 7
    if this_week <= now.day:
        return this_week + 7
    return this_week


def parse_code(code: str | None, now: StoryTime) -> StoryTime | None:
    if not code:
        return None
    m = _CODE_RE.match(code)
    if not m:
        return None
    nxt, token, hh, mm = m.groups()
    token = token.upper()
    if token == "TODAY":
        day = now.day
    elif token == "TOMORROW":
        day = now.day + 1
    else:
        day = resolve_weekday(DAY_CODES.index(token), now, bool(nxt))
    minute = None
    if hh is not None:
        h, mi = int(hh), int(mm)
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        minute = h * 60 + mi
    return StoryTime(day, minute)


def parse_stored_time(code: str | None, now: StoryTime, metadata: dict | None = None) -> StoryTime | None:
    """Read a saved time, preserving its already-resolved day.

    Old saves contain only ``StoryTime.code()``: a bare weekday belongs to story week zero,
    and NEXT belongs to week one. Unlike a fresh learner utterance, WED in a save must not
    roll forward because the story clock is also Wednesday. Metadata on new saves removes
    this ambiguity; inconsistent/corrupt metadata fails closed.
    """
    if not isinstance(code, str) or not code:
        return None
    if metadata is not None:
        if not isinstance(metadata, dict):
            return None
        day, minute = metadata.get("day"), metadata.get("minute")
        if type(day) is not int or day < 0 or "minute" not in metadata:
            return None
        if minute is not None and (type(minute) is not int or not 0 <= minute < 1440):
            return None
        value = StoryTime(day, minute)
        return value if value.code() == " ".join(code.upper().split()) else None
    match = _CODE_RE.match(code)
    if match is None:
        return None
    nxt, token, hh, mm = match.groups()
    token = token.upper()
    if token in {"TODAY", "TOMORROW"}:  # tolerate older hand-authored fixtures/saves
        return parse_code(code, now)
    minute = None
    if hh is not None:
        h, mi = int(hh), int(mm)
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        minute = h * 60 + mi
    return StoryTime(DAY_CODES.index(token) + (7 if nxt else 0), minute)


def parse_clock(code: str) -> StoryTime:
    """Parse the chapter's own story clock ("WED 14:00") — always this week."""
    m = _CODE_RE.match(code)
    if not m or m.group(3) is None or m.group(2).upper() in {"TODAY", "TOMORROW"}:
        raise ValueError(f"bad story clock: {code!r}")
    day = DAY_CODES.index(m.group(2).upper()) + (7 if m.group(1) else 0)
    return StoryTime(day, int(m.group(3)) * 60 + int(m.group(4)))
