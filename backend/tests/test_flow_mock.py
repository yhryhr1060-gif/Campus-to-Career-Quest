"""The whole chapter in mock mode: no API key needed."""
import pytest

from app import db
from app.services import negotiate, outcome, prepare, session
from app.services.session import Conflict

EMAIL = ("Dear Prof. Chen,\n\nUnfortunately, I have been sick for three days, so I haven't been able to finish "
         "the project report by Friday. Would it be possible to hand it in next Wednesday?\n\nThanks,\nAlex")


def prepared():
    sid, _ = session.create()
    session.view_hotspot(sid, "syllabus")
    prepare.get_feedback(sid, EMAIL)
    out = prepare.send_email(sid, EMAIL)
    fields = {f["field"] for f in out["prep"]["summary"]}
    assert {"situation", "proposal"} <= fields
    prepare.confirm_summary(sid)
    out = prepare.submit_prediction(sid, "He might say Wednesday is too late.", "Outline first, then the rest.")
    assert out["phase"] == "negotiate" and out["xp"] == 20
    return sid


def say(sid, text, n=[0]):
    n[0] += 1
    return negotiate.turn(sid, text, request_id=f"req-{n[0]:06d}")


def test_staged_ending_end_to_end():
    sid = prepared()
    out = negotiate.start(sid)
    opening = out["negotiation"]["turns"][0]["text"]
    assert "In your email" in opening and "next Wednesday" in opening
    assert len(out["negotiation"]["key_points"]) <= 3

    # Walkthrough from the design (§4.9): "yes" to "you asked for next Wednesday — is that right?"
    out = say(sid, "Yes. Unfortunately, I've been sick since Monday, so I haven't been able to work on it.")
    assert out["decision"] == "constraint_change" and "conference" in out["reply"]
    assert out["negotiation"]["agreement"]["status"] == "none"
    assert out["negotiation"]["hints_unlocked"]

    out = say(sid, "Um... I... maybe...")
    assert out["negotiation"]["agreement"]["status"] == "none"
    assert "another time" not in out["reply"]                    # hesitation is not off-topic

    out = negotiate.hint(sid)
    assert out["negotiation"]["hints"][0]["text"].startswith("Offer an earlier plan")

    out = say(sid, "I could send you the outline by Friday and the full report by Sunday evening.")
    assert out["decision"] == "restate"
    assert "6 pm on Friday" in out["reply"] and "6 pm on Sunday" in out["reply"]
    assert out["negotiation"]["agreement"]["status"] == "proposed"

    out = say(sid, "Yes, that's right. I'll make sure they're on time.")
    assert out["decision"] == "confirmed"
    assert out["negotiation"]["agreement"]["status"] == "confirmed"
    assert out["xp"] == 50

    negotiate.finish(sid)
    res = outcome.outcome(sid)
    assert res["ending"]["id"] == "staged"
    assert res["notes"] == ["Fri 18:00 · outline", "Sun 18:00 · full report"]
    assert res["ending"]["confirm_quote"] == "Yes, that's right. I'll make sure they're on time."
    assert res["other_ending"] == {"id": "full", "face_down": True, "text": "There is another ending."}
    assert 1 <= len(res["strategy"]) <= 2 and res["strategy"][0]["quote"]
    assert res["xp"] == 50


def test_full_ending_with_confirm_button():
    sid = prepared()
    negotiate.start(sid)
    say(sid, "I'm sorry, I've been ill.")                        # Prof. Chen asks a follow-up
    out = say(sid, "I could finish the whole report by Saturday at noon.")
    assert out["decision"] == "restate" and "noon on Saturday" in out["reply"]
    out = negotiate.confirm(sid)
    assert out["decision"] == "confirmed"
    negotiate.finish(sid)
    res = outcome.outcome(sid)
    assert res["ending"]["id"] == "full" and res["notes"] == ["Sat 12:00 · full report"]


def test_same_request_id_is_processed_once():
    sid = prepared()
    negotiate.start(sid)
    a = negotiate.turn(sid, "I've been ill.", request_id="dup-000001")
    b = negotiate.turn(sid, "I've been ill.", request_id="dup-000001")
    assert a == b and b["negotiation"]["turn_count"] == 1


def test_pause_after_twelve_turns_and_retry():
    sid = prepared()
    negotiate.start(sid)
    for i in range(12):
        out = negotiate.turn(sid, "Hmm, let me think about the report.", request_id=f"pause-{i:04d}")
    assert out["negotiation"]["paused"] and "pause" in out["reply"].lower()
    with pytest.raises(Conflict):
        negotiate.turn(sid, "One more?", request_id="pause-9999")
    out = negotiate.retry(sid)
    assert out["negotiation"]["attempt"] == 2 and out["negotiation"]["turn_count"] == 0
    assert out["negotiation"]["turns"][0]["kind"] == "opening"
    assert out["xp"] == 20                                       # retrying never removes or repeats XP


def test_phases_cannot_be_skipped():
    sid, _ = session.create()
    with pytest.raises(Conflict):
        negotiate.start(sid)
    with pytest.raises(Conflict):
        prepare.submit_prediction(sid, "", "")
    with pytest.raises(Conflict):
        outcome.outcome(sid)


def test_events_are_logged_with_model_and_prompt_version():
    sid = prepared()
    negotiate.start(sid)
    say(sid, "Could I hand it in next Wednesday?")
    events = db.list_events(sid)
    kinds = [e["kind"] for e in events]
    assert {"session_created", "hotspot_viewed", "feedback", "email_sent", "prediction", "opening", "turn"} <= set(kinds)
    turn = [e for e in events if e["kind"] == "turn"][-1]
    assert turn["model"] == "mock" and turn["prompt_version"].startswith("npc_system@")
    assert turn["rule_decision"] == "constraint_change" and turn["raw_output"]


def test_private_info_never_reaches_the_browser(chapter):
    view = chapter.public_view()
    text = str(view)
    assert "conference" not in text and "Sunday 18:00" not in text and "SUN 18:00" not in text
