"""Prepare: write the email, check and revise it, decide to send, check the summary, predict."""
from __future__ import annotations

from .. import db
from ..content import get_chapter
from ..llm import mock
from ..llm.client import call_json
from ..llm.prompt_templates import VERSIONS, render
from ..llm.schemas import SummaryOut
from ..limits import MAX_EMAIL_CHARS, MAX_PREDICTION_CHARS, MAX_BACKUP_CHARS, validate_length
from ..rules.quotes import quote_in
from . import writing_feedback
from .session import Conflict, NotFound, load, public_state, chapter_for

def _update(sid: str, change) -> dict:
    state = db.mutate_state(sid, change)
    if state is None:
        raise NotFound(sid)
    return state


def _require_prepare(state: dict) -> None:
    if state["phase"] not in ("explore", "prepare") or state["prep"]["email_final"]:
        raise Conflict("the email has already been sent")


def get_feedback(sid: str, draft: str, request_id: str | None = None) -> dict:
    """Whole-draft check bound to this draft version (see services/writing_feedback.py)."""
    return writing_feedback.check_draft(sid, draft, request_id=request_id)


def save_draft(sid: str, draft: str) -> dict:
    validate_length(draft, MAX_EMAIL_CHARS, "Email")
    def save(current):
        _require_prepare(current)
        current["prep"]["draft"] = draft
    state = _update(sid, save)
    return public_state(sid, state)


def send_email(sid: str, email: str, *, acknowledged_open_points: bool = False) -> dict:
    """Fix the final email, extract a summary and keep only fields whose quotes are really in it.

    Sending is the learner's decision. Open feedback points are recorded with the send, never used
    to refuse it; only the game's own limits (length, near-empty text) can stop a send.
    """
    state = load(sid)
    _require_prepare(state)
    validate_length(email, MAX_EMAIL_CHARS, "Email")
    if len(email.strip()) < 20:
        raise Conflict("the email is too short to send", code="email_too_short")
    chapter = chapter_for(state)
    prompt = render("prep_summary", npc=chapter.npc, email=email)
    res = call_json(SummaryOut, [{"role": "user", "content": prompt}],
                    prompt_version=VERSIONS["prep_summary"], mock=lambda: mock.mock_summary(email))

    kept, dropped, seen = [], [], set()
    if res.ok:
        for f in res.data.fields:
            if f.field in seen:
                dropped.append({**f.model_dump(), "drop_reason": "duplicate_field"})
            elif not quote_in(f.quote, email):
                dropped.append({**f.model_dump(), "drop_reason": "quote_not_in_email"})
            else:
                kept.append(f.model_dump())
                seen.add(f.field)

    decision = {}

    def send(current):
        _require_prepare(current)
        prep = writing_feedback.ensure_prep(current["prep"])
        try:
            sent_version = writing_feedback.version_for(prep, email, "send")["version"]
        except Conflict:
            sent_version = None     # the version limit never prevents sending
        snapshot = writing_feedback.review_state(prep, email)
        decision.update(version=sent_version, at=db.now_iso(), feedback_state=snapshot["feedback_state"],
                        base_version=snapshot["base_version"], open_points=snapshot["open"],
                        kept_points=snapshot["kept"], resolved_points=snapshot["resolved"],
                        uncertain_points=snapshot["uncertain"],
                        acknowledged_open_points=bool(acknowledged_open_points))
        prep["send_decisions"].append(dict(decision))
        prep.update(draft=email, email_final=email, summary=kept, summary_confirmed=False)
        current["phase"] = "prepare"
    state = _update(sid, send)
    db.log_event(sid, "prepare", "email_sent", player_text=email, model=res.model,
                 prompt_version=res.prompt_version, raw_output=res.raw_for_log(),
                 rule_decision="ok" if res.ok else "model_failed",
                 payload={"kept": kept, "dropped": dropped, "send_decision": decision})
    return {**public_state(sid, state), "summary_available": res.ok}


def edit_again(sid: str) -> dict:
    """Go back from the summary to the editor (before confirming)."""
    def edit(current):
        if current["phase"] not in ("explore", "prepare") or current["prep"]["summary_confirmed"]:
            raise Conflict("the summary is already confirmed")
        current["prep"].update(email_final=None, summary=None)
    state = _update(sid, edit)
    db.log_event(sid, "prepare", "back_to_edit")
    return public_state(sid, state)


def confirm_summary(sid: str) -> dict:
    def confirm(current):
        if current["phase"] not in ("explore", "prepare") or not current["prep"]["email_final"]:
            raise Conflict("send the email before confirming its summary")
        current["prep"]["summary_confirmed"] = True
    state = _update(sid, confirm)
    db.log_event(sid, "prepare", "summary_confirmed", payload={"summary": state["prep"]["summary"]})
    return public_state(sid, state)


def _prediction_values(prediction: str, backup_plan: str) -> None:
    validate_length(prediction, MAX_PREDICTION_CHARS, "Prediction")
    validate_length(backup_plan, MAX_BACKUP_CHARS, "Backup plan")


def _require_prediction(state: dict) -> None:
    if (state["phase"] not in ("explore", "prepare") or state["prep"]["done"]
            or not state["prep"]["summary_confirmed"]):
        raise Conflict("prediction notes can only be saved before entering the office")


def save_prediction(sid: str, prediction: str, backup_plan: str) -> dict:
    """Autosave notes without advancing the chapter or awarding XP."""
    _prediction_values(prediction, backup_plan)
    def save(current):
        _require_prediction(current)
        current["prep"].update(prediction=prediction, backup_plan=backup_plan)
    return public_state(sid, _update(sid, save))


def submit_prediction(sid: str, prediction: str, backup_plan: str) -> dict:
    """Record (not score) what the learner expects Prof. Chen to say and their fallback offer."""
    _prediction_values(prediction, backup_plan)
    def submit(current):
        _require_prediction(current)
        current["prep"].update(prediction=prediction, backup_plan=backup_plan, done=True)
        current["phase"] = "negotiate"
    state = _update(sid, submit)
    chapter = chapter_for(state)
    newly = db.award_xp(sid, "prep_complete", chapter.xp.prep_complete)
    db.log_event(sid, "prepare", "prediction", player_text=prediction,
                 payload={"backup_plan": backup_plan}, xp=chapter.xp.prep_complete if newly else 0)
    return public_state(sid, state)
