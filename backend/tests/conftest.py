import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test gets its own empty data folder and runs in mock mode unless it says otherwise."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_MOCK", "1")
    yield


@pytest.fixture
def chapter():
    from app.content import get_chapter
    return get_chapter("chapter2")
