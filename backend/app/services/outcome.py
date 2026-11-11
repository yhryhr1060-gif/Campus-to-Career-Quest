"""Consequences: sticky notes, the ending card, the face-down other ending, 1–2 strategy notes,
and 1–2 language notes on what the learner actually said (quotes checked, generated once)."""
from __future__ import annotations

import json
import re

from .. import db
from ..content import get_chapter
from ..llm import mock
from ..llm.client import call_json
from ..llm.prompt_templates import VERSIONS, render
from ..llm.schemas import FeedbackOut, NpcTurn
from ..rules.engine import check_plan, plan_from_dict
from ..rules.quotes import quote_in
from ..rules.utterance import confirm_blockers
from .session import Conflict, NotFound, load, chapter_for

_ORDER = {"error": 0, "info_missing": 1, "upgrade": 2, "positive": 3}

# These checks only withhold praise; they never infer a successful interaction move.
# A quoted date or a keyword alone is not evidence that the learner offered or agreed to it.
_REPORTED_OR_EMPTY = re.compile(
    r'\b(said|says|told|quoted|according to|nothing|nobody|nowhere|neither|nor)\b|["“”]', re.I,
)
_STRATEGIES = {
    "alternative": (
        "You put forward a concrete plan that passed the deadline checks",
        "For further practice, offer a concrete plan — e.g. “I could send you … by …”.",
    ),
    "commitment": (
        "You confirmed the restated plan in your reply",
        "For further practice, confirm the restated plan in your own reply — e.g. “Yes, that's right. I'll make sure it's on time.”",
    ),
}


def _event_evidence(event: dict) -> tuple[dict, list]:
    """Recover the successful, checked model facts from existing logs; old/missing logs give no praise."""
    try:
        payload = json.loads(event.get("payload_json") or "{}")
        decision = payload.get("decision")
        raw_log = json.loads(event.get("raw_output") or "{}")
        outputs = raw_log.get("raw")
        if not isinstance(decision, dict) or not isinstance(outputs, list) or not outputs:
            return {}, []
        raw = outputs[-1].strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        parsed = NpcTurn.model_validate_json(fenced.group(1) if fenced else raw)
    except (ValueError, TypeError, AttributeError):
        return {}, []
    dropped_items = decision.get("dropped", [])
    if not isinstance(dropped_items, list):
        return {}, []
    dropped = [item.get("fact") for item in dropped_items if isinstance(item, dict)]
    text = event.get("player_text") or ""
    facts = [f for f in parsed.candidate_facts
             if f.source == "player" and f.polarity == "affirm" and f.mode != "quoted"
             and quote_in(f.quote, text) and f.model_dump() not in dropped]
    return decision, facts


def strategy_feedback(player_turns: list[str], *, events: list[dict] | None = None) -> list[dict]:
    """Describe evidenced moves, not language proficiency or independent use of an expression.

    Only the current attempt's rule-checked events can supply positive feedback. A conditional
    offer may be a proposal, but only an unqualified textual confirmation is a commitment.
    """
    events = events or []
    start = max((i + 1 for i, e in enumerate(events)
                 if e.get("phase") == "negotiate" and e.get("kind") == "retry"), default=0)
    found = {}
    for event in events[start:]:
        text = event.get("player_text")
        if (event.get("phase") != "negotiate" or event.get("kind") != "turn"
                or not text or text not in player_turns or _REPORTED_OR_EMPTY.search(text)):
            continue
        decision, facts = _event_evidence(event)
        kind = (event.get("rule_decision") or "").split("+", 1)[0]
        if decision.get("kind") != kind or not decision.get("plan"):
            continue
        blockers = confirm_blockers(text)
        if (kind in ("follow_up", "restate") and not {"negation", "hedge"}.intersection(blockers)
                and any(f.field in ("outline_due", "final_due") for f in facts)):
            found.setdefault("alternative", text)
        if (kind == "confirmed" and decision.get("became_confirmed") is True and not blockers
                and any(f.field == "confirm" and f.mode == "assert" for f in facts)):
            found.setdefault("commitment", text)

    out = [{"move": move, "kind": "did_well", "text": _STRATEGIES[move][0], "quote": quote}
           for move, quote in found.items()]
    for move, (_, suggestion) in _STRATEGIES.items():
        if len(out) >= 2:
            break
        if move not in found:
            out.append({"move": move, "kind": "try_next", "text": suggestion, "quote": None})
    return out[:2]


def language_notes(sid: str, state: dict, chapter) -> tuple[list[dict], bool]:
    """1–2 notes on the learner's own lines in this conversation. Cached in the session after the first call."""
    neg = state["negotiation"]
    if neg.get("language_notes") is not None:
        return neg["language_notes"], True
    lines = [t["text"] for t in neg["turns"] if t["speaker"] == "player"]
    shown: list[dict] = []
    dropped: list[dict] = []
    res = None
    if lines:
        prompt = render("turn_feedback", chapter=chapter, npc=chapter.npc, lines=lines)
        res = call_json(FeedbackOut, [{"role": "user", "content": prompt}],
                        prompt_version=VERSIONS["turn_feedback"], mock=lambda: mock.mock_turn_feedback(lines))
        if res.ok:
            for item in sorted(res.data.items, key=lambda i: _ORDER[i.category]):
                src = next((ln for ln in lines if quote_in(item.quote, ln)), None)
                if src is None:
                    dropped.append({**item.model_dump(), "drop_reason": "quote_not_in_learner_lines"})
                elif len(shown) >= 2:
                    dropped.append({**item.model_dump(), "drop_reason": "over_limit"})
                else:
                    shown.append({**item.model_dump(), "line": src})
    available = not lines or bool(res and res.ok)

    def cache_if_current(latest: dict) -> None:
        latest_neg = latest["negotiation"]
        if (latest["phase"] != "outcome" or latest_neg["attempt"] != neg["attempt"]
                or latest_neg["turns"] != neg["turns"]):
            raise Conflict("the conversation changed; open the latest reflection again")
        if available and latest_neg.get("language_notes") is None:
            latest_neg["language_notes"] = shown

    latest = db.mutate_state(sid, cache_if_current)
    if latest is None:
        raise NotFound(sid)
    db.log_event(sid, "outcome", "language_notes",
                 model=res.model if res else None, prompt_version=res.prompt_version if res else None,
                 raw_output=res.raw_for_log() if res else None,
                 rule_decision="ok" if (res and res.ok) else ("no_lines" if not lines else "model_failed"),
                 payload={"shown": shown, "dropped": dropped})
    cached = latest["negotiation"].get("language_notes")
    return (cached, True) if cached is not None else (shown, False)


def _current_attempt_events(events: list[dict], attempt: int) -> list[dict]:
    """Exclude prior attempts, including legacy events without an explicit attempt field."""
    start = max((i + 1 for i, e in enumerate(events) if e.get("kind") == "retry"
                 and e.get("phase") == "negotiate"), default=0)
    current = []
    for event in events[start:]:
        try:
            payload = json.loads(event.get("payload_json") or "{}")
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("attempt", attempt) == attempt:
            current.append({**event, "payload": payload})
    return current


def unfinished_reflection(state: dict, chapter, events: list[dict]) -> dict:
    """Explain recorded progress without treating technical failures as learner errors.

    Known conditions come from public facts and server lines the learner actually saw.
    Never fill this section from the chapter's private limits or hidden ending content.
    """
    neg = state["negotiation"]
    ag = neg["agreement"]
    current = _current_attempt_events(events, neg["attempt"])
    exchanges = [e for e in current if e.get("phase") == "negotiate"
                 and e.get("kind") in ("opening", "opening_failed", "turn", "turn_failed", "confirm_button")]

    def technical(event: dict) -> bool:
        return event.get("kind") in ("opening_failed", "turn_failed") or (
            event.get("rule_decision") or "").startswith("model_failed")

    failures = sum(technical(e) for e in exchanges)
    last = exchanges[-1] if exchanges else {}
    interrupted = bool(last) and technical(last)
    last_kind = (last.get("rule_decision") or "").split("+", 1)[0]
    proposal = plan_from_dict(ag.get("proposal"), chapter)
    known = list(chapter.public_facts)
    shown = {t["text"] for t in neg["turns"] if t["speaker"] == "npc"}
    for event in exchanges:
        kind = (event.get("rule_decision") or "").split("+", 1)[0]
        reply = event["payload"].get("reply")
        if kind in ("constraint_change", "rejected") and isinstance(reply, str) and reply in shown:
            if reply not in known:
                known.append(reply)

    if ag["status"] == "proposed" and proposal is not None:
        reason = "A plan was proposed, but your confirmation has not been recorded."
        steps = [f"Review the proposed plan: {proposal.describe()}.",
                 "Continue the conversation to confirm it clearly, or explain what needs to change."]
    elif neg["paused"]:
        reason = "This attempt reached the conversation limit before an agreement was confirmed."
        steps = ["Start a new attempt from the same goal. Your email and the earlier conversation are kept.",
                 "Ask about any unclear conditions, then offer a concrete plan and confirm the restatement."]
    elif interrupted:
        reason = ("A service problem interrupted the conversation. "
                  "This is a technical interruption, not an assessment of your English.")
        steps = ["Continue this attempt and retry the interrupted exchange when the service is available.",
                 "Your completed turns and any proposed plan are still saved."]
    elif last_kind == "withdrawn":
        reason = "You withdrew the proposed plan, and no replacement was confirmed."
        steps = ["Continue with a different plan that you can realistically keep.",
                 "Check the restated details before confirming."]
    elif last_kind in ("rejected", "constraint_change"):
        reason = "The last plan did not meet a condition Prof. Chen explained; no revised plan was confirmed."
        steps = ["Use the conditions already discussed to revise your plan.",
                 "Ask Prof. Chen what is possible if a condition is still unclear."]
    elif last_kind == "incomplete":
        reason = "The proposed plan still needed more detail before it could be confirmed."
        steps = ["Say what you can submit and when each part will arrive.",
                 "Continue until the complete plan is restated, then confirm or correct it."]
    elif ag.get("pending"):
        reason = "A plan was suggested, but the follow-up discussion and final confirmation were not completed."
        steps = ["Answer Prof. Chen's follow-up about your progress and what you can submit.",
                 "Check and confirm the plan after it is restated."]
    else:
        reason = ("You ended this attempt before discussing a plan." if neg["turn_count"] == 0
                  else "The conversation ended before a plan was confirmed.")
        steps = ["Continue from your current progress and explain what you can realistically submit.",
                 "Ask about unclear conditions, propose a deadline, and confirm the restated plan."]
    if interrupted and ag["status"] == "proposed":
        reason += " A service problem interrupted the latest exchange."
    if neg["paused"] and ag["status"] == "proposed":
        steps = ["This attempt is paused. Start a new attempt from the same goal.",
                 "Use the proposed plan as a starting point, then confirm the new restatement."]
    return {"reason": reason, "known_constraints": known, "next_steps": steps,
            "technical_failures": failures}


def outcome(sid: str) -> dict:
    state = load(sid)
    if state["phase"] != "outcome":
        raise Conflict("the chapter is not finished yet")
    chapter = chapter_for(state)
    neg = state["negotiation"]
    ag = neg["agreement"]
    plan = plan_from_dict(ag["confirmed"], chapter)
    agreed = ag["status"] == "confirmed" and plan is not None and check_plan(chapter, plan) is None
    events = db.list_events(sid)
    player_turns = [t["text"] for t in neg["turns"] if t["speaker"] == "player"]
    language, language_available = language_notes(sid, state, chapter)
    result = {
        "status": "agreed" if agreed else "not_agreed",
        "notes": plan.notes() if agreed else [],
        "ending": {
            "id": plan.submission_plan if agreed else "not_agreed",
            "title": chapter.endings[plan.submission_plan].title if agreed else "No agreement yet",
            "plan": plan.describe() if agreed else "No agreement was confirmed.",
            "confirm_quote": ag["confirm_quote"] if agreed else None,
        },
        "other_ending": ({"id": "staged" if plan.submission_plan == "full" else "full",
                          "face_down": True, "text": "There is another ending."} if agreed else None),
        "strategy": strategy_feedback(player_turns, events=events),
        "language": language,
        "language_available": language_available,
        "xp": db.total_xp(sid),
        "turns": neg["turn_count"],
    }
    if not agreed:
        result["reflection"] = unfinished_reflection(state, chapter, events)
    db.log_event(sid, "outcome", "outcome_shown", payload=result)
    return result
