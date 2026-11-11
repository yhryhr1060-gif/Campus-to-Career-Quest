"""Saving and input-boundary contracts for the second usability increment."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.limits import INPUT_LIMITS, InputTooLong
from app.main import app
from app.services import prepare, session, negotiate, writing_feedback
from app.services.session import Conflict

EMAIL = "Dear Prof. Chen, I have been ill. Could I send the report next Wednesday? Thanks, Alex"


def before_prediction():
    sid, _ = session.create()
    prepare.send_email(sid, EMAIL)
    prepare.confirm_summary(sid)
    return sid


def test_draft_autosave_preserves_phase_notes_and_empty_text():
    sid, _ = session.create()
    session.view_hotspot(sid, "syllabus")
    prepare.save_draft(sid, "a draft")
    saved = prepare.save_draft(sid, "")
    assert saved["phase"] == "explore" and saved["notes"] == ["syllabus"]
    assert session.load(sid)["prep"]["draft"] == ""
    assert saved["xp"] == 0


def test_prediction_autosave_does_not_enter_office_or_award_xp():
    sid = before_prediction()
    saved = prepare.save_prediction(sid, "He might say no.", "An earlier date.")
    assert saved["phase"] == "prepare" and not saved["prep"]["done"]
    assert saved["xp"] == 0
    reloaded = session.public_state(sid, session.load(sid))
    assert reloaded["prep"]["prediction"] == "He might say no."
    assert reloaded["prep"]["backup_plan"] == "An earlier date."
    prepare.submit_prediction(sid, "He might say no.", "An earlier date.")
    before = session.load(sid)
    with pytest.raises(Conflict):
        prepare.save_prediction(sid, "late old request", "late old request")
    assert session.load(sid) == before


def test_stale_draft_cannot_replace_sent_email():
    sid, _ = session.create()
    prepare.send_email(sid, EMAIL)
    before = session.load(sid)
    with pytest.raises(Conflict):
        prepare.save_draft(sid, "old draft")
    assert session.load(sid) == before


def test_prediction_cannot_be_saved_before_summary_confirmation():
    sid, _ = session.create()
    with pytest.raises(Conflict):
        prepare.save_prediction(sid, "anything", "anything")


def test_feedback_finishing_after_send_cannot_reopen_editor(monkeypatch):
    sid, _ = session.create()
    original = writing_feedback.call_json
    def late_feedback(*args, **kwargs):
        state = session.load(sid)
        state["prep"].update(email_final=EMAIL, draft=EMAIL, summary=[])
        state["phase"] = "prepare"
        db.save_state(sid, state)
        return original(*args, **kwargs)
    monkeypatch.setattr(writing_feedback, "call_json", late_feedback)
    with pytest.raises(Conflict):
        prepare.get_feedback(sid, "an older draft")
    state = session.load(sid)
    assert state["prep"]["draft"] == state["prep"]["email_final"] == EMAIL


def test_delayed_feedback_preserves_a_newer_saved_draft(monkeypatch):
    sid, _ = session.create()
    earlier = "Dear Prof. Chen, I has been ill. Could I submit next Wednesday? Thanks, Alex"
    newer = "Dear Prof. Chen, I have been ill. I could submit the full report on Sunday. Thanks, Alex"
    prepare.save_draft(sid, earlier)
    original = writing_feedback.call_json

    def feedback_finishes_after_edit(*args, **kwargs):
        # The learner left the busy page, returned, and saved a new revision while the model ran.
        session.set_phase(sid, "prepare")
        prepare.save_draft(sid, newer)
        return original(*args, **kwargs)

    monkeypatch.setattr(writing_feedback, "call_json", feedback_finishes_after_edit)
    result = prepare.get_feedback(sid, earlier)
    state = session.load(sid)
    assert result["available"]
    assert state["prep"]["draft"] == newer
    assert state["phase"] == "prepare" and state["prep"]["feedback_requests"] == 1
    logged = [e for e in db.list_events(sid) if e["kind"] == "feedback"]
    assert logged[-1]["player_text"] == earlier


@pytest.mark.parametrize("operation", [prepare.save_draft, prepare.get_feedback, prepare.send_email])
def test_email_limit_rejects_entire_request_without_truncation(operation):
    sid, _ = session.create()
    before = session.load(sid)
    with pytest.raises(InputTooLong):
        operation(sid, "x" * 2000 + "I cannot agree.")
    assert session.load(sid) == before


@pytest.mark.parametrize("operation", [prepare.save_prediction, prepare.submit_prediction])
@pytest.mark.parametrize("field", ["prediction", "backup_plan"])
def test_prediction_limits_leave_state_unchanged(operation, field):
    sid = before_prediction()
    before = session.load(sid)
    values = {"prediction": "plan", "backup_plan": "backup", field: "x" * 501}
    with pytest.raises(InputTooLong):
        operation(sid, **values)
    assert session.load(sid) == before


def test_exact_limit_preserves_unicode_code_points():
    sid = before_prediction()
    text = "😀" * 500
    prepare.save_prediction(sid, text, text)
    assert session.load(sid)["prep"]["prediction"] == text
    sid2, _ = session.create()
    prepare.save_draft(sid2, "😀" * 2000)
    assert len(session.load(sid2)["prep"]["draft"]) == 2000


def test_turn_limit_rejects_before_model_or_state_changes(monkeypatch):
    sid = before_prediction()
    prepare.submit_prediction(sid, "", "")
    negotiate.start(sid)
    before = session.load(sid)
    def forbidden(*args, **kwargs):
        raise AssertionError("overlong input must not reach model")
    monkeypatch.setattr(negotiate, "call_json", forbidden)
    with pytest.raises(InputTooLong):
        negotiate.turn(sid, "x" * 600 + " I cannot agree.", request_id="length-test-001")
    assert session.load(sid) == before


def test_api_and_public_content_use_the_same_limits():
    with TestClient(app) as client:
        assert client.get("/api/content/chapter2").json()["input_limits"] == INPUT_LIMITS
        sid = client.post("/api/sessions").json()["session_id"]
        base = f"/api/sessions/{sid}"
        for method, route, body in [
            ("PUT", "/prep/draft", {"draft": "x" * 2001}),
            ("POST", "/prep/feedback", {"draft": "x" * 2001}),
            ("POST", "/prep/send", {"email": "x" * 2001}),
            ("PUT", "/prep/prediction", {"prediction": "x" * 501}),
            ("POST", "/prep/prediction", {"backup_plan": "x" * 501}),
            ("POST", "/negotiate/turn", {"text": "x" * 601, "request_id": "length-002"}),
        ]:
            response = client.request(method, base + route, json=body)
            assert response.status_code == 422, (route, response.text)
        assert session.load(sid)["prep"]["draft"] == ""


def test_prediction_put_route_only_saves():
    sid = before_prediction()
    with TestClient(app) as client:
        response = client.put(f"/api/sessions/{sid}/prep/prediction", json={"prediction": "guess", "backup_plan": "backup"})
    assert response.status_code == 200
    assert response.json()["phase"] == "prepare" and response.json()["xp"] == 0
