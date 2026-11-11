"""Repeat both chapter endings through HTTP routes using disposable synthetic sessions.

Defaults to mock mode. --live explicitly uses the configured model and incurs API usage.
Neither mode reads or overwrites the player's saved database.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "round1" / "acceptance.json")
    args = parser.parse_args()
    if not 1 <= args.runs <= 5:
        parser.error("--runs must be between 1 and 5")

    os.environ["LLM_MOCK"] = "0" if args.live else "1"
    from app.config import get_settings
    if args.live and not get_settings().llm_api_key:
        parser.error("The configured model has no API key")

    from fastapi.testclient import TestClient
    from app import db
    from app.main import app
    from app.llm.prompt_templates import VERSIONS

    records = []
    with tempfile.TemporaryDirectory(prefix="ccq-round1-acceptance-") as data_dir:
        os.environ["DATA_DIR"] = data_dir
        with TestClient(app) as client:
            def request(method, path, body=None):
                response = client.request(method, path, json=body)
                if response.status_code != 200:
                    raise AssertionError(f"{method} {path.rsplit('/', 1)[-1]}: HTTP {response.status_code}")
                return response.json()

            for run in range(1, args.runs + 1):
                for ending, offer in [
                    ("full", "I could finish the whole report by Saturday at noon."),
                    ("staged", "I could send you the outline by Friday and the full report by Sunday evening."),
                ]:
                    record = {"run": run, "ending": ending, "passed": False, "steps": []}
                    sid = None
                    try:
                        state = request("POST", "/api/sessions")
                        sid = state["session_id"]
                        base = f"/api/sessions/{sid}"
                        request("POST", base + "/hotspots/syllabus")
                        request("POST", base + "/phase", {"phase": "prepare"})
                        email = ("Dear Prof. Chen, I have been sick for three days, so I haven't been able "
                                 "to finish the project report by Friday. Would it be possible to hand "
                                 "it in next Wednesday? Thanks, Alex")
                        feedback = request("POST", base + "/prep/feedback", {"draft": email})
                        assert feedback["available"], "email feedback unavailable"
                        state = request("POST", base + "/prep/send", {"email": email})
                        assert state["summary_available"] and state["prep"]["summary"], "summary unavailable"
                        request("POST", base + "/prep/confirm-summary")
                        request("POST", base + "/prep/prediction", {
                            "prediction": "Wednesday might be too late.", "backup_plan": "Offer an earlier plan.",
                        })
                        request("POST", base + "/negotiate/start")
                        record["steps"].append("prepared")
                        for index, text in enumerate([
                            "Could I submit the full report next Wednesday?", offer,
                            "Yes, that's right. I'll make sure they're on time.",
                        ], start=1):
                            state = request("POST", base + "/negotiate/turn", {
                                "text": text, "request_id": f"round1-{run}-{ending}-{index}",
                            })
                            assert state.get("available") is True, f"model unavailable on turn {index}"
                            record["steps"].append(state["decision"])
                            expected = ["constraint_change", "restate", "confirmed"][index - 1]
                            assert state["decision"] == expected, f"turn {index}: expected {expected}, got {state['decision']}"
                        request("POST", base + "/negotiate/finish")
                        result = request("GET", base + "/outcome")
                        restored = request("GET", base)
                        assert result["ending"]["id"] == ending
                        assert restored["phase"] == "outcome" and restored["xp"] == 50
                        assert any(note["kind"] == "did_well" for note in result["strategy"])
                        record.update(passed=True, notes=result["notes"], turns=result["turns"], xp=result["xp"])
                    except Exception as exc:
                        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    if sid:
                        events = db.list_events(sid)
                        calls = [event for event in events if event.get("raw_output")]
                        bad = [event["kind"] for event in events
                               if "failed" in (event.get("rule_decision") or "")
                               or "fallback" in (event.get("rule_decision") or "")]
                        record["failed_or_fallback_events"] = bad
                        record["model_calls"] = len(calls)
                        record["attempt_latency_ms"] = [duration for event in calls
                            for duration in json.loads(event["raw_output"]).get("latency_ms", [])]
                        record["recovered_attempt_errors"] = sum(
                            len(json.loads(event["raw_output"]).get("errors", [])) for event in calls)
                        if bad:
                            record["passed"] = False
                    records.append(record)
                    print(json.dumps(record, ensure_ascii=False), flush=True)

    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "live": args.live,
              "model": get_settings().model_label, "prompt_versions": VERSIONS,
              "passed": sum(record["passed"] for record in records), "total": len(records), "runs": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
