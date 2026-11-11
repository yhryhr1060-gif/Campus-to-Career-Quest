"""FastAPI entry point. Routes are thin: all logic lives in app/services and app/rules.

Run (from backend/):  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import get_settings
from .content import get_chapter
from .limits import InputTooLong, MAX_EMAIL_CHARS, MAX_TURN_CHARS, MAX_PREDICTION_CHARS, MAX_BACKUP_CHARS
from .services import export, negotiate, outcome, prepare, session, speech, writing_feedback
from .services.session import Conflict, NotFound

app = FastAPI(title="Campus to Career Quest · Chapter 2 grey box")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(NotFound)
async def _not_found(_: Request, exc: NotFound):
    return JSONResponse(status_code=404, content={"detail": f"not found: {exc}"})


@app.exception_handler(Conflict)
async def _conflict(_: Request, exc: Conflict):
    content = {"detail": str(exc)}
    if getattr(exc, "code", None):
        content["code"] = exc.code
    return JSONResponse(status_code=409, content=content)


@app.exception_handler(InputTooLong)
async def _too_long(_: Request, exc: InputTooLong):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(speech.SpeechError)
async def _speech_error(_: Request, exc: speech.SpeechError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


# ------------------------------------------------------------------ bodies

class DraftBody(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=100)
    draft: str = Field(max_length=MAX_EMAIL_CHARS)


class EmailBody(BaseModel):
    email: str = Field(max_length=MAX_EMAIL_CHARS)
    acknowledged_open_points: bool = False


class DecisionBody(BaseModel):
    version: int = Field(ge=1)
    item_id: str = Field(min_length=1, max_length=40)
    decision: str = Field(pattern="^(keep|clear)$")


class PredictionBody(BaseModel):
    prediction: str = Field(default="", max_length=MAX_PREDICTION_CHARS)
    backup_plan: str = Field(default="", max_length=MAX_BACKUP_CHARS)


class TurnBody(BaseModel):
    text: str = Field(max_length=MAX_TURN_CHARS)
    request_id: str = Field(min_length=6, max_length=64)
    input_mode: str = Field(default="typed", pattern="^(typed|voice|voice_edited)$")
    asr_text: str | None = Field(default=None, max_length=6000)


class PhaseBody(BaseModel):
    phase: str


class PlayerBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class LocationBody(BaseModel):
    area: str = Field(min_length=1, max_length=40)


# ------------------------------------------------------------------ routes

@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "llm": s.model_label, "asr": s.asr_backend}


@app.get("/api/speech/status")
def speech_status():
    return speech.status()


@app.post("/api/sessions/{sid}/speech/transcribe")
async def transcribe_speech(sid: str, request: Request, attempt: int = Query(ge=1)):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "audio/wav":
        raise speech.SpeechError(400, "unsupported_content_type", "Send the recording as audio/wav.")
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise speech.SpeechError(400, "unsupported_encoding", "Send an uncompressed WAV request body.")
    declared = request.headers.get("content-length")
    try:
        declared_size = int(declared) if declared is not None else None
    except ValueError:
        raise speech.SpeechError(400, "invalid_content_length", "The recording request has an invalid size.") from None
    if declared_size is not None:
        if declared_size < 0:
            raise speech.SpeechError(400, "invalid_content_length", "The recording request has an invalid size.")
        if declared_size > speech.MAX_BYTES:
            raise speech.SpeechError(413, "audio_too_large", "Record at most 20 seconds of audio.")
    await run_in_threadpool(speech.ensure_session, sid, attempt)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > speech.MAX_BYTES:
            raise speech.SpeechError(413, "audio_too_large", "Record at most 20 seconds of audio.")
        body.extend(chunk)
    if declared_size is not None and declared_size != len(body):
        raise speech.SpeechError(400, "invalid_content_length", "The recording request was incomplete. Record it again.")
    return await run_in_threadpool(speech.transcribe, sid, bytes(body), attempt=attempt)


@app.get("/api/content/{chapter_id}")
def content(chapter_id: str):
    if chapter_id != "chapter2":
        raise HTTPException(404, "unknown chapter")
    return get_chapter(chapter_id).public_view()


@app.post("/api/sessions")
def create_session():
    sid, state = session.create()
    return session.public_state(sid, state)


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    return session.public_state(sid, session.load(sid))


@app.get("/api/sessions/{sid}/export")
def export_session(sid: str, include_text: bool = False):
    data, filename = export.make_archive(sid, include_text=include_text)
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@app.post("/api/sessions/{sid}/phase")
def set_phase(sid: str, body: PhaseBody):
    return session.set_phase(sid, body.phase)


@app.put("/api/sessions/{sid}/player")
def set_player(sid: str, body: PlayerBody):
    return session.set_player_name(sid, body.name)


@app.post("/api/sessions/{sid}/location")
def set_location(sid: str, body: LocationBody):
    return session.set_location(sid, body.area)


@app.post("/api/sessions/{sid}/hotspots/{hotspot_id}")
def view_hotspot(sid: str, hotspot_id: str, topic: str | None = Query(default=None, min_length=1, max_length=40)):
    return session.view_hotspot(sid, hotspot_id, topic)


@app.put("/api/sessions/{sid}/prep/draft")
def save_draft(sid: str, body: DraftBody):
    return prepare.save_draft(sid, body.draft)


@app.post("/api/sessions/{sid}/prep/feedback")
def feedback(sid: str, body: DraftBody):
    return prepare.get_feedback(sid, body.draft, request_id=body.request_id)


@app.post("/api/sessions/{sid}/prep/recheck")
def recheck(sid: str, body: DraftBody):
    return writing_feedback.recheck(sid, body.draft, request_id=body.request_id)


@app.post("/api/sessions/{sid}/prep/feedback/decision")
def feedback_decision(sid: str, body: DecisionBody):
    return writing_feedback.set_decision(sid, body.version, body.item_id, body.decision)


@app.post("/api/sessions/{sid}/prep/send")
def send(sid: str, body: EmailBody):
    return prepare.send_email(sid, body.email, acknowledged_open_points=body.acknowledged_open_points)


@app.post("/api/sessions/{sid}/prep/edit")
def edit_again(sid: str):
    return prepare.edit_again(sid)


@app.post("/api/sessions/{sid}/prep/confirm-summary")
def confirm_summary(sid: str):
    return prepare.confirm_summary(sid)


@app.post("/api/sessions/{sid}/prep/prediction")
def prediction(sid: str, body: PredictionBody):
    return prepare.submit_prediction(sid, body.prediction, body.backup_plan)


@app.put("/api/sessions/{sid}/prep/prediction")
def save_prediction(sid: str, body: PredictionBody):
    return prepare.save_prediction(sid, body.prediction, body.backup_plan)


@app.post("/api/sessions/{sid}/negotiate/start")
def negotiate_start(sid: str):
    return negotiate.start(sid)


@app.post("/api/sessions/{sid}/negotiate/turn")
def negotiate_turn(sid: str, body: TurnBody):
    return negotiate.turn(sid, body.text, request_id=body.request_id,
                          input_mode=body.input_mode, asr_text=body.asr_text)


@app.post("/api/sessions/{sid}/negotiate/confirm")
def negotiate_confirm(sid: str):
    return negotiate.confirm(sid)


@app.post("/api/sessions/{sid}/negotiate/hint")
def negotiate_hint(sid: str):
    return negotiate.hint(sid)


@app.post("/api/sessions/{sid}/negotiate/retry")
def negotiate_retry(sid: str):
    return negotiate.retry(sid)


@app.post("/api/sessions/{sid}/negotiate/finish")
def negotiate_finish(sid: str):
    return negotiate.finish(sid)


@app.post("/api/sessions/{sid}/negotiate/resume")
def negotiate_resume(sid: str):
    return negotiate.resume(sid)


@app.get("/api/sessions/{sid}/outcome")
def get_outcome(sid: str):
    return outcome.outcome(sid)
