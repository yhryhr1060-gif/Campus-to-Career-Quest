"""Serve the built UI against mock responses and a disposable database for manual QA."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["LLM_MOCK"] = "1"

from app.main import app
from fastapi.staticfiles import StaticFiles
import uvicorn


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="ccq-round2-preview-") as temporary_data:
        os.environ["DATA_DIR"] = temporary_data
        app.mount("/", StaticFiles(directory=str(ROOT / "frontend" / "dist"), html=True), name="preview")
        uvicorn.run(app, host="127.0.0.1", port=18743)
