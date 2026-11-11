"""Downloads describe saved evidence, leave progress alone, and require text opt-in."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.llm import client
from app.llm.client import LLMResult
from app.llm.schemas import OpeningOut
from app.main import app
from app.services import export, negotiate, outcome, prepare, session

EMAIL = "Dear Prof. Chen, I have been ill for three days. Could I submit next Wednesday? Thanks, Alex"


def ready():
    sid, _ = session.create()
    prepare.send_email(sid, EMAIL)
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "He may ask for a plan.", "Offer an outline first.")
    negotiate.start(sid)
    return sid


def agree(sid, *, button=False):
    negotiate.turn(sid, "Could I submit next Wednesday?", request_id="export-reason")
    negotiate.turn(sid, "I can send the full report by Saturday at noon.", request_id="export-offer")
    if button:
        negotiate.confirm(sid)
    else:
        negotiate.turn(sid, "Yes, that's right. I'll make sure they're on time.", request_id="export-yes")
    negotiate.finish(sid)


def unzip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_export_in_progress_has_no_fake_attempt_activity():
    sid, _ = session.create()
    record = export.build_record(sid)
    assert record["schema_version"] == "ccq-record@1"
    assert record["summary"]["attempts_recorded"] == 1
    assert record["summary"]["attempts_with_activity"] == 0
    assert record["summary"]["attempts_with_confirmed_agreement"] == 0
    assert record["attempts"][0]["confirmation_method"] == "none"
    assert record["data_quality_warnings"] == []


@pytest.mark.parametrize("button,method,count", [(False,"text",3), (True,"button",2)])
def test_success_records_confirmation_methods_separately(button, method, count):
    sid = ready()
    agree(sid, button=button)
    record = export.build_record(sid)
    assert record["summary"]["completed_learner_turns"] == count
    assert record["summary"]["attempts_with_confirmed_agreement"] == 1
    assert record["summary"]["xp"] == 50
    assert record["attempts"][0]["confirmation_method"] == method
    assert record["attempts"][0]["ending"] == "full"
    assert record["data_quality_warnings"] == []
    assert set(record["summary"]["model_operations_by_mode"]) == {"mock"}


def test_failed_turn_retry_and_archived_attempts_are_not_pooled(monkeypatch):
    sid = ready()
    with monkeypatch.context() as patch:
        patch.setattr(negotiate, "call_json", lambda *a, **k: LLMResult(
            data=None,model="mock",prompt_version="test",errors=["injected"],latency_ms=[1,2],execution_mode="mock"))
        negotiate.turn(sid, "Could we discuss the report?", request_id="same-retry")
    negotiate.turn(sid, "Could we discuss the report?", request_id="same-retry")
    negotiate.turn(sid, "Could we discuss the report?", request_id="same-retry")
    negotiate.finish(sid)
    negotiate.retry(sid)
    record = export.build_record(sid)
    assert record["summary"]["completed_learner_turns"] == 1
    assert record["summary"]["technical_failures"] == 1
    assert record["summary"]["attempts_with_activity"] == 2
    first, second = record["attempts"]
    assert first["archived"] and first["completed_learner_turns"] == 1 and first["technical_failures"] == 1
    assert not second["archived"] and second["completed_learner_turns"] == 0 and second["technical_failures"] == 0
    failure = next(e for e in record["events"] if e["kind"] == "turn_failed")
    assert failure["model_attempts"] == 2 and failure["model_latency_ms"] == 3
    assert record["data_quality_warnings"] == []


def test_json_and_csv_export_join_without_usable_session_id():
    sid = ready()
    agree(sid)
    data, filename = export.make_archive(sid)
    files = unzip(data)
    assert set(files) == {"record.json", "events.csv", "attempts.csv", "README.txt"}
    record = json.loads(files["record.json"])
    assert sid not in filename and all(sid.encode() not in value for value in files.values())
    for table in ("events.csv", "attempts.csv"):
        rows = list(csv.DictReader(io.StringIO(files[table].decode("utf-8-sig"))))
        assert rows and all(row["record_id"] == record["record_id"] for row in rows)
    assert not record["includes_text"] and "conversations" not in record
    assert EMAIL.encode() not in data and EMAIL.encode() not in files["record.json"]
    assert record["record_id"] == export.build_record(sid)["record_id"]


def test_text_opt_in_contains_exact_user_writing_and_completed_conversation():
    sid = ready()
    reply = "Could we discuss the report?"
    negotiate.turn(sid, reply, request_id="text-option")
    data, _ = export.make_archive(sid, include_text=True)
    record = json.loads(unzip(data)["record.json"])
    assert record["includes_text"]
    assert record["preparation"]["email_final"] == EMAIL
    assert record["preparation"]["prediction"] == "He may ask for a plan."
    assert any(t["text"] == reply and t["speaker"] == "player" for t in record["conversations"][0]["turns"])
    assert any(e["player_text"] == reply for e in record["events"])


def test_export_does_not_call_model_write_events_or_change_database(monkeypatch):
    sid = ready()
    negotiate.finish(sid)  # Has no language feedback cache; export must not generate it.
    def forbidden(*args, **kwargs):
        raise AssertionError("export must not call a model or outcome service")
    for service in (prepare, negotiate, outcome):
        monkeypatch.setattr(service, "call_json", forbidden)
    monkeypatch.setattr(outcome, "outcome", forbidden)
    path = get_settings().db_path
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot = db.read_session_snapshot(sid)
    export.make_archive(sid)
    export.make_archive(sid, include_text=True)
    assert db.read_session_snapshot(sid) == snapshot
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_route_default_is_text_free_and_opt_in_download_is_zip():
    sid = ready()
    with TestClient(app) as http:
        base = f"/api/sessions/{sid}/export"
        response = http.get(base)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-disposition"].startswith('attachment; filename="ccq-record-R-')
        assert json.loads(unzip(response.content)["record.json"])["includes_text"] is False
        included = http.get(base + "?include_text=true")
        assert json.loads(unzip(included.content)["record.json"])["preparation"]["email_final"] == EMAIL
        assert http.get(base + "?include_text=wrong").status_code == 422
        assert http.get("/api/sessions/missing/export").status_code == 404


def test_missing_export_does_not_create_a_database():
    path = get_settings().db_path
    with TestClient(app) as http:
        assert http.get("/api/sessions/missing/export").status_code == 404
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize("mode", ["mock", "live", "custom"])
def test_transport_mode_records_selected_branch_without_network(monkeypatch, mode):
    monkeypatch.setenv("LLM_MOCK", "1" if mode == "mock" else "0")
    transport = lambda _: '{"reply":"In your email, you asked for more time."}'
    monkeypatch.setattr(client, "http_transport", transport)
    result = client.call_json(OpeningOut, [{"role":"user","content":"test"}], prompt_version="mode-test",
                             mock=lambda: {"reply":"In your email, you asked for more time."},
                             transport=transport if mode == "custom" else None)
    assert result.ok and result.execution_mode == mode
    assert json.loads(result.raw_for_log())["execution_mode"] == mode


def test_command_exports_one_session_and_refuses_overwrite(tmp_path):
    sid = ready()
    destination = tmp_path / "download.zip"
    tool = Path(__file__).resolve().parents[1] / "tools" / "export_session.py"
    command = [sys.executable, str(tool), "--session", sid, "--output", str(destination)]
    result = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert json.loads(unzip(destination.read_bytes())["record.json"])["includes_text"] is False
    original = destination.read_bytes()
    repeated = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy())
    assert repeated.returncode == 2 and "already exists" in repeated.stderr
    assert destination.read_bytes() == original


def test_command_unknown_session_creates_no_output(tmp_path):
    destination = tmp_path / "absent" / "download.zip"
    tool = Path(__file__).resolve().parents[1] / "tools" / "export_session.py"
    result = subprocess.run([sys.executable,str(tool),"--session","missing","--output",str(destination)],
                            capture_output=True,text=True,env=os.environ.copy())
    assert result.returncode == 2 and not destination.parent.exists()
