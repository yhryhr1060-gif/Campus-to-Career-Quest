"""Round-6 acceptance isolation and export metadata, using synthetic records only."""
import os
from pathlib import Path
import subprocess
import sys

from app.services.export import _draft_versions


def test_acceptance_skip_prevents_dotenv_from_being_called():
    code = """
import sys, types
stub = types.ModuleType('dotenv')
def forbidden(*args, **kwargs):
    raise AssertionError('credential file loader must not run')
stub.load_dotenv = forbidden
sys.modules['dotenv'] = stub
from app.config import get_settings
assert get_settings().llm_mock
print('dotenv skipped')
"""
    env = dict(os.environ, CCQ_SKIP_DOTENV="1", LLM_MOCK="1", LLM_API_KEY="")
    result = subprocess.run([sys.executable, "-c", code], env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "dotenv skipped"


def test_export_keeps_retry_and_limit_metadata_without_leaking_draft_or_request_ids():
    prep = {"versions": [{"version": 1, "text": "synthetic learner text", "origin": "check",
        "request_id": "private-attempt-id", "feedback": {"status": "ok", "review_status": "partial",
        "review_retryable": True, "item_limit": 12, "possibly_incomplete": True, "over_limit_count": 2,
        "items": [{"id": "v1-1", "type": "capitalization", "judgement": "clear_error", "flags": [],
                   "quote": "i", "suggestion": "I", "reason": "Capitalize I."}]},
        "recheck": {"status": "partial", "retryable": True, "base_version": 1, "results": [
            {"id": "v1-1", "status": "cannot_judge", "basis": "evidence_missing", "retryable": True,
             "current_quote": "", "note": ""}]}}]}
    rows, _ = _draft_versions(prep, False)
    row = rows[0]
    assert row["points_by_type"] == {"capitalization": 1}
    assert row["review_status"] == "partial" and row["review_retryable"] is True
    assert row["feedback_item_limit"] == 12 and row["feedback_over_limit_count"] == 2
    assert row["feedback_possibly_incomplete"] is True
    assert row["recheck_retryable"] is True and row["recheck_status"] == "partial"
    assert row["recheck_by_basis"] == {"evidence_missing": 1}
    assert "synthetic learner text" not in str(rows) and "private-attempt-id" not in str(rows)
    assert "points" not in row and "recheck_results" not in row
    text_rows, _ = _draft_versions(prep, True)
    assert text_rows[0]["points"][0]["type"] == "capitalization"
    assert text_rows[0]["recheck_results"][0]["retryable"] is True


def test_legacy_export_does_not_invent_retry_or_limit_observations():
    rows, _ = _draft_versions({"versions": [{"version": 1, "feedback": {"status": "ok", "items": []}}]}, False)
    assert rows[0]["review_retryable"] is None
    assert rows[0]["feedback_item_limit"] is None
    assert rows[0]["feedback_possibly_incomplete"] is None
    assert rows[0]["recheck_retryable"] is None
