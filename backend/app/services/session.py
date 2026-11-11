"""Session state: one anonymous play-through of a chapter."""
from __future__ import annotations

import re
from functools import lru_cache

from .. import db
from ..content import Chapter, get_chapter
from ..rules.engine import new_agreement_state, summarize_state


class NotFound(Exception):
    pass


class Conflict(Exception):
    """The request does not fit the current phase (e.g. negotiating before the email is sent).

    `code` marks limits of the game itself (empty text, version limit) so the interface can show
    them separately from language advice.
    """

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message)
        self.code = code


MAX_NAME_CHARS = 20
# Letters and digits of any script, single spaces, hyphen, apostrophe, full stop. The nickname is shown
# in the game and inserted into Prof. Chen's lines and prompts, so nothing else is accepted.
_NAME = re.compile(r"[^\W_]+(?:[ '’.\-][^\W_]+)*\.?")


def clean_player_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name or len(name) > MAX_NAME_CHARS or not _NAME.fullmatch(name):
        raise Conflict(f"Use 1–{MAX_NAME_CHARS} letters or numbers for the nickname "
                       "(spaces, hyphens and apostrophes are allowed).", code="nickname_invalid")
    return name


@lru_cache(maxsize=64)
def _personalised(chapter_id: str, name: str) -> Chapter:
    chapter = get_chapter(chapter_id)
    default = chapter.player["name"]
    swap = lambda text: re.sub(rf"\b{re.escape(default)}\b", lambda _: name, text)  # noqa: E731
    return chapter.model_copy(update={
        "player": {**chapter.player, "name": name},
        "public_facts": [swap(f) for f in chapter.public_facts],
        "npc_lines": {k: swap(v) for k, v in chapter.npc_lines.items()},
        "constraint_change": chapter.constraint_change.model_copy(
            update={"npc_line": swap(chapter.constraint_change.npc_line)}),
    })


def player_name(state: dict) -> str:
    return state.get("player_name") or get_chapter(state["chapter_id"]).player["name"]


def chapter_for(state: dict) -> Chapter:
    """The chapter as this learner meets it: Prof. Chen uses their nickname. Rules are unchanged.
    Saved games from before nicknames keep the default name."""
    chapter = get_chapter(state["chapter_id"])
    name = player_name(state)
    return chapter if name == chapter.player["name"] else _personalised(chapter.id, name)


def new_negotiation() -> dict:
    return {
        "turns": [],            # {"speaker": "npc"|"player", "text", "input_mode", "mood", "kind"}
        "turn_count": 0,        # learner turns in this attempt
        "agreement": new_agreement_state(),
        "hint_level": 0,
        "opening": None,
        "paused": False,
        "attempt": 1,
    }


def new_state(chapter: Chapter) -> dict:
    return {
        "chapter_id": chapter.id,
        "chapter_version": chapter.version,
        "phase": "explore",     # explore -> prepare -> negotiate -> outcome (explore is optional)
        "player_name": None,    # game nickname; None until chosen (older saves have no such key)
        "location": chapter.start,      # where on campus the learner is
        "places_visited": [chapter.start],
        "expressions_seen": [],  # target expressions met in the story: exposure only, never mastery
        "notes": [],            # clue ids: a hotspot ("syllabus") or a person's topic ("ivy:extension")
        "prep": {
            "draft": "",
            "feedback_requests": 0,
            "email_final": None,
            "summary": None,    # verified summary fields
            "summary_confirmed": False,
            "prediction": None,
            "backup_plan": None,
            "done": False,
            "versions": [],         # checked draft versions, each with its own feedback and recheck
            "send_decisions": [],   # what was open when the learner chose to send
        },
        "negotiation": new_negotiation(),
        "archived_attempts": [],
    }


def create() -> tuple[str, dict]:
    chapter = get_chapter()
    state = new_state(chapter)
    sid = db.create_session(state)
    db.log_event(sid, "session", "session_created", payload={"chapter": chapter.id, "version": chapter.version})
    return sid, state


def load(sid: str) -> dict:
    state = db.load_state(sid)
    if state is None:
        raise NotFound(sid)
    return state


def key_points(state: dict) -> list[dict]:
    summary = state["prep"]["summary"] or []
    order = {"situation": 0, "request": 1, "proposal": 2}
    return sorted(({"field": f["field"], "text": f["value"]} for f in summary), key=lambda x: order[x["field"]])[:3]


def public_state(sid: str, state: dict) -> dict:
    chapter = get_chapter(state["chapter_id"])
    neg = state["negotiation"]
    prep = state["prep"]
    from .writing_feedback import public_versions  # local import: that module builds on this one
    return {
        "session_id": sid,
        "phase": state["phase"],
        "player_name": player_name(state),
        # Only games created with the nickname step ask for one; older saves carry on as before.
        "needs_name": "player_name" in state and state["player_name"] is None,
        "location": state.get("location") or chapter.start,
        "places_visited": state.get("places_visited", []),
        "expressions_seen": state.get("expressions_seen", []),
        "notes": state["notes"],
        "prep": {
            "draft": prep["draft"],
            "email_final": prep["email_final"],
            "summary": prep["summary"],
            "summary_confirmed": prep["summary_confirmed"],
            "prediction": prep["prediction"],
            "backup_plan": prep["backup_plan"],
            "done": prep["done"],
            # Draft history is only needed while the email can still be edited or reviewed.
            "versions": [] if prep["done"] else public_versions(prep),
        },
        "negotiation": {
            "turns": neg["turns"],
            "turn_count": neg["turn_count"],
            "max_turns": chapter.rules.max_turns,
            "agreement": summarize_state(neg["agreement"], chapter),
            "hints_unlocked": neg["agreement"]["constraint_fired"],
            "hints": [h.model_dump() for h in chapter.hints.levels[: neg["hint_level"]]],
            "paused": neg["paused"],
            "attempt": neg["attempt"],
            "key_points": key_points(state),
        },
        "xp": db.total_xp(sid),
    }


def _require_campus(state: dict) -> None:
    if state["phase"] in ("negotiate", "outcome"):
        raise Conflict("the email has already been sent")


def set_player_name(sid: str, raw: str) -> dict:
    """Choose the game nickname. It can change until the learner goes to the office."""
    name = clean_player_name(raw)

    def change(current):
        _require_campus(current)
        if current["prep"]["done"]:
            raise Conflict("the nickname is fixed once you go to the office")
        current["player_name"] = name
    state = db.mutate_state(sid, change)
    if state is None:
        raise NotFound(sid)
    db.log_event(sid, "session", "player_named")     # the nickname itself is not written to the event log
    return public_state(sid, state)


def set_location(sid: str, area_id: str) -> dict:
    state = load(sid)
    chapter = get_chapter(state["chapter_id"])
    if area_id not in {a.id for a in chapter.areas}:
        raise NotFound(area_id)

    def move(current):
        _require_campus(current)
        current["location"] = area_id
        visited = current.setdefault("places_visited", [])
        if area_id not in visited:
            visited.append(area_id)
    state = db.mutate_state(sid, move)
    if state is None:
        raise NotFound(sid)
    db.log_event(sid, "explore", "location_entered", payload={"area": area_id})
    return public_state(sid, state)


def view_hotspot(sid: str, hotspot_id: str, topic_id: str | None = None) -> dict:
    """Record that the learner looked at something, or asked a person one pre-written question.
    The note and any target expression in that text are recorded only when this save succeeds."""
    state = load(sid)
    chapter = get_chapter(state["chapter_id"])
    spot = next((h for h in chapter.hotspots if h.id == hotspot_id), None)
    if spot is None:
        raise NotFound(hotspot_id)
    topic = None
    if topic_id is not None:
        topic = next((t for t in spot.topics if t.id == topic_id), None)
        if topic is None:
            raise NotFound(f"{hotspot_id}:{topic_id}")
    clue_id = hotspot_id if topic is None else f"{hotspot_id}:{topic.id}"
    met = topic.expressions if topic else spot.expressions

    def add_note(current):
        if topic is not None:
            if hotspot_id not in current["notes"]:
                raise Conflict("talk to this person first", code="topic_locked")
            if topic.requires and f"{hotspot_id}:{topic.requires}" not in current["notes"]:
                raise Conflict("ask the earlier question first", code="topic_locked")
        if clue_id not in current["notes"]:
            current["notes"].append(clue_id)
        seen = current.setdefault("expressions_seen", [])
        for expression_id in met:
            if expression_id not in seen:
                seen.append(expression_id)
    state = db.mutate_state(sid, add_note)
    if state is None:
        raise NotFound(sid)
    db.log_event(sid, "explore", "hotspot_viewed",
                 payload={"hotspot": hotspot_id, "topic": topic.id if topic else None, "expressions": list(met)})
    return public_state(sid, state)


def set_phase(sid: str, phase: str) -> dict:
    """Move between explore and prepare freely; later phases are reached through their own steps."""
    if phase not in ("explore", "prepare"):
        raise Conflict("only explore/prepare can be chosen directly")
    def move(current):
        if current["phase"] in ("negotiate", "outcome"):
            raise Conflict("the email has already been sent")
        current["phase"] = phase
    state = db.mutate_state(sid, move)
    if state is None:
        raise NotFound(sid)
    db.log_event(sid, phase, "phase_entered")
    return public_state(sid, state)
