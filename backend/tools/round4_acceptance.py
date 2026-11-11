"""Local speech smoke check with synthetic WAV fixtures, a disposable DB and mock dialogue."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
SOURCES = [
    ("reason", "I have been ill for three days.", ["ill", "days"]),
    ("proposal", "Could I submit the full report by Saturday at noon?", ["report", "saturday", "noon"]),
    ("confirmation", "Yes, that is right. I will make sure it is on time.", ["yes", "time"]),
]


def parse_args(argv=None):
    samples = "\n".join(f"  round4-{name}.wav: {reference}" for name, reference, _ in SOURCES)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Synthetic WAV fixtures are not included with the public code. Create your own "
               "synthetic English speech using these exact filenames and spoken text:\n" + samples)
    parser.add_argument("--fixtures-dir", type=Path, default=ROOT / "output" / "playwright",
                        help="Directory containing the three synthetic round4-*.wav samples")
    args = parser.parse_args(argv)
    missing = [f"round4-{name}.wav" for name, _, _ in SOURCES
               if not (args.fixtures_dir / f"round4-{name}.wav").is_file()]
    if missing:
        parser.error("Synthetic WAV fixtures are not included with the public code. "
                     "Missing files: " + ", ".join(missing) + ".\n"
                     "Create your own synthetic English speech WAV files with these exact names "
                     "and spoken text, then pass --fixtures-dir PATH:\n" + samples)
    return args


def main(argv=None):
    args = parse_args(argv)  # Validate before importing the app or loading any ASR model.
    os.environ["LLM_MOCK"] = "1"
    with tempfile.TemporaryDirectory(prefix="ccq-round4-") as temporary:
        os.environ["DATA_DIR"] = temporary
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services import export, negotiate, prepare, session

        client = TestClient(app)
        status = client.get("/api/speech/status").json()
        assert status["available"], status
        sid, _ = session.create()
        prepare.send_email(sid, "Dear Prof. Chen, I have been ill for three days. Could I submit next Wednesday? Thanks, Alex")
        prepare.confirm_summary(sid)
        prepare.submit_prediction(sid, "He may ask for a plan.", "Offer an outline first.")
        negotiate.start(sid)
        results = []
        for index, (name, reference, words) in enumerate(SOURCES):
            fixture = args.fixtures_dir / f"round4-{name}.wav"
            before = session.load(sid)
            started = time.perf_counter()
            response = client.post(f"/api/sessions/{sid}/speech/transcribe?attempt=1",
                                   content=fixture.read_bytes(), headers={"content-type": "audio/wav"})
            elapsed = round(time.perf_counter() - started, 3)
            assert response.status_code == 200, response.text
            result = response.json()
            assert all(word in result["text"].lower() for word in words), result
            assert session.load(sid) == before, "ASR must not change game state"
            # The second message represents a learner correcting the question into an offer.
            sent = "I can submit the full report by Saturday at noon." if index == 1 else result["text"]
            reply = client.post(f"/api/sessions/{sid}/negotiate/turn", json={
                "text": sent, "input_mode": "voice_edited" if index == 1 else "voice",
                "asr_text": result["text"], "request_id": f"round4-synthetic-{index}",
            })
            assert reply.status_code == 200, reply.text
            assert reply.json().get("available") is not False, reply.text
            results.append({"fixture": fixture.name, "reference": reference,
                            "transcript": result["text"], "submitted": sent,
                            "input_mode": "voice_edited" if index == 1 else "voice",
                            "duration_seconds": result["duration_seconds"], "elapsed_seconds": elapsed,
                            "required_words_present": True, "state_unchanged_until_send": True})
        state = session.load(sid)
        assert state["negotiation"]["agreement"]["status"] == "confirmed", state["negotiation"]["agreement"]
        negotiate.finish(sid)
        record = export.build_record(sid)
        assert record["summary"]["completed_learner_turns"] == 3
        assert record["summary"]["speech_transcriptions"] == 3
        assert record["summary"]["speech_failures"] == 0
        assert not record["data_quality_warnings"], record["data_quality_warnings"]
        output = ROOT / "reports" / "round4"
        output.mkdir(parents=True, exist_ok=True)
        (output / "sample-voice-summary.zip").write_bytes(export.make_archive(sid)[0])
        report = {"status": "passed", "speech": "real local Parakeet CPU int8", "dialogue": "mock",
                  "input_source": "User-supplied synthetic English speech; no physical microphone",
                  "samples": results, "record_summary": record["summary"],
                  "limitations": ["Three clean synthetic English samples only; not an accuracy benchmark.",
                                  "Physical microphone, noise, learner accents and other browsers remain unverified."]}
        (output / "real-asr-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
