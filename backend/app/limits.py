"""Shared character limits, measured in Unicode code points (Python string length)."""

MAX_EMAIL_CHARS = 2000
MAX_TURN_CHARS = 600
MAX_PREDICTION_CHARS = 500
MAX_BACKUP_CHARS = 500

INPUT_LIMITS = {"email": MAX_EMAIL_CHARS, "turn": MAX_TURN_CHARS,
                "prediction": MAX_PREDICTION_CHARS, "backup_plan": MAX_BACKUP_CHARS}


class InputTooLong(ValueError):
    pass


def validate_length(text: str, limit: int, label: str) -> None:
    if len(text) > limit:
        raise InputTooLong(f"{label} is {len(text) - limit} characters over the {limit}-character limit. Shorten it and try again.")
