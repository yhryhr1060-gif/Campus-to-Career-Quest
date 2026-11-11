"""Fixture validation must not require FastAPI, credentials, or a real ASR model."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import round4_acceptance as acceptance


def test_default_fixture_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(acceptance, "ROOT", tmp_path)
    directory = tmp_path / "output" / "playwright"
    directory.mkdir(parents=True)
    for name, _, _ in acceptance.SOURCES:
        (directory / f"round4-{name}.wav").touch()
    assert acceptance.parse_args([]).fixtures_dir == directory


def test_custom_fixture_directory(tmp_path):
    for name, _, _ in acceptance.SOURCES:
        (tmp_path / f"round4-{name}.wav").touch()
    assert acceptance.parse_args(["--fixtures-dir", str(tmp_path)]).fixtures_dir == tmp_path


def test_partial_fixture_set_reports_only_missing(tmp_path, capsys):
    (tmp_path / "round4-reason.wav").touch()
    with pytest.raises(SystemExit) as error:
        acceptance.parse_args(["--fixtures-dir", str(tmp_path)])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "Missing files: round4-proposal.wav, round4-confirmation.wav." in message
    assert "not included with the public code" in message
    assert "synthetic English speech" in message
    assert "--fixtures-dir PATH" in message


def test_missing_fixtures_fail_before_app_import(tmp_path):
    # -S disables site packages: any premature FastAPI/ASR import would traceback.
    result = subprocess.run(
        [sys.executable, "-S", str(Path(acceptance.__file__)),
         "--fixtures-dir", str(tmp_path / "absent")],
        capture_output=True, text=True,
        env=dict(os.environ, CCQ_SKIP_DOTENV="1", PYTHONDONTWRITEBYTECODE="1"),
    )
    assert result.returncode == 2
    assert "not included with the public code" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "absent").exists()
