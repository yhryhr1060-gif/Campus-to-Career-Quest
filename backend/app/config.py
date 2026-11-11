"""Settings read from environment / .env. Read on every call so tests can monkeypatch."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # project root (contains backend/, frontend/, content/)
CONTENT_DIR = ROOT / "content"

try:  # python-dotenv is optional at import time (tests run without it)
    from dotenv import load_dotenv

    # Isolated acceptance must be able to run without opening any credential file.
    # Normal launches retain their existing configuration behavior.
    if os.getenv("CCQ_SKIP_DOTENV") != "1":
        load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    llm_mock: bool
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    asr_backend: str
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def model_label(self) -> str:
        return "mock" if self.llm_mock else self.llm_model


def get_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR") or (ROOT / "data"))
    return Settings(
        llm_mock=os.getenv("LLM_MOCK", "1").strip() in {"1", "true", "yes"},
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", "deepseek-flash"),
        llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        asr_backend=os.getenv("ASR_BACKEND", "parakeet"),
        data_dir=data_dir,
    )
