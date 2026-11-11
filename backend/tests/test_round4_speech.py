"""Local speech is a validated, retryable draft operation, never a negotiation turn."""
import asyncio
import io
import json
import math
import struct
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import app, transcribe_speech
from app.services import negotiate, prepare, session, speech
from app.services.session import Conflict
from tools import prepare_speech


def wav_bytes(seconds=0.5, *, rate=16000, channels=1, width=2, silent=False):
    frames = int(seconds * rate)
    if width == 2:
        raw = b"".join(struct.pack("<h", 0 if silent else int(8000 * math.sin(2 * math.pi * 220 * i / rate)))
                       * channels for i in range(frames))
    else:
        raw = bytes([128] * frames * channels * width)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(raw)
    return stream.getvalue()


def ready(*, opening=True):
    sid, _ = session.create()
    prepare.send_email(sid, "Dear Prof. Chen, I have been ill for three days. "
                            "Could I submit the report next Wednesday? Thanks, Alex")
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "He may ask for another date.", "Offer an outline.")
    if opening:
        negotiate.start(sid)
    return sid


@pytest.fixture
def local_model(monkeypatch, tmp_path):
    pytest.importorskip("numpy")
    root = tmp_path / "models"
    folder = root / "parakeet"
    folder.mkdir(parents=True)
    for name in ("config.json", "vocab.txt", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"):
        (folder / name).write_bytes(b"test model placeholder")
    monkeypatch.setattr(speech, "MODELS_ROOT", root)
    monkeypatch.setenv("ASR_BACKEND", "parakeet")
    monkeypatch.setenv("ASR_MODEL_DIR", str(folder))
    monkeypatch.setattr(speech, "_cached_key", None)
    monkeypatch.setattr(speech, "_cached_model", None)
    monkeypatch.setattr(speech, "_check_dependencies", lambda: None)
    return folder


def fake_recognizer(monkeypatch, text="Could I send it by Saturday?", callback=None):
    calls = []

    class Recognizer:
        def recognize(self, waveform, *, sample_rate):
            calls.append((waveform, sample_rate))
            if callback:
                callback()
            return text

    monkeypatch.setattr(speech, "_load_model", lambda config: Recognizer())
    return calls


def test_status_never_loads_or_downloads_a_model(local_model, monkeypatch):
    monkeypatch.setattr(speech, "_load_model", lambda config: pytest.fail("status cannot load models"))
    with TestClient(app) as http:
        status = http.get("/api/speech/status").json()
    assert status == {"available": True, "backend": "parakeet", "model": "nemo-parakeet-tdt-0.6b-v2",
                      "max_seconds": 20, "max_bytes": 640044, "sample_rate": 16000}
    assert not get_settings().data_dir.exists()


def test_missing_or_incomplete_model_is_unavailable_without_creating_files(monkeypatch, tmp_path):
    root = tmp_path / "models"
    folder = root / "parakeet"
    monkeypatch.setattr(speech, "MODELS_ROOT", root)
    monkeypatch.setenv("ASR_MODEL_DIR", str(folder))
    monkeypatch.setenv("ASR_BACKEND", "parakeet")
    assert speech.status()["available"] is False
    assert not folder.exists()
    folder.mkdir(parents=True)
    (folder / "config.json").write_bytes(b"{}")
    assert speech.status()["available"] is False
    assert sorted(p.name for p in folder.iterdir()) == ["config.json"]


def test_unsupported_backend_and_outside_model_directory_are_unavailable(local_model, monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_BACKEND", "unsupported-provider")
    assert speech.status()["available"] is False
    assert "not supported" in speech.status()["message"]
    monkeypatch.setenv("ASR_BACKEND", "parakeet")
    monkeypatch.setenv("ASR_MODEL_DIR", str(tmp_path / "outside"))
    assert "inside" in speech.status()["message"]


def test_missing_optional_dependencies_are_a_readable_unavailable_state(local_model, monkeypatch):
    # Restore the real dependency probe after the fixture's portable stub.
    def unavailable():
        raise speech.SpeechError(503, "dependencies_missing", "Local speech recognition is not installed yet.")
    monkeypatch.setattr(speech, "_check_dependencies", unavailable)
    assert speech.status()["available"] is False
    sid = ready()
    with TestClient(app) as http:
        response = http.post(f"/api/sessions/{sid}/speech/transcribe?attempt=1",
                             content=wav_bytes(), headers={"Content-Type": "audio/wav"})
    assert response.status_code == 503 and "not installed" in response.json()["detail"]


def test_transcription_returns_a_draft_and_only_logs_metadata(local_model, monkeypatch):
    sid = ready()
    before = session.load(sid)
    xp = db.total_xp(sid)
    original = "My private draft is not sent yet."
    calls = fake_recognizer(monkeypatch, text=original)
    with TestClient(app) as http:
        response = http.post(f"/api/sessions/{sid}/speech/transcribe?attempt=1",
                             content=wav_bytes(), headers={"Content-Type": "audio/wav"})
    assert response.status_code == 200, response.text
    assert response.json() == {"text": original, "model": "nemo-parakeet-tdt-0.6b-v2", "duration_seconds": 0.5}
    assert session.load(sid) == before and db.total_xp(sid) == xp
    waveform, rate = calls[0]
    assert str(waveform.dtype) == "float32" and waveform.ndim == 1 and len(waveform) == 8000
    assert rate == 16000 and float(abs(waveform).max()) <= 1
    event = db.list_events(sid)[-1]
    assert event["kind"] == "speech_transcribed" and event["xp"] == 0
    assert event["player_text"] is None and event["asr_text"] is None and event["audio_path"] is None
    payload = json.loads(event["payload_json"])
    assert payload["attempt"] == 1 and payload["execution_mode"] == "local"
    assert payload["transcript_chars"] == len(original) and original not in json.dumps(event)
    assert not get_settings().audio_dir.exists()


@pytest.mark.parametrize("body,expected_status,code", [
    (b"not a WAV", 400, "invalid_wav"),
    (wav_bytes(rate=8000), 400, "unsupported_audio_format"),
    (wav_bytes(channels=2), 400, "unsupported_audio_format"),
    (wav_bytes(width=1), 400, "unsupported_audio_format"),
    (wav_bytes(seconds=0.1), 400, "audio_too_short"),
    (wav_bytes(silent=True), 422, "audio_silent"),
    (wav_bytes()[:-2], 400, "invalid_wav_size"),
    (b"x" * (speech.MAX_BYTES + 1), 413, "audio_too_large"),
])
def test_bad_audio_is_rejected_before_inference(local_model, monkeypatch, body, expected_status, code):
    sid = ready()
    before = session.load(sid)
    calls = fake_recognizer(monkeypatch)
    with pytest.raises(speech.SpeechError) as caught:
        speech.transcribe(sid, body, attempt=1)
    assert (caught.value.status_code, caught.value.code) == (expected_status, code)
    assert calls == [] and session.load(sid) == before
    assert db.list_events(sid)[-1]["kind"] == "speech_failed"


@pytest.mark.parametrize("offset,value", [(20, 3), (28, 123), (32, 4)])
def test_wav_codec_byte_rate_and_alignment_are_checked(local_model, monkeypatch, offset, value):
    sid = ready()
    body = bytearray(wav_bytes())
    struct.pack_into("<I" if offset == 28 else "<H", body, offset, value)
    fake_recognizer(monkeypatch)
    with pytest.raises(speech.SpeechError, match="uncompressed"):
        speech.transcribe(sid, bytes(body), attempt=1)


def test_exact_maximum_length_is_accepted_but_one_extra_frame_is_not(local_model, monkeypatch):
    sid = ready()
    fake_recognizer(monkeypatch)
    body = wav_bytes(seconds=20)
    assert len(body) == speech.MAX_BYTES
    assert speech.transcribe(sid, body, attempt=1)["duration_seconds"] == 20
    with pytest.raises(speech.SpeechError) as caught:
        speech.transcribe(sid, wav_bytes(seconds=20 + 1 / 16000), attempt=1)
    assert caught.value.status_code == 413


@pytest.mark.parametrize("text,status,code", [
    ("   ", 422, "no_speech_recognized"),
    ("a" * 6001, 422, "transcript_too_long"),
    (None, 503, "invalid_transcript"),
])
def test_empty_invalid_and_overlong_transcripts_are_not_returned(local_model, monkeypatch, text, status, code):
    sid = ready()
    fake_recognizer(monkeypatch, text=text)
    with pytest.raises(speech.SpeechError) as caught:
        speech.transcribe(sid, wav_bytes(), attempt=1)
    assert (caught.value.status_code, caught.value.code) == (status, code)
    assert session.load(sid)["negotiation"]["turn_count"] == 0


@pytest.mark.parametrize("change", ["phase", "attempt", "paused", "confirmed", "opening"])
def test_stale_or_ineligible_session_is_rejected_without_recognition_or_log(local_model, monkeypatch, change):
    sid = ready()
    state = session.load(sid)
    if change == "phase":
        state["phase"] = "outcome"
    elif change == "attempt":
        state["negotiation"]["attempt"] = 2
    elif change == "paused":
        state["negotiation"]["paused"] = True
    elif change == "confirmed":
        state["negotiation"]["agreement"]["status"] = "confirmed"
    else:
        state["negotiation"]["opening"] = None
    db.save_state(sid, state)
    events = db.list_events(sid)
    calls = fake_recognizer(monkeypatch)
    with pytest.raises(Conflict):
        speech.transcribe(sid, wav_bytes(), attempt=1)
    assert calls == [] and db.list_events(sid) == events


def test_busy_service_does_not_queue_another_inference(local_model, monkeypatch):
    sid = ready()
    calls = fake_recognizer(monkeypatch)
    assert speech._INFERENCE_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(speech.SpeechError) as caught:
            speech.transcribe(sid, wav_bytes(), attempt=1)
    finally:
        speech._INFERENCE_LOCK.release()
    assert caught.value.status_code == 503 and caught.value.code == "speech_busy"
    assert calls == []
    assert speech.transcribe(sid, wav_bytes(), attempt=1)["text"]


def test_inference_failure_releases_global_lock_for_retry(local_model, monkeypatch):
    sid = ready()
    def fail():
        raise RuntimeError("private internal error must not reach the browser")
    fake_recognizer(monkeypatch, callback=fail)
    with pytest.raises(speech.SpeechError) as caught:
        speech.transcribe(sid, wav_bytes(), attempt=1)
    assert caught.value.status_code == 503 and "private" not in str(caught.value)
    fake_recognizer(monkeypatch)
    assert speech.transcribe(sid, wav_bytes(), attempt=1)["text"]


def test_long_inference_holds_no_session_lock_and_discards_old_result(local_model, monkeypatch):
    sid = ready()
    entered, release = Event(), Event()
    def wait_for_finish():
        entered.set()
        assert release.wait(10)
    fake_recognizer(monkeypatch, callback=wait_for_finish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        recognizing = pool.submit(speech.transcribe, sid, wav_bytes(), attempt=1)
        try:
            assert entered.wait(5)
            finished = pool.submit(negotiate.finish, sid)
            assert finished.result(timeout=3)["phase"] == "outcome"
        finally:
            release.set()
        with pytest.raises(Conflict):
            recognizing.result(timeout=5)
    assert not [e for e in db.list_events(sid) if e["kind"] == "speech_transcribed"]


def test_new_attempt_during_inference_discards_old_result(local_model, monkeypatch):
    sid = ready()
    fake_recognizer(monkeypatch, callback=lambda: negotiate.retry(sid))
    with pytest.raises(Conflict, match="changed"):
        speech.transcribe(sid, wav_bytes(), attempt=1)
    assert session.load(sid)["negotiation"]["attempt"] == 2
    assert not [e for e in db.list_events(sid) if e["kind"].startswith("speech_")]


def test_model_cache_is_thread_safe_and_uses_only_local_cpu_files(local_model, monkeypatch):
    calls = []
    model = object()
    original_import = speech.importlib.import_module
    def load_model(*args, **kwargs):
        calls.append((args, kwargs))
        return model
    def fake_import(name):
        if name == "onnxruntime":
            return SimpleNamespace(SessionOptions=SimpleNamespace)
        if name == "onnx_asr":
            return SimpleNamespace(load_model=load_model)
        return original_import(name)
    monkeypatch.setattr(speech.importlib, "import_module", fake_import)
    config = speech._config()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: speech._load_model(config), range(4)))
    assert all(value is model for value in results) and len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("nemo-parakeet-tdt-0.6b-v2", local_model.resolve())
    assert kwargs["quantization"] == "int8" and kwargs["providers"] == ["CPUExecutionProvider"]
    assert kwargs["sess_options"].intra_op_num_threads == 4
    assert kwargs["sess_options"].inter_op_num_threads == 1


def test_route_rejects_media_type_and_declared_size_early(local_model, monkeypatch):
    sid = ready()
    fake_recognizer(monkeypatch)
    with TestClient(app) as http:
        wrong_type = http.post(f"/api/sessions/{sid}/speech/transcribe?attempt=1", content=b"x")
        too_large = http.post(f"/api/sessions/{sid}/speech/transcribe?attempt=1", content=b"x",
                              headers={"Content-Type": "audio/wav", "Content-Length": str(speech.MAX_BYTES + 1)})
        mismatch = http.post(f"/api/sessions/{sid}/speech/transcribe?attempt=1", content=wav_bytes(),
                             headers={"Content-Type": "audio/wav", "Content-Length": "1"})
    assert wrong_type.status_code == 400
    assert too_large.status_code == 413
    assert mismatch.status_code == 400


def test_streaming_upload_stops_as_soon_as_size_limit_is_crossed(local_model, monkeypatch):
    sid = ready()
    read = []
    class Request:
        headers = {"content-type": "audio/wav"}
        async def stream(self):
            for index, chunk in enumerate((b"x" * speech.MAX_BYTES, b"x", b"must not be read")):
                read.append(index)
                yield chunk
    fake_recognizer(monkeypatch)
    with pytest.raises(speech.SpeechError) as caught:
        asyncio.run(transcribe_speech(sid, Request(), attempt=1))
    assert caught.value.status_code == 413 and read == [0, 1]


@pytest.fixture
def installer(monkeypatch, tmp_path):
    downloads, loads = [], []
    monkeypatch.setattr(prepare_speech, "ROOT", tmp_path)
    monkeypatch.setattr(speech, "MODELS_ROOT", tmp_path / "models")
    monkeypatch.setattr(prepare_speech, "version", lambda name: "test-version")
    monkeypatch.setenv("ASR_BACKEND", "parakeet")
    monkeypatch.setenv("ASR_MODEL_DIR", "")
    def download(repo, **kwargs):
        downloads.append((repo, kwargs))
        folder = kwargs["local_dir"]
        folder.mkdir(parents=True, exist_ok=True)
        decoder = "decoder-model.int8.onnx" if "canary" in repo else "decoder_joint-model.int8.onnx"
        for name in ("config.json", "vocab.txt", "encoder-model.int8.onnx", decoder):
            (folder / name).write_bytes(b"validated fake model file")
    def load_model(*args, **kwargs):
        loads.append((args, kwargs))
        return object()
    monkeypatch.setitem(sys.modules, "onnx_asr", SimpleNamespace(load_model=load_model))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(SessionOptions=SimpleNamespace))
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    return tmp_path, downloads, loads


def test_model_preparation_respects_service_directory_and_reuses_verified_local_files(installer, monkeypatch):
    root, downloads, loads = installer
    monkeypatch.setenv("ASR_MODEL_DIR", "models/custom-parakeet")
    assert prepare_speech.main([]) == 0
    target = root / "models" / "custom-parakeet"
    assert downloads[0][1]["local_dir"] == target
    assert downloads[0][1]["revision"] == prepare_speech.MODELS["parakeet"][2]
    assert loads[0][0] == (speech._MODELS["parakeet"], target)
    assert prepare_speech.main([]) == 0
    assert len(downloads) == 1 and len(loads) == 2  # rerun validates locally without a Hub request
    manifest = json.loads((target / "model-info.json").read_text(encoding="utf-8"))
    assert manifest["model"] == speech._MODELS["parakeet"]
    assert not (target / ".model-info.json.tmp").exists()


def test_model_preparation_repairs_corruption_instead_of_reusing_it(installer):
    root, downloads, loads = installer
    prepare_speech.main([])
    encoder = root / "models" / "parakeet" / "encoder-model.int8.onnx"
    encoder.write_bytes(b"x" * encoder.stat().st_size)  # same size, different digest
    assert prepare_speech.main([]) == 0
    assert len(downloads) == 2 and downloads[-1][1]["force_download"] is True
    assert len(loads) == 2 and encoder.read_bytes() == b"validated fake model file"


def test_canary_preparation_matches_runtime_mapping_and_uses_fixed_revision(installer):
    root, downloads, loads = installer
    assert prepare_speech.main(["--backend", "canary"]) == 0
    assert loads[0][0] == (speech._MODELS["canary"], root / "models" / "canary")
    assert downloads[0][0] == "istupakov/canary-180m-flash-onnx"
    assert downloads[0][1]["revision"] == "92c2231a4e2b2524277fea759be967d2e6edfc49"


def test_model_preparation_refuses_outside_directory_and_mixed_models(installer, monkeypatch):
    root, downloads, _ = installer
    monkeypatch.setenv("ASR_MODEL_DIR", str(root / "outside"))
    with pytest.raises(SystemExit) as caught:
        prepare_speech.main([])
    assert caught.value.code == 2 and downloads == []
    monkeypatch.setenv("ASR_MODEL_DIR", "models/shared")
    assert prepare_speech.main([]) == 0
    with pytest.raises(SystemExit) as caught:
        prepare_speech.main(["--backend", "canary"])
    assert caught.value.code == 2 and len(downloads) == 1
