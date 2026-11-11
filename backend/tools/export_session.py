"""Export one saved play-through without invoking a model or modifying the database."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.export import make_archive
from app.services.session import NotFound


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="The session code shown in the game sidebar")
    parser.add_argument("--output", required=True, type=Path, help="New .zip file; existing files are never overwritten")
    parser.add_argument("--include-text", action="store_true", help="Include writing and dialogue; review before sharing")
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".zip":
        parser.error("--output must be a .zip file")
    try:
        data, _ = make_archive(args.session, include_text=args.include_text)
    except NotFound:
        parser.error("The saved session was not found. Check its code and the configured data folder.")
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as target:
            target.write(data)
    except FileExistsError:
        parser.error("The output file already exists. Choose a new filename.")
    print(f"Saved {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
