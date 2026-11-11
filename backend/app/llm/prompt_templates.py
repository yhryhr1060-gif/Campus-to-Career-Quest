"""Versioned prompt templates. The version string is written to the log with every model call."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPT_DIR = Path(__file__).parent / "prompts"

VERSIONS = {
    "npc_system": "npc_system@0.3.2",
    "prep_feedback": "prep_feedback@0.4",
    "prep_feedback_review": "prep_feedback_review@0.2",
    "prep_recheck": "prep_recheck@0.2",
    "prep_summary": "prep_summary@0.1",
    "opening": "opening@0.1",
    "turn_feedback": "turn_feedback@0.1",
}

_env = Environment(loader=FileSystemLoader(PROMPT_DIR), undefined=StrictUndefined,
                   trim_blocks=False, lstrip_blocks=False, autoescape=False)


def render(name: str, **kw) -> str:
    return _env.get_template(f"{name}.j2").render(**kw)
