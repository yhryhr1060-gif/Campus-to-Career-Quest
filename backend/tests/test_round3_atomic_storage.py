"""A successful exchange is either fully committed or safely retryable with the same id."""
import json
import sqlite3
from copy import deepcopy

import pytest

from app import db
from app.config import get_settings
from app.services import negotiate, prepare, session
from app.services.session import Conflict


def ready(*, proposal=False):
    sid, _ = session.create()
    prepare.send_email(sid, "Dear Prof. Chen, I have been ill for three days. "
                            "Could I hand in the report next Wednesday? Thanks, Alex")
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "He may need a clearer plan.", "Ask what is possible.")
    negotiate.start(sid)
    if proposal:
        negotiate.turn(sid, "I have been ill.", request_id="reason-0001")
        result = negotiate.turn(sid, "I can send the full report by Saturday at noon.", request_id="plan-0001")
        assert result["negotiation"]["agreement"]["status"] == "proposed"
    return sid


def install_failure(stage):
    operation, table = {
        "state": ("UPDATE", "sessions"), "xp": ("INSERT", "xp_awards"),
        "event": ("INSERT", "events"), "cache": ("INSERT", "processed_requests"),
    }[stage]
    with db.connect() as connection:
        connection.execute(f"CREATE TRIGGER injected_failure BEFORE {operation} ON {table} "
                           "BEGIN SELECT RAISE(ABORT, 'injected atomic failure'); END")


def remove_failure():
    with db.connect() as connection:
        connection.execute("DROP TRIGGER injected_failure")


@pytest.mark.parametrize("stage", ["state", "xp", "event", "cache"])
def test_confirming_turn_rolls_back_every_write_and_same_id_retries_once(stage, chapter, monkeypatch):
    sid = ready(proposal=True)
    before = db.read_session_snapshot(sid)
    install_failure(stage)
    with pytest.raises(sqlite3.IntegrityError, match="injected atomic failure"):
        negotiate.turn(sid, "Yes, that's right.", request_id="atomic-0001")
    assert db.read_session_snapshot(sid) == before
    assert db.get_processed(sid, "atomic-0001") is None
    remove_failure()

    success = negotiate.turn(sid, "Yes, that's right.", request_id="atomic-0001")
    assert success["decision"] == "confirmed"
    assert success["negotiation"]["turn_count"] == before["state"]["negotiation"]["turn_count"] + 1
    assert success["xp"] == chapter.xp.prep_complete + chapter.xp.first_agreement
    assert db.get_processed(sid, "atomic-0001") == success
    monkeypatch.setattr(negotiate, "call_json", lambda *a, **k: pytest.fail("committed id must not call model again"))
    assert negotiate.turn(sid, "Yes, that's right.", request_id="atomic-0001") == success
    after = db.read_session_snapshot(sid)
    events = [e for e in after["events"] if e["kind"] == "turn"
              and json.loads(e["payload_json"])["request_id"] == "atomic-0001"]
    assert len(events) == 1 and events[0]["xp"] == chapter.xp.first_agreement
    assert after["xp"] == success["xp"]


def test_exception_after_response_cache_insert_also_rolls_back(monkeypatch):
    sid = ready()
    before = db.read_session_snapshot(sid)
    original = db.put_processed

    def insert_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure after cache write")

    with monkeypatch.context() as patch:
        patch.setattr(db, "put_processed", insert_then_fail)
        with pytest.raises(RuntimeError, match="after cache write"):
            negotiate.turn(sid, "I have been ill.", request_id="cache-0001")
    assert db.read_session_snapshot(sid) == before
    assert db.get_processed(sid, "cache-0001") is None
    success = negotiate.turn(sid, "I have been ill.", request_id="cache-0001")
    assert success["negotiation"]["turn_count"] == 1
    assert negotiate.turn(sid, "I have been ill.", request_id="cache-0001") == success


@pytest.mark.parametrize("stage", ["xp", "event"])
def test_confirm_button_state_and_xp_commit_together(stage, chapter):
    sid = ready(proposal=True)
    before = db.read_session_snapshot(sid)
    install_failure(stage)
    with pytest.raises(sqlite3.IntegrityError, match="injected atomic failure"):
        negotiate.confirm(sid)
    assert db.read_session_snapshot(sid) == before
    remove_failure()
    success = negotiate.confirm(sid)
    assert success["decision"] == "confirmed"
    assert success["xp"] == chapter.xp.prep_complete + chapter.xp.first_agreement
    assert negotiate.confirm(sid)["xp"] == success["xp"]
    stored = db.read_session_snapshot(sid)
    assert sum(e["xp"] or 0 for e in stored["events"] if e["kind"] == "confirm_button") == chapter.xp.first_agreement
    assert sum(t["kind"] == "confirmed" for t in stored["state"]["negotiation"]["turns"]
               if t["speaker"] == "npc") == 1


def test_model_call_holds_no_database_write_transaction(monkeypatch):
    sid = ready()
    original = negotiate.call_json

    def probe_transaction_then_call(*args, **kwargs):
        # A separate connection can take the write lock while the provider is being called.
        probe = sqlite3.connect(get_settings().db_path, timeout=0)
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.rollback()
        finally:
            probe.close()
        return original(*args, **kwargs)

    monkeypatch.setattr(negotiate, "call_json", probe_transaction_then_call)
    assert negotiate.turn(sid, "I have been ill.", request_id="outside-tx-0001")["available"] is True


def test_state_changed_during_model_call_is_preserved_and_same_id_can_retry(monkeypatch):
    sid = ready()
    original = negotiate.call_json

    def add_note_during_model(*args, **kwargs):
        result = original(*args, **kwargs)
        session.view_hotspot(sid, "syllabus")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(negotiate, "call_json", add_note_during_model)
        with pytest.raises(Conflict, match="conversation changed"):
            negotiate.turn(sid, "I have been ill.", request_id="changed-0001")
    current = db.read_session_snapshot(sid)
    assert "syllabus" in current["state"]["notes"]
    assert current["state"]["negotiation"]["turn_count"] == 0
    assert not [e for e in current["events"] if e["kind"] == "turn"]
    assert db.get_processed(sid, "changed-0001") is None
    assert negotiate.turn(sid, "I have been ill.", request_id="changed-0001")["negotiation"]["turn_count"] == 1


def test_missing_snapshot_does_not_create_database_or_directory(monkeypatch, tmp_path):
    missing = tmp_path / "uncreated" / "data"
    monkeypatch.setenv("DATA_DIR", str(missing))
    assert db.read_session_snapshot("S-MISSING") is None
    assert not missing.exists()


def test_snapshot_contains_only_stored_values_and_does_not_mutate_database():
    sid = ready()
    negotiate.turn(sid, "I have been ill.", request_id="snapshot-0001")
    path = get_settings().db_path
    before_bytes = path.read_bytes()
    snapshot = db.read_session_snapshot(sid)
    assert set(snapshot) == {"created_at", "state", "events", "xp"}
    assert snapshot["created_at"]
    assert snapshot["state"] == session.load(sid)
    assert snapshot["events"] == db.list_events(sid)
    assert snapshot["xp"] == db.total_xp(sid)
    assert db.read_session_snapshot("S-MISSING") is None
    assert path.read_bytes() == before_bytes


def test_snapshot_keeps_one_database_version_when_a_writer_commits_between_reads(monkeypatch):
    sid = ready()
    with db.connect() as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    before = db.read_session_snapshot(sid)
    changed = deepcopy(before["state"])
    changed["notes"].append("syllabus")
    original_connect = sqlite3.connect
    wrote = False

    class InterleavedRead:
        def __init__(self, connection):
            self.connection = connection

        @property
        def row_factory(self):
            return self.connection.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self.connection.row_factory = value

        def execute(self, sql, parameters=()):
            nonlocal wrote
            cursor = self.connection.execute(sql, parameters)
            if sql.startswith("SELECT created_at") and not wrote:
                wrote = True
                writer = original_connect(get_settings().db_path)
                try:
                    writer.execute("BEGIN IMMEDIATE")
                    writer.execute("UPDATE sessions SET state_json = ? WHERE id = ?", (json.dumps(changed), sid))
                    writer.execute("INSERT INTO events (session_id, ts, phase, kind) VALUES (?,?,?,?)",
                                   (sid, db.now_iso(), "test", "snapshot_interleaving"))
                    writer.execute("INSERT INTO xp_awards (session_id, kind, amount, ts) VALUES (?,?,?,?)",
                                   (sid, "snapshot_test", 7, db.now_iso()))
                    writer.commit()
                finally:
                    writer.close()
            return cursor

        def rollback(self):
            self.connection.rollback()

        def close(self):
            self.connection.close()

    def connect_with_interleaving(*args, **kwargs):
        assert kwargs.get("uri") is True and "mode=ro" in str(args[0])
        return InterleavedRead(original_connect(*args, **kwargs))

    with monkeypatch.context() as patch:
        patch.setattr(db.sqlite3, "connect", connect_with_interleaving)
        snapshot = db.read_session_snapshot(sid)
    assert wrote
    assert snapshot == before
    after = db.read_session_snapshot(sid)
    assert after["state"] == changed and after["xp"] == before["xp"] + 7
    assert after["events"][-1]["kind"] == "snapshot_interleaving"
