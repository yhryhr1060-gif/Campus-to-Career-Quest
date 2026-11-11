"""Milestone 2: reply guard, chapter-end language notes, latency logging."""
import json

from app import db
from app.llm import client, mock
from app.rules.reply_guard import sounds_like_acceptance
from app.services import negotiate, outcome, prepare, session


def test_reply_guard_spots_acceptance_of_a_time():
    assert sounds_like_acceptance("Okay then, Sunday at 6 pm is fine.")
    assert sounds_like_acceptance("Deal — you can submit it on Saturday.")
    assert not sounds_like_acceptance("Friday is the deadline. What could you manage?")
    assert not sounds_like_acceptance("That works for me in principle, but what exactly would you send?")


def _ready():
    sid, _ = session.create()
    prepare.send_email(sid, "Dear Prof. Chen, I have been ill for three days. "
                            "Would it be possible to hand in the report next Wednesday? Thanks, Alex")
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "", "")
    negotiate.start(sid)
    return sid


def test_model_cannot_accept_a_plan_by_itself(monkeypatch):
    sid = _ready()
    monkeypatch.setenv("LLM_MOCK", "0")
    monkeypatch.setattr(client, "http_transport", lambda msgs: json.dumps(
        {"reply": "Okay then, Sunday at 6 pm is fine.", "mood": "warm", "candidate_facts": []}))
    out = negotiate.turn(sid, "Could you give me more time?", request_id="guard-0001")
    assert "fine" not in out["reply"] and "make sure I understand" in out["reply"]
    assert out["decision"].endswith("+reply_guarded")
    assert out["negotiation"]["agreement"]["status"] == "none"
    turn = [e for e in db.list_events(sid) if e["kind"] == "turn"][-1]
    payload = json.loads(turn["payload_json"])
    assert payload["model_reply"] == "Okay then, Sunday at 6 pm is fine."
    assert isinstance(payload["latency_ms"], list) and payload["latency_ms"]


def _play_to_outcome(sid, lines):
    for i, t in enumerate(lines):
        negotiate.turn(sid, t, request_id=f"lang-{i:04d}")
    negotiate.confirm(sid)
    negotiate.finish(sid)


def test_language_notes_quote_the_learners_own_words():
    sid = _ready()
    _play_to_outcome(sid, ["i want to next monday to give you my plan",
                           "I could send the outline by Friday and the full report by Sunday."])
    res = outcome.outcome(sid)
    assert res["language"], res
    first = res["language"][0]
    assert first["category"] == "error" and first["quote"] in first["line"]
    again = outcome.outcome(sid)                      # generated once, then cached
    assert again["language"] == res["language"]
    assert sum(e["kind"] == "language_notes" for e in db.list_events(sid)) == 1


def test_fabricated_language_note_is_dropped(monkeypatch):
    sid = _ready()
    monkeypatch.setattr(mock, "mock_turn_feedback", lambda lines: {"items": [
        {"category": "error", "quote": "I has finished nothing", "suggestion": "x", "reason": "y"},
        {"category": "positive", "quote": "I could send the outline by Friday", "suggestion": "", "reason": "good"},
    ]})
    _play_to_outcome(sid, ["I've been ill.", "I could send the outline by Friday and the full report by Sunday."])
    res = outcome.outcome(sid)
    assert [n["quote"] for n in res["language"]] == ["I could send the outline by Friday"]
