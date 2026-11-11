"""Negotiate: Prof. Chen opens from the email, then one model call per learner turn; the rule engine
decides proposals and agreements; Confirm button; hints; pause at the turn limit; retry."""
from __future__ import annotations

import json
import re
from copy import deepcopy

from .. import db
from ..content import get_chapter
from ..limits import MAX_TURN_CHARS, validate_length
from ..llm import mock
from ..llm.client import call_json
from ..llm.prompt_templates import VERSIONS, render
from ..llm.schemas import NpcTurn, OpeningOut
from ..rules.engine import confirm_by_button, evaluate_turn, plan_from_dict
from ..rules.reply_guard import sounds_like_acceptance
from ..session_serialization import serialized_session
from .session import Conflict, NotFound, load, new_negotiation, public_state, chapter_for

HISTORY_ROUNDS = 6


def _require_negotiation(state: dict) -> None:
    if state["phase"] != "negotiate":
        raise Conflict("finish preparing first" if state["phase"] in ("explore", "prepare") else "negotiation is over")


@serialized_session
def start(sid: str) -> dict:
    """Cache a valid opening only; failed openings remain retryable."""
    state = load(sid)
    _require_negotiation(state)
    neg = state["negotiation"]
    if neg["opening"] is None:
        attempt = neg["attempt"]
        chapter = chapter_for(state)
        summary = state["prep"]["summary"] or []
        prompt = render("opening", chapter=chapter, npc=chapter.npc,
                        player_name=chapter.player["name"], summary=summary)
        res = call_json(OpeningOut, [{"role": "user", "content": prompt}],
                        prompt_version=VERSIONS["opening"], mock=lambda: mock.mock_opening(summary, chapter.player["name"]))
        # The learner can leave/retry while the provider is responding. Never write a
        # stale copy of the session over its new phase or a different attempt.
        state = load(sid)
        _require_negotiation(state)
        neg = state["negotiation"]
        if neg["attempt"] != attempt:
            raise Conflict("the conversation changed; reload the current attempt")
        if neg["opening"] is not None:
            return {**public_state(sid, state), "available": True}
        text = res.data.reply if res.ok else None
        if text is None or "in your email" not in text.lower():   # must name the right source
            db.log_event(sid, "negotiate", "opening_failed", model=res.model,
                         prompt_version=res.prompt_version, raw_output=res.raw_for_log(),
                         rule_decision="model_failed" if not res.ok else "invalid_opening_source",
                         payload={"attempt": attempt, "counted": False})
            return {**public_state(sid, state), "available": False}
        created = False

        def save_opening_if_current(latest: dict) -> None:
            nonlocal created
            _require_negotiation(latest)
            latest_neg = latest["negotiation"]
            if latest_neg["attempt"] != attempt:
                raise Conflict("the conversation changed; reload the current attempt")
            if latest_neg["opening"] is None:
                latest_neg["opening"] = text
                latest_neg["turns"].append({"speaker": "npc", "text": text, "mood": "neutral", "kind": "opening"})
                created = True

        state = db.mutate_state(sid, save_opening_if_current)
        if state is None:
            raise NotFound(sid)
        if created:
            db.log_event(sid, "negotiate", "opening", model=res.model, prompt_version=res.prompt_version,
                         raw_output=res.raw_for_log(), rule_decision="ok",
                         payload={"reply": text, "attempt": attempt})
    return {**public_state(sid, state), "available": True}


def _messages(chapter, state: dict, player_text: str) -> list[dict]:
    neg = state["negotiation"]
    ag = neg["agreement"]
    confirmed = plan_from_dict(ag["confirmed"], chapter)
    proposed = plan_from_dict(ag["proposal"], chapter)
    system = render(
        "npc_system", chapter=chapter, npc=chapter.npc, player_name=chapter.player["name"],
        summary=state["prep"]["summary"] or [],
        agreed=confirmed.describe() if confirmed else "",
        proposed=proposed.describe() if proposed else "",
        constraint_fired=ag["constraint_fired"],
        final_max=chapter.final_max.spoken(), outline_max=chapter.outline_max.spoken(),
    )
    msgs = [{"role": "system", "content": system}]
    for t in neg["turns"][-HISTORY_ROUNDS * 2:]:
        if t["speaker"] == "npc":
            # Match the JSON response format even when replaying server-authored NPC lines.
            text = json.dumps({"reply": t["text"], "mood": t.get("mood", "neutral"),
                               "candidate_facts": []}, ensure_ascii=False)
            msgs.append({"role": "assistant", "content": text})
        else:
            msgs.append({"role": "user", "content": t["text"]})
    msgs.append({"role": "user", "content": player_text})
    return msgs


def _last_npc_line(neg: dict) -> str:
    for t in reversed(neg["turns"]):
        if t["speaker"] == "npc":
            return t["text"]
    return ""


def _expression_candidates(chapter, text: str) -> list[str]:
    return [e.id for e in chapter.expressions if re.search(e.pattern, text, re.IGNORECASE)]


def _commit_completed_action(sid: str, **action) -> dict:
    try:
        response = db.commit_session_action(sid, **action)
    except db.StateChanged as exc:
        raise Conflict("the conversation changed; reload it and retry your message") from exc
    if response is None:
        raise NotFound(sid)
    return response


@serialized_session
def turn(sid: str, text: str, *, request_id: str, input_mode: str = "typed",
         asr_text: str | None = None, audio_path: str | None = None) -> dict:
    validate_length(text, MAX_TURN_CHARS, "message")
    cached = db.get_processed(sid, request_id)
    if cached is not None:              # the same turn sent twice is processed once
        return cached

    state = load(sid)
    _require_negotiation(state)
    neg = state["negotiation"]
    if neg["opening"] is None:
        raise Conflict("start the conversation first")
    if neg["paused"]:
        raise Conflict("the conversation is paused; retry from the current goal")
    if neg["agreement"]["status"] == "confirmed":
        raise Conflict("already agreed")
    text = text.strip()
    if not text:
        raise Conflict("empty turn")

    chapter = chapter_for(state)
    ag = neg["agreement"]
    npc_previous = _last_npc_line(neg)
    expected_state = deepcopy(state)

    res = call_json(NpcTurn, _messages(chapter, state, text), prompt_version=VERSIONS["npc_system"],
                    mock=lambda: mock.mock_npc_turn(text, npc_previous))

    if not res.ok:
        # Keep technical failures in the audit log, outside the learner's conversation.
        # Do not cache this response: the same message/request id must be retryable.
        reply = chapter.line("neutral_fallback")
        db.log_event(
            sid, "negotiate", "turn_failed", player_text=text, asr_text=asr_text, audio_path=audio_path,
            model=res.model, prompt_version=res.prompt_version, raw_output=res.raw_for_log(),
            rule_decision="model_failed", xp=0,
            payload={"request_id": request_id, "attempt": neg["attempt"], "counted": False,
                     "input_mode": input_mode, "latency_ms": res.latency_ms,
                     "agreement_status": ag["status"]},
        )
        return {**public_state(sid, state), "reply": reply, "mood": "neutral",
                "decision": "model_failed", "available": False}

    neg["turn_count"] += 1
    neg["turns"].append({"speaker": "player", "text": text, "input_mode": input_mode,
                         "turn_no": neg["turn_count"]})

    decision = evaluate_turn(chapter, ag, res.data.candidate_facts, player_text=text,
                             npc_previous=npc_previous, email=state["prep"]["email_final"] or "")
    reply = decision.reply_override or res.data.reply
    mood = res.data.mood
    if decision.kind == "none" and res.data.needs_clarification:
        ag["pushbacks"] += 1
    guarded = decision.reply_override is None and sounds_like_acceptance(reply)
    if guarded:
        # The model sounded as if it accepted a time; only the server may do that.
        reply = chapter.line("clarify_plan")

    kind = decision.kind
    if guarded:
        kind += "+reply_guarded"
    if ag["status"] != "confirmed":
        if neg["turn_count"] >= chapter.rules.max_turns:
            neg["paused"] = True
            reply = chapter.line("pause")
            kind += "+paused"
        elif neg["turn_count"] == chapter.rules.nudge_at_turn and kind == "none":
            reply = chapter.line("nudge")

    neg["turns"].append({"speaker": "npc", "text": reply, "mood": mood, "kind": kind})
    response = {**public_state(sid, state), "reply": reply, "mood": mood, "decision": kind, "available": True}
    return _commit_completed_action(
        sid, expected_state=expected_state, state=state, response=response, request_id=request_id,
        xp_award=("first_agreement", chapter.xp.first_agreement) if decision.became_confirmed else None,
        event={"phase": "negotiate", "kind": "turn", "player_text": text,
               "asr_text": asr_text, "audio_path": audio_path, "model": res.model,
               "prompt_version": res.prompt_version, "raw_output": res.raw_for_log(), "rule_decision": kind,
               "payload": {"turn_no": neg["turn_count"], "request_id": request_id,
                           "attempt": neg["attempt"], "counted": True, "input_mode": input_mode, "reply": reply,
                           "model_reply": res.data.reply, "latency_ms": res.latency_ms,
                           "decision": decision.to_log(),
                           "expression_candidates": _expression_candidates(chapter, text),
                           "agreement_status": ag["status"]}},
    )


@serialized_session
def confirm(sid: str) -> dict:
    state = load(sid)
    _require_negotiation(state)
    chapter = chapter_for(state)
    neg = state["negotiation"]
    expected_state = deepcopy(state)
    dec = confirm_by_button(chapter, neg["agreement"])
    if dec.became_confirmed:
        neg["turns"].append({"speaker": "npc", "text": dec.reply_override, "mood": "warm", "kind": "confirmed"})
    response = {**public_state(sid, state), "decision": dec.kind}
    return _commit_completed_action(
        sid, expected_state=expected_state, state=state, response=response,
        xp_award=("first_agreement", chapter.xp.first_agreement) if dec.became_confirmed else None,
        event={"phase": "negotiate", "kind": "confirm_button", "rule_decision": dec.kind,
               "payload": {**dec.to_log(), "attempt": neg["attempt"]}},
    )


@serialized_session
def finish(sid: str) -> dict:
    """Leave for reflection with or without an agreement; repeated calls are harmless."""
    state = load(sid)
    if state["phase"] == "outcome":
        return public_state(sid, state)
    _require_negotiation(state)
    neg = state["negotiation"]
    state["phase"] = "outcome"
    db.save_state(sid, state)
    db.log_event(sid, "outcome", "phase_entered",
                 payload={"attempt": neg["attempt"], "agreement_status": neg["agreement"]["status"],
                          "paused": neg["paused"]})
    return public_state(sid, state)


@serialized_session
def resume(sid: str) -> dict:
    """Continue an unfinished conversation without discarding its current attempt."""
    state = load(sid)
    neg = state["negotiation"]
    if state["phase"] not in ("outcome", "negotiate"):
        raise Conflict("finish preparing first")
    if neg["agreement"]["status"] == "confirmed":
        raise Conflict("already agreed; start a new play-through to explore another ending")
    chapter = chapter_for(state)
    if neg["paused"] or neg["turn_count"] >= chapter.rules.max_turns:
        raise Conflict("this attempt is paused; start a new attempt from your current goal")
    if state["phase"] == "negotiate":
        return public_state(sid, state)
    state["phase"] = "negotiate"
    neg.pop("language_notes", None)  # later reflection must include any new learner turns
    db.save_state(sid, state)
    db.log_event(sid, "negotiate", "resumed", payload={"attempt": neg["attempt"]})
    return public_state(sid, state)


@serialized_session
def hint(sid: str) -> dict:
    state = load(sid)
    _require_negotiation(state)
    chapter = chapter_for(state)
    neg = state["negotiation"]
    if not neg["agreement"]["constraint_fired"]:
        raise Conflict("hints open once Prof. Chen has explained his limit")
    if neg["hint_level"] < len(chapter.hints.levels):
        neg["hint_level"] += 1
        db.save_state(sid, state)
    db.log_event(sid, "negotiate", "hint_opened", hint_level=neg["hint_level"],
                 payload={"turn_no": neg["turn_count"]})
    return public_state(sid, state)


@serialized_session
def retry(sid: str) -> dict:
    """Retry from the current goal: earlier turns are kept for analysis, the conversation restarts."""
    state = load(sid)
    if state["phase"] not in ("negotiate", "outcome"):
        raise Conflict("finish preparing first")
    old = state["negotiation"]
    if old["agreement"]["status"] == "confirmed":
        raise Conflict("already agreed")
    state["archived_attempts"].append(old)
    fresh = new_negotiation()
    fresh["attempt"] = old["attempt"] + 1
    state["negotiation"] = fresh
    state["phase"] = "negotiate"
    db.save_state(sid, state)
    db.log_event(sid, "negotiate", "retry", payload={"attempt": fresh["attempt"]})
    return start(sid)
