"""Conservative legacy interpretation and export privacy at malformed-data boundaries."""
import copy
import csv
import io
import json
import zipfile

import pytest

from app.services import export

SID = "S-012345ABCDEF"
STAMP = "2026-11-08T12:00:00+00:00"


def negotiation(attempt=1):
    return {"attempt": attempt, "turn_count": 0, "turns": [], "paused": False,
            "agreement": {"status": "none", "confirmed": None}}


def event(kind="turn", *, phase="negotiate", payload=None, raw=None, **extra):
    return {"id": 9988, "session_id": SID, "ts": STAMP, "phase": phase, "kind": kind,
            "rule_decision": "none", "model": "mock", "prompt_version": "npc_system@0.3",
            "payload_json": json.dumps({"attempt": 1, "counted": True, "input_mode": "typed"} if payload is None else payload),
            "raw_output": json.dumps({"execution_mode": "mock", "latency_ms": [3], "errors": []} if raw is None else raw),
            **extra}


def snapshot(events=None):
    return {"created_at": STAMP, "xp": 0, "state": {
        "chapter_id": "chapter2", "chapter_version": "0.1.0", "phase": "negotiate",
        "prep": {"done": False, "summary_confirmed": False, "draft": "", "email_final": None},
        "negotiation": negotiation(), "archived_attempts": [],
    }, "events": [event("session_created", phase="session", payload={}, raw={}, model=None)] + (events or [])}


def bind(monkeypatch, data):
    monkeypatch.setattr(export.db, "read_session_snapshot", lambda _sid: copy.deepcopy(data))


def warning_codes(record):
    return {warning["code"] for warning in record["data_quality_warnings"]}


@pytest.mark.parametrize("field,value", [
    ("phase", []), ("kind", {}), ("rule_decision", []),
])
def test_malformed_event_enums_do_not_break_export(monkeypatch, field, value):
    data = snapshot([event(**{field: value})])
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert record["events"][-1]["rule_decision"] == "unknown" if field == "rule_decision" else (
        record["events"][-1][field] == "unknown")


@pytest.mark.parametrize("value", [[], {}, True, 1])
def test_malformed_transport_and_input_modes_stay_unknown(monkeypatch, value):
    data = snapshot([event(payload={"attempt": 1, "counted": True, "input_mode": value},
                           raw={"execution_mode": value})])
    bind(monkeypatch, data)
    row = export.build_record(SID)["events"][-1]
    assert row["model_mode"] == "unknown"
    assert row["model_mode_source"] == "invalid_transport_metadata"
    assert row["reported_input_mode"] is None


@pytest.mark.parametrize("value", [None, "broken-json", "[]", "true", "null"])
def test_unreadable_payloads_are_not_guessed_as_counted(monkeypatch, value):
    data = snapshot([event(payload_json=value, raw_output=value)])
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert record["summary"]["completed_learner_turns"] == 0
    assert record["summary"]["unclassified_legacy_turns"] == 1
    assert "legacy_turn_counting_unknown" in warning_codes(record)


@pytest.mark.parametrize("counted", [1, "true", [], {}])
def test_counting_requires_a_json_boolean(monkeypatch, counted):
    bind(monkeypatch, snapshot([event(payload={"attempt": 1, "counted": counted})]))
    record = export.build_record(SID)
    assert record["summary"]["completed_learner_turns"] == 0
    assert record["summary"]["unclassified_legacy_turns"] == 1


def test_legacy_failures_and_missing_flags_do_not_inflate_effective_turns(monkeypatch):
    data = snapshot([
        event(payload={"turn_no": 1}, raw={}, rule_decision="none", model="old-provider"),
        event(payload={"turn_no": 2}, raw={"latency_ms": [10, 12], "errors": ["x", "y"]},
              rule_decision="model_failed+paused"),
        event(payload={"attempt": 1, "counted": True}),
    ])
    data["state"]["negotiation"].update(turn_count=3, turns=[{"speaker": "player", "text": "x"}] * 3)
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert record["summary"]["completed_learner_turns"] == 1
    assert record["summary"]["unclassified_legacy_turns"] == 1
    assert record["summary"]["technical_failures"] == 1
    assert record["events"][2]["model_attempts"] == 2
    assert record["events"][1]["model_mode"] == "unknown"
    assert record["events"][2]["model_mode_source"] == "legacy_model_label"
    assert record["attempts"][0]["state_turn_count"] == 3
    assert "turn_counts_need_review" in warning_codes(record)


def test_legacy_opening_fallback_is_separate_from_normal_model_results(monkeypatch):
    data = snapshot([event("opening", payload={}, raw={}, rule_decision="fallback_opening")])
    bind(monkeypatch, data)
    record = export.build_record(SID)
    row = record["events"][-1]
    assert row["rule_decision"] == "fallback_opening" and row["technical_failure"] is True
    assert row["model_mode"] == "mock" and row["model_mode_source"] == "legacy_model_label"
    assert record["summary"]["technical_failures"] == 1


def test_missing_and_wrongly_typed_state_flags_remain_null(monkeypatch):
    data = snapshot([event(payload={"attempt": True, "counted": True})])
    data["state"]["phase"] = ["prepare"]
    data["state"]["prep"] = {"done": 1, "summary_confirmed": "true"}
    data["state"]["negotiation"] = {"attempt": True, "paused": 0}
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert record["summary"]["current_phase"] == "unknown"
    assert record["preparation"]["completed"] is None
    assert record["preparation"]["summary_confirmed"] is None
    assert record["events"][-1]["attempt"] is None
    row = record["attempts"][0]
    assert row["attempt"] is None and row["paused"] is None
    assert row["completed_learner_turns"] is None and row["recorded_player_lines"] is None
    assert row["activity_observed"] is None


def test_explicit_attempt_conflict_does_not_assign_later_events_to_old_attempt(monkeypatch):
    data = snapshot([
        event(payload={"attempt": 2, "counted": True}),
        event("hint_opened", payload={"turn_no": 1}, raw={}, model=None, hint_level=1),
    ])
    data["state"]["archived_attempts"] = [negotiation(1)]
    data["state"]["negotiation"] = negotiation(2)
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert record["events"][1]["attempt"] == 2
    assert record["events"][2]["attempt"] is None
    assert record["attempts"][0]["hint_requests"] == 0
    assert "attempt_sequence_conflict" in warning_codes(record)


def test_missing_retry_number_leaves_following_legacy_events_unassigned(monkeypatch):
    data = snapshot([
        event("retry", payload={}, raw={}, model=None),
        event("hint_opened", payload={}, raw={}, model=None, hint_level=1),
    ])
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert all(row["attempt"] is None for row in record["events"][1:])
    assert "attempt_boundary_ambiguous" in warning_codes(record)


def test_late_creation_record_does_not_backfill_prior_events(monkeypatch):
    data = snapshot()
    data["events"].insert(0, event(payload={}))
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert record["events"][0]["attempt"] is None
    assert "attempt_boundary_ambiguous" in warning_codes(record)


def test_duplicate_attempt_snapshots_do_not_double_attribute_events(monkeypatch):
    data = snapshot([event()])
    data["state"]["archived_attempts"] = [negotiation(1)]
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert record["summary"]["completed_learner_turns"] == 1
    assert all(row["completed_learner_turns"] is None for row in record["attempts"])
    assert "attempt_number_duplicate" in warning_codes(record)


@pytest.mark.parametrize("value", [[], None, "old-corrupt-state"])
def test_invalid_state_does_not_fabricate_an_attempt(monkeypatch, value):
    data = snapshot()
    data["state"] = value
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert record["attempts"] == [] and record["summary"]["attempts_recorded"] == 0
    assert "state_snapshot_invalid" in warning_codes(record)


def test_unknown_hint_levels_are_not_reported_as_zero(monkeypatch):
    bind(monkeypatch, snapshot([event("hint_opened", payload={}, raw={}, model=None, hint_level=True)]))
    record = export.build_record(SID)
    row = record["attempts"][0]
    assert row["hint_requests"] == 1
    assert row["highest_hint_level"] is None and row["distinct_hint_levels"] is None
    assert "hint_level_unknown" in warning_codes(record)


def test_non_model_events_cannot_inflate_model_operations(monkeypatch):
    bind(monkeypatch, snapshot([event("hint_opened", raw={"execution_mode": "live", "latency_ms": [1]})]))
    record = export.build_record(SID)
    assert record["summary"]["model_operations_by_mode"] == {}
    assert record["events"][-1]["model_attempts"] is None


def test_contradictory_confirmation_metadata_is_not_counted_as_confirmation(monkeypatch):
    data = snapshot([event("confirm_button", payload={"kind": "rejected", "became_confirmed": True},
                           raw={}, rule_decision="confirmed", model=None)])
    bind(monkeypatch, data)
    assert export.build_record(SID)["events"][-1]["confirmation_method"] is None


def private_snapshot():
    allowed = "LEARNER_VISIBLE_SENTINEL"
    forbidden = "HIDDEN_PROVIDER_SENTINEL"
    data = snapshot([event(player_text=allowed, audio_path=forbidden, asr_text=forbidden,
        payload={"attempt": 1, "counted": True, "reply": allowed, "model_reply": forbidden,
                 "request_id": forbidden, "decision": {"dropped": [{"fact": {"quote": forbidden}}]},
                 "extra": {"secret": forbidden}},
        raw={"execution_mode": "live", "raw": [forbidden], "errors": [forbidden], "latency_ms": [12]})])
    data["state"]["prep"].update(draft=allowed, email_final=allowed, prediction=allowed, backup_plan=allowed,
                                 summary=[{"quote": forbidden, "value": forbidden}])
    data["state"]["negotiation"]["turns"] = [{"speaker": "player", "text": allowed, "raw": forbidden}]
    data["state"]["negotiation"]["agreement"]["confirm_quote"] = forbidden
    data["state"]["extra_secret"] = forbidden
    data["events"].append(event("outcome_shown", phase="outcome", payload={"language": [{"quote": forbidden}],
        "strategy": [{"quote": forbidden}], "reflection": {"reason": forbidden}}, raw={}, model=None))
    return data, allowed, forbidden


@pytest.mark.parametrize("include_text", [False, True])
def test_every_archive_file_obeys_the_text_allowlist(monkeypatch, include_text):
    data, allowed, forbidden = private_snapshot()
    bind(monkeypatch, data)
    blob, filename = export.make_archive(SID, include_text=include_text)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for name in archive.namelist():
            text = archive.read(name).decode("utf-8-sig")
            assert forbidden not in text and SID not in text
            if not include_text:
                assert allowed not in text
        record = json.loads(archive.read("record.json"))
    assert SID not in filename and forbidden not in filename
    assert record["includes_text"] is include_text
    assert (allowed in json.dumps(record)) is include_text


def test_text_export_requires_explicit_boolean_opt_in(monkeypatch):
    data, allowed, _ = private_snapshot()
    bind(monkeypatch, data)
    record = export.build_record(SID, include_text="false")
    assert record["includes_text"] is False and allowed not in json.dumps(record)


@pytest.mark.parametrize("text", ["=SUM(1,2)", "+cmd", "-1+1", "@SUM(1)", "  =SUM(1)", "\tcommand", "\rcommand"])
def test_csv_formula_cells_are_safe_while_json_keeps_opted_in_text(monkeypatch, text):
    bind(monkeypatch, snapshot([event(player_text=text)]))
    blob, _ = export.make_archive(SID, include_text=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("events.csv").decode("utf-8-sig"))))
        record = json.loads(archive.read("record.json"))
    assert rows[-1]["player_text"] == "'" + text
    assert record["events"][-1]["player_text"] == text


def test_missing_or_backwards_times_stay_unknown(monkeypatch):
    data = snapshot([event(ts="not-a-time"), event(ts="2026-11-07T12:00:00+00:00")])
    bind(monkeypatch, data)
    record = export.build_record(SID)
    assert all(row["elapsed_seconds"] is None for row in record["events"][1:])
    assert "event_time_unknown" in warning_codes(record)
