"""Voice provenance stays descriptive; raw ASR never enters exported records."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import export, negotiate, prepare, session


def ready():
    sid, _ = session.create()
    prepare.send_email(sid, "Dear Prof. Chen, I have been ill for three days. Could I submit next Wednesday?")
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "He may ask for a plan.", "Offer an outline first.")
    negotiate.start(sid)
    return sid


@pytest.mark.parametrize("include_text", [False, True])
@pytest.mark.parametrize("mode", ["voice", "voice_edited"])
def test_raw_asr_is_never_exported_even_when_writing_is_included(include_text, mode):
    sid = ready()
    asr = "PRIVATE_UNSENT_ASR_SOURCE"
    reply = "Could I submit next Wednesday?"
    negotiate.turn(sid, reply, request_id="speech-test", input_mode=mode, asr_text=asr)
    archive, _ = export.make_archive(sid, include_text=include_text)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        assert all(asr.encode() not in zipped.read(name) for name in zipped.namelist())
        record = json.loads(zipped.read("record.json"))
    turn = next(event for event in record["events"] if event["kind"] == "turn")
    assert turn["reported_input_mode"] == mode
    assert record["summary"]["completed_learner_turns"] == 1
    if include_text:
        assert turn["player_text"] == reply
    else:
        assert "player_text" not in turn


def test_speech_events_are_separate_from_llm_and_learner_turn_counts():
    sid = ready()
    before = export.build_record(sid)["summary"]
    for kind, error in [("speech_transcribed", None), ("speech_failed", "audio_silent")]:
        db.log_event(sid, "negotiate", kind, model="nemo-parakeet-tdt-0.6b-v2", rule_decision=error or "ok",
                     payload={"attempt": 1, "execution_mode": "local", "backend": "parakeet",
                              "duration_seconds": 1.25, "latency_ms": 75, "transcript_chars": 42,
                              "error_code": error, "transcript": "NOT_FOR_EXPORT", "audio": "PRIVATE_AUDIO"})
    record = export.build_record(sid)
    assert record["summary"]["model_operations_by_mode"] == before["model_operations_by_mode"]
    assert record["summary"]["completed_learner_turns"] == before["completed_learner_turns"]
    assert record["summary"]["technical_failures"] == before["technical_failures"]
    assert record["summary"]["speech_transcriptions"] == record["summary"]["speech_failures"] == 1
    assert record["attempts"][0]["speech_transcriptions"] == record["attempts"][0]["speech_failures"] == 1
    assert not record["data_quality_warnings"]
    events = record["events"][-2:]
    assert all(e["model_mode"] == "not_applicable" and e["speech_mode"] == "local" for e in events)
    assert events[0]["speech_duration_seconds"] == 1.25
    assert events[1]["speech_error_code"] == "audio_silent"
    assert "NOT_FOR_EXPORT" not in json.dumps(record) and "PRIVATE_AUDIO" not in json.dumps(record)


def test_malformed_speech_metadata_is_withheld():
    sid = ready()
    db.log_event(sid, "negotiate", "speech_failed", payload={"attempt": 1, "backend": "private/path",
                 "execution_mode": "cloud", "duration_seconds": float("nan"), "latency_ms": -1,
                 "transcript_chars": True, "error_code": "secret provider error"})
    event = export.build_record(sid)["events"][-1]
    assert all(event[key] is None for key in ["speech_mode", "speech_backend", "speech_duration_seconds",
                                             "speech_latency_ms", "speech_transcript_characters", "speech_error_code"])


def test_original_transcript_has_separate_bounded_limit():
    sid = ready()
    client = TestClient(app)
    before = session.load(sid)
    rejected = client.post(f"/api/sessions/{sid}/negotiate/turn", json={
        "text": "Could I submit next Wednesday?", "request_id": "source-limit", "input_mode": "voice_edited", "asr_text": "x" * 6001})
    assert rejected.status_code == 422
    assert session.load(sid) == before
    accepted = client.post(f"/api/sessions/{sid}/negotiate/turn", json={
        "text": "Could I submit next Wednesday?", "request_id": "source-limit", "input_mode": "voice_edited", "asr_text": "x" * 601})
    assert accepted.status_code == 200
