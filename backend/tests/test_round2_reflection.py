"""Unfinished conversations can be reviewed and resumed without inventing success."""
import json

import pytest

from app import db
from app.llm.client import LLMResult
from app.llm.schemas import OpeningOut
from app.limits import InputTooLong, MAX_TURN_CHARS
from app.services import negotiate, outcome, prepare, session
from app.services.session import Conflict


EMAIL = ("Dear Prof. Chen, I have been ill for three days. "
         "Could I submit the report next Wednesday? Thanks, Alex")


def ready(*, start=True):
    sid, _ = session.create()
    prepare.send_email(sid, EMAIL)
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "He may ask for a clearer plan.", "Ask what is possible.")
    if start:
        negotiate.start(sid)
    return sid


def propose(sid):
    negotiate.turn(sid, "I have been ill.", request_id="reason-0001")
    reply = negotiate.turn(sid, "I can send the full report by Saturday at noon.", request_id="plan-0001")
    assert reply["negotiation"]["agreement"]["status"] == "proposed"


def fail_turn(monkeypatch, sid):
    with monkeypatch.context() as patch:
        patch.setattr(negotiate, "call_json", lambda *a, **k: LLMResult(
            data=None, model="test", prompt_version="test", errors=["test timeout"]))
        return negotiate.turn(sid, "Could we discuss the report?", request_id="failed-0001")


def test_finish_without_agreement_is_idempotent_and_does_not_award_success(chapter):
    sid = ready()
    first = negotiate.finish(sid)
    assert first["phase"] == "outcome"
    assert negotiate.finish(sid) == first
    result = outcome.outcome(sid)
    assert result["status"] == "not_agreed"
    assert result["ending"]["id"] == "not_agreed"
    assert result["ending"]["confirm_quote"] is None
    assert result["notes"] == [] and result["other_ending"] is None
    assert result["language_available"] is True and result["language"] == []
    assert result["xp"] == chapter.xp.prep_complete
    assert result["reflection"]["technical_failures"] == 0
    assert "before discussing a plan" in result["reflection"]["reason"]
    phases = [e for e in db.list_events(sid) if e["phase"] == "outcome" and e["kind"] == "phase_entered"]
    assert len(phases) == 1
    assert not any(e["xp"] == chapter.xp.first_agreement for e in db.list_events(sid))


def test_unfinished_reflection_does_not_reveal_hidden_conditions():
    sid = ready()
    negotiate.finish(sid)
    result = outcome.outcome(sid)
    shown = json.dumps(result).lower()
    assert "conference" not in shown
    assert "sunday" not in shown
    assert "staged" not in shown


def test_known_conditions_only_include_rejection_lines_actually_shown():
    sid = ready()
    first = negotiate.turn(sid, "Could I submit next Wednesday?", request_id="late-0001")
    assert first["decision"] == "constraint_change"
    negotiate.finish(sid)
    reflection = outcome.outcome(sid)["reflection"]
    assert first["reply"] in reflection["known_constraints"]
    assert "conference" in " ".join(reflection["known_constraints"])
    assert "sunday" not in " ".join(reflection["known_constraints"]).lower()
    negotiate.resume(sid)
    second = negotiate.turn(sid, "Could I submit next Wednesday?", request_id="late-0002")
    assert second["decision"] == "rejected"
    negotiate.finish(sid)
    reflection = outcome.outcome(sid)["reflection"]
    assert second["reply"] in reflection["known_constraints"]
    assert "6 pm on Sunday" in second["reply"]


def test_proposed_plan_is_not_presented_as_an_agreement_and_can_resume(chapter):
    sid = ready()
    propose(sid)
    before = session.load(sid)
    negotiate.finish(sid)
    result = outcome.outcome(sid)
    assert result["status"] == "not_agreed" and result["xp"] == chapter.xp.prep_complete
    assert "confirmation has not been recorded" in result["reflection"]["reason"]
    assert any("noon on Saturday" in step for step in result["reflection"]["next_steps"])
    assert session.load(sid)["negotiation"].get("language_notes") is not None
    resumed = negotiate.resume(sid)
    assert resumed["phase"] == "negotiate"
    after = session.load(sid)
    assert after["negotiation"]["attempt"] == before["negotiation"]["attempt"]
    assert after["negotiation"]["turns"] == before["negotiation"]["turns"]
    assert after["negotiation"]["agreement"] == before["negotiation"]["agreement"]
    assert after["prep"] == before["prep"]
    assert "language_notes" not in after["negotiation"]
    assert negotiate.resume(sid) == resumed
    assert sum(e["kind"] == "resumed" for e in db.list_events(sid)) == 1
    negotiate.confirm(sid)
    negotiate.finish(sid)
    success = outcome.outcome(sid)
    assert success["status"] == "agreed" and success["ending"]["id"] == "full"
    assert success["notes"] == ["Sat 12:00 · full report"]
    assert success["xp"] == chapter.xp.prep_complete + chapter.xp.first_agreement
    with pytest.raises(Conflict, match="already agreed"):
        negotiate.resume(sid)


def test_technical_failure_is_reported_separately_and_keeps_same_attempt(monkeypatch):
    sid = ready()
    assert fail_turn(monkeypatch, sid)["available"] is False
    negotiate.finish(sid)
    result = outcome.outcome(sid)
    assert result["turns"] == 0
    reflection = result["reflection"]
    assert reflection["technical_failures"] == 1
    assert "technical interruption" in reflection["reason"]
    assert "not an assessment of your English" in reflection["reason"]
    assert negotiate.resume(sid)["negotiation"]["attempt"] == 1


def test_a_failed_confirmation_does_not_blame_the_player(monkeypatch):
    sid = ready()
    propose(sid)
    fail_turn(monkeypatch, sid)
    negotiate.finish(sid)
    result = outcome.outcome(sid)
    assert result["status"] == "not_agreed"
    assert "confirmation has not been recorded" in result["reflection"]["reason"]
    assert "service problem" in result["reflection"]["reason"]
    assert result["reflection"]["technical_failures"] == 1


def test_paused_attempt_requires_new_attempt_and_archives_progress(chapter):
    sid = ready()
    for i in range(chapter.rules.max_turns):
        negotiate.turn(sid, "Let me think about the report.", request_id=f"pause-{i:04d}")
    before = session.load(sid)
    assert before["negotiation"]["paused"]
    negotiate.finish(sid)
    result = outcome.outcome(sid)
    assert "conversation limit" in result["reflection"]["reason"]
    with pytest.raises(Conflict, match="new attempt"):
        negotiate.resume(sid)
    retried = negotiate.retry(sid)
    assert retried["phase"] == "negotiate"
    assert retried["negotiation"]["attempt"] == 2
    assert retried["negotiation"]["turn_count"] == 0 and not retried["negotiation"]["paused"]
    assert retried["xp"] == chapter.xp.prep_complete
    stored = session.load(sid)
    assert stored["archived_attempts"][0]["turns"] == before["negotiation"]["turns"]
    assert stored["prep"] == before["prep"]


def test_old_attempt_failures_do_not_contaminate_new_reflection(monkeypatch):
    sid = ready()
    fail_turn(monkeypatch, sid)
    negotiate.finish(sid)
    assert outcome.outcome(sid)["reflection"]["technical_failures"] == 1
    negotiate.retry(sid)
    negotiate.finish(sid)
    reflection = outcome.outcome(sid)["reflection"]
    assert reflection["technical_failures"] == 0
    assert "technical interruption" not in reflection["reason"]


def test_finish_resume_and_retry_do_not_skip_preparation():
    sid, _ = session.create()
    for operation in (negotiate.finish, negotiate.resume, negotiate.retry):
        with pytest.raises(Conflict):
            operation(sid)


@pytest.mark.parametrize("reply", [None, "Hello, Alex. What do you need?"])
def test_failed_opening_is_not_cached_and_can_retry(monkeypatch, reply):
    sid = ready(start=False)
    with monkeypatch.context() as patch:
        patch.setattr(negotiate, "call_json", lambda *a, **k: LLMResult(
            data=None if reply is None else OpeningOut(reply=reply), model="test", prompt_version="test"))
        failed = negotiate.start(sid)
    assert failed["available"] is False
    neg = session.load(sid)["negotiation"]
    assert neg["opening"] is None and neg["turns"] == [] and neg["turn_count"] == 0
    assert sum(e["kind"] == "opening_failed" for e in db.list_events(sid)) == 1
    success = negotiate.start(sid)
    assert success["available"] is True
    assert len(success["negotiation"]["turns"]) == 1
    assert negotiate.start(sid) == success
    assert sum(e["kind"] == "opening" for e in db.list_events(sid)) == 1


def test_failed_opening_can_be_reviewed_as_a_technical_interruption(monkeypatch):
    sid = ready(start=False)
    monkeypatch.setattr(negotiate, "call_json", lambda *a, **k: LLMResult(
        data=None, model="test", prompt_version="test", errors=["test timeout"]))
    negotiate.start(sid)
    negotiate.finish(sid)
    result = outcome.outcome(sid)
    assert result["reflection"]["technical_failures"] == 1
    assert "technical interruption" in result["reflection"]["reason"]
    assert result["turns"] == 0


def test_opening_response_cannot_overwrite_a_finished_phase(monkeypatch):
    sid = ready(start=False)

    def finish_while_waiting(*args, **kwargs):
        negotiate.finish(sid)
        return LLMResult(data=OpeningOut(reply="In your email, you asked for more time."),
                         model="test", prompt_version="test")

    monkeypatch.setattr(negotiate, "call_json", finish_while_waiting)
    with pytest.raises(Conflict, match="over"):
        negotiate.start(sid)
    state = session.load(sid)
    assert state["phase"] == "outcome"
    assert state["negotiation"]["opening"] is None and state["negotiation"]["turns"] == []


def test_opening_response_cannot_overwrite_a_new_attempt(monkeypatch):
    sid = ready(start=False)

    def change_attempt_while_waiting(*args, **kwargs):
        state = session.load(sid)
        state["negotiation"] = session.new_negotiation()
        state["negotiation"]["attempt"] = 2
        db.save_state(sid, state)
        return LLMResult(data=OpeningOut(reply="In your email, you asked for more time."),
                         model="test", prompt_version="test")

    monkeypatch.setattr(negotiate, "call_json", change_attempt_while_waiting)
    with pytest.raises(Conflict, match="conversation changed"):
        negotiate.start(sid)
    state = session.load(sid)
    assert state["negotiation"]["attempt"] == 2
    assert state["negotiation"]["opening"] is None


def test_language_feedback_response_cannot_put_a_resumed_session_back_in_outcome(monkeypatch):
    sid = ready()
    negotiate.turn(sid, "I have been ill.", request_id="reason-0001")
    negotiate.finish(sid)

    def resume_while_waiting(*args, **kwargs):
        negotiate.resume(sid)
        return LLMResult(data=None, model="test", prompt_version="test")

    monkeypatch.setattr(outcome, "call_json", resume_while_waiting)
    with pytest.raises(Conflict, match="conversation changed"):
        outcome.outcome(sid)
    assert session.load(sid)["phase"] == "negotiate"


def test_language_feedback_failure_remains_retryable_and_success_is_cached(monkeypatch):
    sid = ready()
    negotiate.turn(sid, "I has been ill.", request_id="language-0001")
    negotiate.finish(sid)
    with monkeypatch.context() as patch:
        patch.setattr(outcome, "call_json", lambda *a, **k: LLMResult(
            data=None, model="test", prompt_version="test", errors=["test timeout"]))
        for _ in range(2):
            failed = outcome.outcome(sid)
            assert failed["language_available"] is False and failed["language"] == []
            assert session.load(sid)["negotiation"].get("language_notes") is None
            assert failed["reflection"]["technical_failures"] == 0  # feedback is separate from exchanges
    success = outcome.outcome(sid)
    assert success["language_available"] is True and success["language"]
    cached = outcome.outcome(sid)
    assert cached["language_available"] is True and cached["language"] == success["language"]
    notes_events = [e for e in db.list_events(sid) if e["kind"] == "language_notes"]
    assert [e["rule_decision"] for e in notes_events] == ["model_failed", "model_failed", "ok"]


def test_overlong_turn_is_rejected_before_trimming_calling_model_or_logging(monkeypatch):
    sid = ready()
    before = session.load(sid)
    events_before = db.list_events(sid)
    monkeypatch.setattr(negotiate, "call_json", lambda *a, **k: pytest.fail("model should not be called"))
    with pytest.raises(InputTooLong):
        negotiate.turn(sid, "a" * MAX_TURN_CHARS + " ", request_id="length-0001")
    assert session.load(sid) == before
    assert db.list_events(sid) == events_before
