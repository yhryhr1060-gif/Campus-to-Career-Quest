"""Start the backend and keep a log in data/logs/backend.log (also shown in the console).

Usage (from backend/):  .venv\\Scripts\\python.exe serve.py [--reload]

Hard crashes (segfaults etc.) are written to data/logs/backend-crash.log, and a clean shutdown
writes "backend exiting", so a log that just stops means the window/process was killed.
"""
from __future__ import annotations

import copy
import faulthandler
import logging
import sys
from datetime import datetime
from pathlib import Path

import uvicorn
from uvicorn.config import LOGGING_CONFIG

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_crash = open(LOG_DIR / "backend-crash.log", "a", encoding="utf-8")
_crash.write(f"\n--- start {datetime.now().isoformat(timespec='seconds')} python {sys.version.split()[0]}\n")
_crash.flush()
faulthandler.enable(_crash)


def _log_config() -> dict:
    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["formatters"]["file"] = {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}
    cfg["handlers"]["file"] = {
        "class": "logging.FileHandler",
        "filename": str(LOG_DIR / "backend.log"),
        "encoding": "utf-8",
        "formatter": "file",
    }
    for name in ("uvicorn", "uvicorn.access"):
        cfg["loggers"][name]["handlers"].append("file")
    cfg["loggers"]["app"] = {"handlers": ["default", "file"], "level": "INFO", "propagate": False}
    return cfg


def _excepthook(exc_type, exc, tb):
    logging.getLogger("app").critical("uncaught exception", exc_info=(exc_type, exc, tb))
    sys.__excepthook__(exc_type, exc, tb)


if __name__ == "__main__":
    sys.excepthook = _excepthook
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload="--reload" in sys.argv,
        log_config=_log_config(),
    )
    logging.getLogger("app").info("backend exiting (normal shutdown)")
