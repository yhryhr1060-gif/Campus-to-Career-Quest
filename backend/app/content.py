"""Load chapter content from YAML and validate it with Pydantic."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator, model_validator

from .config import CONTENT_DIR
from .limits import INPUT_LIMITS
from .story_clock import StoryTime, parse_clock, parse_code


class Task(BaseModel):
    situation: str
    target: str
    goal: str
    goal_short: str


class Character(BaseModel):
    name: str
    role: str
    persona: str = ""
    speech_style: str = ""


class Area(BaseModel):
    id: str
    label: str
    description: str = ""     # one or two lines shown when the learner is at this place
    icon: str = ""            # placeholder glyph until there is art


class Topic(BaseModel):
    """One pre-written question the learner can put to a person, with its fixed reply."""

    id: str
    ask: str
    reply: str
    note: str = ""
    expressions: list[str] = []
    requires: str | None = None    # another topic of the same person that must be asked first


class Hotspot(BaseModel):
    id: str
    area: str
    label: str
    title: str
    text: str
    note: str = ""
    kind: Literal["object", "person", "moment"] = "object"
    speaker: str = ""              # who says `text`, for persons and overheard moments
    expressions: list[str] = []    # target expressions that occur, in context, in `text`
    topics: list[Topic] = []


class PrivateInfo(BaseModel):
    when: str
    reveal: str


class Rules(BaseModel):
    outline_due_max: str
    final_due_max: str
    max_turns: int = 12
    nudge_at_turn: int = 10


class Ending(BaseModel):
    title: str
    description: str


class ConstraintChange(BaseModel):
    id: str
    npc_line: str


class Expression(BaseModel):
    id: str
    text: str
    function: str
    pattern: str

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class HintLevel(BaseModel):
    level: int
    kind: str
    text: str


class Hints(BaseModel):
    unlock_after: str
    levels: list[HintLevel]


class XP(BaseModel):
    prep_complete: int
    first_agreement: int


class Chapter(BaseModel):
    id: str
    version: str
    title: str
    location: str
    story_clock: str
    story_clock_label: str
    default_due_time: str
    player: dict
    intro: str = ""                # opening lines at the starting place
    start_area: str = ""           # defaults to the first area
    task: Task
    characters: dict[str, Character]
    areas: list[Area]
    hotspots: list[Hotspot]
    public_facts: list[str]
    private_info: list[PrivateInfo]
    rules: Rules
    endings: dict[Literal["full", "staged"], Ending]
    constraint_change: ConstraintChange
    npc_lines: dict[str, str]
    expressions: list[Expression]
    hints: Hints
    xp: XP

    @model_validator(mode="after")
    def _check(self) -> "Chapter":
        now = self.now
        for code in (self.rules.outline_due_max, self.rules.final_due_max):
            t = parse_code(code, now)
            if t is None or not t.concrete:
                raise ValueError(f"rule time must be a concrete code: {code!r}")
        area_ids = {a.id for a in self.areas}
        for h in self.hotspots:
            if h.area not in area_ids:
                raise ValueError(f"hotspot {h.id} points at unknown area {h.area}")
        expression_ids = {e.id for e in self.expressions}
        seen_ids: set[str] = set()
        for h in self.hotspots:
            if h.id in seen_ids or ":" in h.id:
                raise ValueError(f"hotspot id must be unique and contain no colon: {h.id!r}")
            seen_ids.add(h.id)
            topic_ids = [t.id for t in h.topics]
            if len(set(topic_ids)) != len(topic_ids):
                raise ValueError(f"hotspot {h.id} has duplicate topic ids")
            for t in h.topics:
                if ":" in t.id or (t.requires is not None and t.requires not in topic_ids):
                    raise ValueError(f"hotspot {h.id} topic {t.id}: bad id or unknown 'requires'")
            for used in [*h.expressions, *(e for t in h.topics for e in t.expressions)]:
                if used not in expression_ids:
                    raise ValueError(f"hotspot {h.id} names unknown expression {used}")
        if self.start_area and self.start_area not in area_ids:
            raise ValueError(f"start_area {self.start_area!r} is not an area")
        needed = {
            "opening_fallback", "reject_after_limit", "reject_outline_late", "reject_past",
            "reject_order", "follow_up_before_agree", "restate_full", "restate_staged",
            "confirm_unclear", "proposal_withdrawn", "agreed_close", "neutral_fallback", "nudge", "pause",
            "clarify_plan",
        }
        missing = needed - set(self.npc_lines)
        if missing:
            raise ValueError(f"npc_lines missing: {sorted(missing)}")
        if self.hints.unlock_after != self.constraint_change.id:
            raise ValueError("hints.unlock_after must name the constraint_change id")
        return self

    # --- helpers -------------------------------------------------------------
    @property
    def now(self) -> StoryTime:
        return parse_clock(self.story_clock)

    @property
    def outline_max(self) -> StoryTime:
        return parse_code(self.rules.outline_due_max, self.now)  # type: ignore[return-value]

    @property
    def final_max(self) -> StoryTime:
        return parse_code(self.rules.final_due_max, self.now)  # type: ignore[return-value]

    @property
    def npc(self) -> Character:
        return self.characters["prof_chen"]

    @property
    def start(self) -> str:
        return self.start_area or self.areas[0].id

    def clue(self, clue_id: str) -> tuple[Hotspot, Topic | None] | None:
        """A clue is a hotspot ("ivy") or one topic of a person ("ivy:extension")."""
        hotspot_id, _, topic_id = clue_id.partition(":")
        spot = next((h for h in self.hotspots if h.id == hotspot_id), None)
        if spot is None:
            return None
        if not topic_id:
            return spot, None
        topic = next((t for t in spot.topics if t.id == topic_id), None)
        return (spot, topic) if topic else None

    def known_facts(self, notes: list[str]) -> list[str]:
        """What this learner can be expected to know: the task card plus the clues they found.
        Facts behind places they have not looked at are deliberately left out."""
        facts = [self.task.situation, self.task.target]
        for clue_id in notes:
            found = self.clue(clue_id)
            if found:
                note = found[1].note if found[1] else found[0].note
                if note:
                    facts.append(note)
        return facts

    def line(self, key: str, **kw: str) -> str:
        return self.npc_lines[key].format(**kw)

    def public_view(self) -> dict:
        """What the browser may see. Private info, rules, endings and NPC lines stay on the server."""
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "location": self.location,
            "story_clock_label": self.story_clock_label,
            "player": self.player,
            "intro": self.intro,
            "start_area": self.start,
            "task": self.task.model_dump(),
            "characters": {k: {"name": c.name, "role": c.role} for k, c in self.characters.items()},
            "areas": [a.model_dump() for a in self.areas],
            "hotspots": [h.model_dump() for h in self.hotspots],
            "expressions": [{"id": e.id, "text": e.text, "function": e.function} for e in self.expressions],
            "hint_count": len(self.hints.levels),
            "xp": self.xp.model_dump(),
            "input_limits": dict(INPUT_LIMITS),
        }


def load_chapter_file(path: Path) -> Chapter:
    with open(path, encoding="utf-8") as f:
        return Chapter.model_validate(yaml.safe_load(f))


@lru_cache(maxsize=8)
def get_chapter(chapter_id: str = "chapter2") -> Chapter:
    return load_chapter_file(CONTENT_DIR / f"{chapter_id}.yaml")
