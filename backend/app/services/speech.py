"""Local, in-memory speech recognition. Transcripts are drafts, never game actions."""
from __future__ import annotations

import importlib
import importlib.util
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from .. import db
from ..config import ROOT, get_settings
from ..content import get_chapter
from ..session_serialization import serialized_session
from .session import Conflict, load, chapter_for

MAX_SECONDS = 20
SAMPLE_RATE = 16000
MAX_BYTES = 44 + MAX_SECONDS * SAMPLE_RATE * 2
MIN_SECONDS = 0.25
MAX_TRANSCRIPT_CHARS = 6000
MODELS_ROOT = ROOT / "models"
_MODELS = {"parakeet": "nemo-parakeet-tdt-0.6b-v2", "canary": "istupakov/canary-180m-flash-onnx"}
_MODEL_LOCK = Lock()
_INFERENCE_LOCK = Lock()
_cached_key: tuple | None = None
_cached_model: Any = None


class SpeechError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


@dataclass(frozen=True)
class ModelConfig:
    backend: str
    model: str
    directory: Path


def _config() -> ModelConfig:
    backend = get_settings().asr_backend.strip().lower()
    configured = os.getenv("ASR_MODEL_DIR", "").strip()
    directory = Path(configured) if configured else MODELS_ROOT / backend
    if not directory.is_absolute():
        directory = ROOT / directory
    return ModelConfig(backend, _MODELS.get(backend, ""), directory.resolve())


def _required_files(config: ModelConfig) -> list[Path]:
    if not config.model:
        raise SpeechError(503, "unsupported_backend", "This speech recognition backend is not supported. Typing is still available.")
    if not config.directory.is_relative_to(MODELS_ROOT.resolve()):
        raise SpeechError(503, "model_directory", "Speech models must be prepared inside this project's models folder.")
    patterns = ["config.json", "vocab.txt", "encoder-model?int8.onnx",
                "decoder_joint-model?int8.onnx" if config.backend == "parakeet" else "decoder-model?int8.onnx"]
    if not config.directory.is_dir():
        raise SpeechError(503, "model_missing", "The local speech model is not ready. Prepare it first, or continue by typing.")
    files = []
    for pattern in patterns:
        matches = [p for p in config.directory.glob(pattern) if p.is_file()]
        if len(matches) != 1 or matches[0].stat().st_size <= 0:
            raise SpeechError(503, "model_incomplete", "The local speech model is incomplete. Prepare it again, or continue by typing.")
        if not matches[0].resolve().is_relative_to(MODELS_ROOT.resolve()):
            raise SpeechError(503, "model_directory", "Speech model files must stay inside this project's models folder.")
        files.append(matches[0])
    return files


def _check_dependencies() -> None:
    for module in ("numpy", "onnx_asr", "onnxruntime"):
        try:
            found = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            found = None
        if found is None:
            raise SpeechError(503, "dependencies_missing", "Local speech recognition is not installed yet. Typing is still available.")


def status() -> dict:
    config = _config()
    result = {"available": False, "backend": config.backend, "model": config.model,
              "max_seconds": MAX_SECONDS, "max_bytes": MAX_BYTES, "sample_rate": SAMPLE_RATE}
    try:
        _required_files(config)
        _check_dependencies()
    except (SpeechError, OSError) as exc:
        result["message"] = exc.message if isinstance(exc, SpeechError) else "The local speech model could not be checked. Typing is still available."
    else:
        result["available"] = True
    return result


def ensure_session(sid: str, attempt: int) -> dict:
    state = load(sid)
    neg = state["negotiation"]
    if state["phase"] != "negotiate" or neg["attempt"] != attempt:
        raise Conflict("the conversation changed; record again in the current attempt")
    if neg["paused"] or neg["turn_count"] >= chapter_for(state).rules.max_turns:
        raise Conflict("this attempt is paused; start a new attempt before recording")
    if neg["agreement"]["status"] == "confirmed":
        raise Conflict("the agreement is already confirmed")
    if not neg["opening"]:
        raise Conflict("wait for Prof. Chen's opening before recording")
    return state


def _decode_pcm(body: bytes) -> tuple[bytes, float]:
    if len(body) > MAX_BYTES:
        raise SpeechError(413, "audio_too_large", "Record at most 20 seconds of audio.")
    if len(body) < 44 or body[:4] != b"RIFF" or body[8:12] != b"WAVE":
        raise SpeechError(400, "invalid_wav", "Send a 16 kHz mono PCM16 WAV recording.")
    if struct.unpack_from("<I", body, 4)[0] + 8 != len(body):
        raise SpeechError(400, "invalid_wav_size", "The recording is incomplete or has an invalid WAV size. Record it again.")
    offset, fmt, pcm = 12, None, None
    while offset < len(body):
        if offset + 8 > len(body):
            raise SpeechError(400, "invalid_wav", "The WAV recording has a truncated chunk.")
        kind = body[offset:offset + 4]
        size = struct.unpack_from("<I", body, offset + 4)[0]
        start, end = offset + 8, offset + 8 + size
        if end + (size & 1) > len(body):
            raise SpeechError(400, "invalid_wav", "The WAV recording has a truncated chunk.")
        if kind == b"fmt ":
            if fmt is not None or size < 16:
                raise SpeechError(400, "invalid_wav", "The WAV format header is invalid.")
            fmt = struct.unpack_from("<HHIIHH", body, start)
            if fmt != (1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16):
                raise SpeechError(400, "unsupported_audio_format", "Use uncompressed PCM16 WAV, one channel, at 16 kHz.")
        elif kind == b"data":
            if pcm is not None or fmt is None or size % 2:
                raise SpeechError(400, "invalid_wav", "The WAV audio data is invalid.")
            pcm = body[start:end]
        offset = end + (size & 1)
    if fmt is None or pcm is None:
        raise SpeechError(400, "invalid_wav", "The WAV recording has no usable audio data.")
    duration = len(pcm) / (SAMPLE_RATE * 2)
    if duration > MAX_SECONDS:
        raise SpeechError(413, "audio_too_long", "Record at most 20 seconds of audio.")
    if duration < MIN_SECONDS:
        raise SpeechError(400, "audio_too_short", "The recording is too short. Speak for at least a quarter of a second.")
    return pcm, duration


def _load_model(config: ModelConfig):
    """Only an existing, complete local directory reaches onnx-asr's offline loader."""
    global _cached_key, _cached_model
    with _MODEL_LOCK:
        files = _required_files(config)
        key = (config.backend, str(config.directory), tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files))
        if _cached_key == key and _cached_model is not None:
            return _cached_model
        _check_dependencies()
        try:
            runtime = importlib.import_module("onnxruntime")
            onnx_asr = importlib.import_module("onnx_asr")
            options = runtime.SessionOptions()
            options.intra_op_num_threads = 4
            options.inter_op_num_threads = 1
            # onnx-asr 0.12.0 treats an existing local directory as offline: missing
            # files raise instead of downloading. No environment-wide Hub flags.
            model = onnx_asr.load_model(config.model, config.directory, quantization="int8",
                                        providers=["CPUExecutionProvider"], sess_options=options)
        except Exception as exc:
            raise SpeechError(503, "model_load_failed", "The local speech model could not be loaded. Try again or continue by typing.") from exc
        _cached_key, _cached_model = key, model
        return model


@serialized_session
def _record_current(sid: str, attempt: int, config: ModelConfig, metadata: dict, *, error_code: str | None = None) -> None:
    ensure_session(sid, attempt)
    db.log_event(sid, "negotiate", "speech_failed" if error_code else "speech_transcribed",
                 model=config.model or None, rule_decision=error_code or "ok", xp=0,
                 payload={**metadata, "attempt": attempt, "backend": config.backend, "execution_mode": "local",
                          **({"error_code": error_code} if error_code else {})})


def transcribe(sid: str, body: bytes, *, attempt: int) -> dict:
    ensure_session(sid, attempt)
    config = _config()
    started = time.perf_counter()
    metadata: dict = {"audio_bytes": len(body)}
    try:
        pcm, duration = _decode_pcm(body)
        metadata.update(duration_seconds=round(duration, 4), sample_rate=SAMPLE_RATE)
        _required_files(config)
        _check_dependencies()
        try:
            np = importlib.import_module("numpy")
            waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / np.float32(32768.0)
        except Exception as exc:
            raise SpeechError(503, "audio_processing_failed", "Local audio processing is unavailable. Try again or continue by typing.") from exc
        if float(np.sqrt(np.mean(waveform ** 2))) < 0.001 or float(np.std(waveform)) < 0.0005:
            raise SpeechError(422, "audio_silent", "No audible speech was detected. Check your microphone and record again.")
        if not _INFERENCE_LOCK.acquire(blocking=False):
            raise SpeechError(503, "speech_busy", "Speech recognition is busy. Please wait a moment and try again.")
        try:
            model = _load_model(config)
            ensure_session(sid, attempt)
            try:
                text = model.recognize(waveform, sample_rate=SAMPLE_RATE)
            except Exception as exc:
                raise SpeechError(503, "recognition_failed", "Speech recognition could not finish. Try again or continue by typing.") from exc
        finally:
            _INFERENCE_LOCK.release()
        if not isinstance(text, str):
            raise SpeechError(503, "invalid_transcript", "Speech recognition returned an invalid result. Record again or type your message.")
        text = text.strip()
        metadata["transcript_chars"] = len(text)
        if not text:
            raise SpeechError(422, "no_speech_recognized", "No words were recognized. Record again or type your message.")
        if len(text) > MAX_TRANSCRIPT_CHARS:
            raise SpeechError(422, "transcript_too_long", "The transcription is too long. Record a shorter message or type it instead.")
    except SpeechError as exc:
        metadata["latency_ms"] = int((time.perf_counter() - started) * 1000)
        _record_current(sid, attempt, config, metadata, error_code=exc.code)
        raise
    except OSError as exc:
        error = SpeechError(503, "model_unavailable", "The local speech model is unavailable. Try again or continue by typing.")
        metadata["latency_ms"] = int((time.perf_counter() - started) * 1000)
        _record_current(sid, attempt, config, metadata, error_code=error.code)
        raise error from exc
    metadata["latency_ms"] = int((time.perf_counter() - started) * 1000)
    # The session lock is held only for this final check/log, never for inference.
    _record_current(sid, attempt, config, metadata)
    return {"text": text, "model": config.model, "duration_seconds": round(duration, 4)}
