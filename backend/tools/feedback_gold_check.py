"""Score the whole-draft check against the expected-answer cases in tests/fixtures/feedback_gold.yaml.

    backend\\.venv\\Scripts\\python.exe backend\\tools\\feedback_gold_check.py --live --output reports\\feedback-gold.json

--live calls the model configured in .env and therefore costs API usage; it is never run by the
test suite. Without --live the script refuses to score, because the mock model only recognises a
few fixed patterns and scoring it would say nothing about feedback quality.
The expectations themselves must be reviewed by a human teacher (see the fixture header); this
script compares output with them and does not ask any model to grade itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
GOLD = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "feedback_gold.yaml"


def load_cases(path: Path = GOLD) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]


def _overlaps(quote: str, words: str, email: str) -> bool:
    from app.rules.quotes import normalize
    text, q, w = normalize(email), normalize(quote), normalize(words)
    qs, ws = text.find(q), text.find(w)
    return qs >= 0 and ws >= 0 and qs < ws + len(w) and ws < qs + len(q)


def score_case(case: dict, items: list[dict]) -> dict:
    """Pure comparison of shown points with the expectations of one case."""
    missed, false_errors = [], []
    for expected in case.get("must_flag", []):
        hit = any(i["type"] in expected["types"] and i["judgement"] != "optional"
                  and _overlaps(i["quote"], expected["words"], case["email"]) for i in items)
        if not hit:
            missed.append(expected["words"])
    for acceptable in case.get("must_not_error", []):
        if any(i["judgement"] == "clear_error" and _overlaps(i["quote"], acceptable, case["email"]) for i in items):
            false_errors.append(acceptable)
    return {"id": case["id"], "human_reviewed": bool(case.get("reviewed_by")), "points_shown": len(items),
            "missed": missed, "false_errors": false_errors, "passed": not missed and not false_errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="call the configured model (uses your API key and costs usage)")
    parser.add_argument("--output")
    args = parser.parse_args()
    if not args.live:
        print("Refusing to score the mock model: it is a keyword stand-in. Re-run with --live to use the model in .env.")
        return 2
    import tempfile
    os.environ["LLM_MOCK"] = "0"
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ccq-gold-")   # never the player's saved data
    from app.config import get_settings
    from app.services import prepare, session
    results = []
    for case in load_cases():
        sid, _ = session.create()
        out = prepare.get_feedback(sid, case["email"])
        row = score_case(case, out["items"]) if out["available"] else {"id": case["id"], "passed": None, "error": "feedback_unavailable"}
        results.append(row)
        print(row)
    report = {"model": get_settings().model_label, "cases": results,
              "passed": sum(r["passed"] is True for r in results), "failed": sum(r["passed"] is False for r in results),
              "unavailable": sum(r["passed"] is None for r in results),
              "human_reviewed_cases": sum(bool(r.get("human_reviewed")) for r in results),
              "note": "Expectations not marked human_reviewed are drafts. Passing does not show that all errors are found."}
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
    return 0 if report["failed"] == 0 and report["unavailable"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
