"""Explicit one-time download and local validation of the speech model. No audio is uploaded."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

MODELS = {
    "parakeet": ("nemo-parakeet-tdt-0.6b-v2", "istupakov/parakeet-tdt-0.6b-v2-onnx", "0bbb45a3365852604aef28b538a8f066f4ccaa85"),
    "canary": ("istupakov/canary-180m-flash-onnx", "istupakov/canary-180m-flash-onnx", "92c2231a4e2b2524277fea759be967d2e6edfc49"),
}


def _read_manifest(target: Path) -> dict:
    try:
        value = json.loads((target / "model-info.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _verified_local_copy(target: Path, manifest: dict, required: list[Path], model: str, repo: str, revision: str) -> bool:
    if any(manifest.get(key) != expected for key, expected in
           (("model", model), ("repo", repo), ("revision", revision), ("quantization", "int8"))):
        return False
    recorded = manifest.get("files")
    if not isinstance(recorded, dict) or not all(path.name in recorded for path in required):
        return False
    try:
        for name, info in recorded.items():
            if not isinstance(name, str) or Path(name).name != name or not isinstance(info, dict):
                return False
            path = target / name
            if (not path.resolve().is_relative_to((ROOT / "models").resolve()) or not path.is_file()
                    or path.stat().st_size != info.get("bytes") or path.stat().st_size <= 0):
                return False
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != info.get("sha256"):
                    return False
    except OSError:
        return False
    return True


def main(argv=None) -> int:
    from app.config import get_settings
    from app.services import speech
    parser = argparse.ArgumentParser(description=__doc__)
    configured_backend = get_settings().asr_backend.strip().lower()
    parser.add_argument("--backend", choices=sorted(MODELS), default=configured_backend)
    args = parser.parse_args(argv)
    if args.backend not in MODELS:
        parser.error("Choose --backend parakeet or canary.")
    try:
        import onnx_asr
        import onnxruntime
        from huggingface_hub import snapshot_download
    except ImportError:
        parser.error("Install backend/requirements-speech.txt in the project's Python environment first.")
    model, repo, revision = MODELS[args.backend]
    configured_dir = os.getenv("ASR_MODEL_DIR", "").strip()
    target = Path(configured_dir) if configured_dir else ROOT / "models" / args.backend
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    if not target.is_relative_to((ROOT / "models").resolve()):
        parser.error("ASR_MODEL_DIR must be inside this project's models folder, as required by the speech service.")
    existing = _read_manifest(target)
    if existing.get("model") and existing["model"] != model:
        parser.error("This model directory already contains a different model. Choose a separate ASR_MODEL_DIR.")
    print(f"Preparing {args.backend} in {target}", flush=True)
    started = time.monotonic()
    model_config = speech.ModelConfig(args.backend, model, target)
    try:
        required = speech._required_files(model_config)
    except (speech.SpeechError, OSError):
        required = []
    reuse = bool(required) and _verified_local_copy(target, existing, required, model, repo, revision)
    if reuse:
        print("Verified the existing local files; no model download is needed.", flush=True)
    else:
        print(f"Downloading the pinned model revision {revision}.", flush=True)
        snapshot_download(repo, revision=revision, local_dir=target,
                          allow_patterns=["config.json", "vocab.txt", "*?int8.onnx", "*.onnx?data", "nemo128.onnx", "README.md"],
                          max_workers=2, force_download=bool(existing))
    # The same readiness/path checks used by the request service must pass before loading.
    speech._required_files(model_config)
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    onnx_asr.load_model(model, target, quantization="int8", providers=["CPUExecutionProvider"], sess_options=options)
    files = {}
    for path in sorted(target.iterdir()):
        if path.is_file() and path.name not in {"model-info.json", ".model-info.json.tmp"}:
            with path.open("rb") as source:
                checksum = hashlib.file_digest(source, "sha256").hexdigest()
            files[path.name] = {"bytes": path.stat().st_size, "sha256": checksum}
    manifest = {"model": model, "repo": repo, "revision": revision, "quantization": "int8",
                "provider": "CPUExecutionProvider", "packages": {name: version(name) for name in ("onnx-asr", "onnxruntime", "numpy")},
                "files": files}
    temporary_manifest = target / ".model-info.json.tmp"
    temporary_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary_manifest.replace(target / "model-info.json")
    print(json.dumps({"status": "ready", "backend": args.backend, "seconds": round(time.monotonic() - started, 2),
                      "model_bytes": sum(f["bytes"] for f in files.values())}), flush=True)
    if args.backend != configured_backend:
        print(f"Prepared {args.backend}; the game is still configured for ASR_BACKEND={configured_backend}.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Speech setup could not finish: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
