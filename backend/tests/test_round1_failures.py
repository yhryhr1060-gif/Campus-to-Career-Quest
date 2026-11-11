"""Technical failures remain visible and retryable without becoming learner performance."""
import json

import pytest

from app import db
from app.llm import client
from app.services import negotiate, prepare, session


EMAIL = ("Dear Prof. Chen, I have been ill for three days. "
         "Could I submit the report next Wednesday? Thanks, Alex")


def ready():
    sid, _ = session.create()
    prepare.send_email(sid, EMAIL)
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "He may ask for an earlier date.", "Send an outline first.")
    negotiate.start(sid)
    return sid


def fail_transport(_messages):
    raise TimeoutError("test provider unavailable")


def test_feedback_unavailable_is_distinct_from_no_suggestions(monkeypatch):
    sid, _ = session.create()
    monkeypatch.setenv("LLM_MOCK", "0")
    monkeypatch.setattr(client, "http_transport", fail_transport)
    failed = prepare.get_feedback(sid, EMAIL)
    assert (failed["items"], failed["available"]) == ([], False)
    assert session.load(sid)["prep"]["draft"] == EMAIL

    monkeypatch.setattr(client, "http_transport", lambda _: '{"items": []}')
    checked = prepare.get_feedback(sid, EMAIL)
    assert (checked["items"], checked["available"]) == ([], True)
    events = [e for e in db.list_events(sid) if e["kind"] == "feedback"]
    assert [e["rule_decision"] for e in events] == ["model_failed", "ok"]


@pytest.mark.parametrize("failure", ["timeout", "invalid_json"])
@pytest.mark.parametrize("boundary", ["first", "before_nudge", "before_pause"])
def test_failed_turn_does_not_change_state_or_use_a_turn(monkeypatch, chapter, failure, boundary):
    sid = ready()
    before = session.load(sid)
    before["negotiation"]["turn_count"] = {
        "first": 0,
        "before_nudge": chapter.rules.nudge_at_turn - 1,
        "before_pause": chapter.rules.max_turns - 1,
    }[boundary]
    db.save_state(sid, before)
    monkeypatch.setenv("LLM_MOCK", "0")
    monkeypatch.setattr(client, "http_transport", fail_transport if failure == "timeout" else lambda _: "{bad")

    text = "Could I hand in the report by Saturday at noon?"
    out = negotiate.turn(sid, text, request_id="failed-0001")

    assert out["available"] is False
    assert out["decision"] == "model_failed"
    assert out["reply"] == chapter.line("neutral_fallback")
    assert not out["negotiation"]["paused"]
    assert out["negotiation"]["turn_count"] == before["negotiation"]["turn_count"]
    assert out["negotiation"]["turns"] == before["negotiation"]["turns"]
    assert session.load(sid) == before
    assert db.get_processed(sid, "failed-0001") is None
    assert out["xp"] == chapter.xp.prep_complete

    event = db.list_events(sid)[-1]
    assert event["kind"] == "turn_failed" and event["rule_decision"] == "model_failed"
    assert event["player_text"] == text and event["raw_output"]
    payload = json.loads(event["payload_json"])
    assert payload["request_id"] == "failed-0001" and payload["counted"] is False
    assert len(payload["latency_ms"]) == 2


def test_same_failed_request_can_retry_then_be_processed_once(monkeypatch):
    sid = ready()
    original_turns = session.load(sid)["negotiation"]["turns"]
    text = "I have been ill and need more time."
    monkeypatch.setenv("LLM_MOCK", "0")
    monkeypatch.setattr(client, "http_transport", fail_transport)
    for _ in range(2):
        failed = negotiate.turn(sid, text, request_id="retry-0001")
        assert failed["available"] is False and failed["negotiation"]["turn_count"] == 0

    monkeypatch.setenv("LLM_MOCK", "1")
    success = negotiate.turn(sid, text, request_id="retry-0001")
    repeated = negotiate.turn(sid, text, request_id="retry-0001")
    assert success["available"] is True and repeated == success
    assert success["negotiation"]["turn_count"] == 1
    assert success["negotiation"]["turns"][:len(original_turns)] == original_turns
    assert len(success["negotiation"]["turns"]) == len(original_turns) + 2
    assert sum(t["speaker"] == "player" for t in success["negotiation"]["turns"]) == 1
    events = db.list_events(sid)
    assert sum(e["kind"] == "turn_failed" for e in events) == 2
    assert sum(e["kind"] == "turn" for e in events) == 1
    counted = json.loads(next(e["payload_json"] for e in events if e["kind"] == "turn"))
    assert counted["request_id"] == "retry-0001" and counted["counted"] is True


def test_npc_history_uses_json_and_preserves_each_original_line(chapter):
    sid = ready()
    negotiate.turn(sid, "I have been ill.", request_id="history-0001")
    state = session.load(sid)
    messages = negotiate._messages(chapter, state, "Could we discuss another date?")
    history = state["negotiation"]["turns"]
    assert len(messages) == len(history) + 2
    for message, turn in zip(messages[1:-1], history):
        if turn["speaker"] == "npc":
            assert message["role"] == "assistant"
            parsed = json.loads(message["content"])
            assert parsed == {"reply": turn["text"], "mood": turn.get("mood", "neutral"),
                              "candidate_facts": []}
        else:
            assert message == {"role": "user", "content": turn["text"]}
    assert messages[-1] == {"role": "user", "content": "Could we discuss another date?"}
