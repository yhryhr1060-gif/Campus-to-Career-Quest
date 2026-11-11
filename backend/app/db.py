"""SQLite storage: session state, an append-only event log, idempotency and XP awards."""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    phase TEXT NOT NULL,
    kind TEXT NOT NULL,
    player_text TEXT,
    audio_path TEXT,
    asr_text TEXT,
    model TEXT,
    prompt_version TEXT,
    raw_output TEXT,
    rule_decision TEXT,
    hint_level INTEGER,
    xp INTEGER,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE TABLE IF NOT EXISTS processed_requests (
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (session_id, request_id)
);
CREATE TABLE IF NOT EXISTS xp_awards (
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount INTEGER NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (session_id, kind)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(s.db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _using_connection(connection: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    """Reuse the caller's transaction, or own a short transaction for a standalone write."""
    if connection is not None:
        yield connection
    else:
        with connect() as owned:
            yield owned


def new_session_id() -> str:
    # Anonymous code, never a real name. Not guessable.
    return "S-" + secrets.token_hex(6).upper()


def create_session(state: dict) -> str:
    sid = new_session_id()
    with connect() as c:
        c.execute(
            "INSERT INTO sessions (id, created_at, state_json) VALUES (?, ?, ?)",
            (sid, now_iso(), json.dumps(state)),
        )
    return sid


def load_state(session_id: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT state_json FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return json.loads(row["state_json"]) if row else None


def save_state(session_id: str, state: dict, *, connection: sqlite3.Connection | None = None) -> None:
    with _using_connection(connection) as c:
        c.execute("UPDATE sessions SET state_json = ? WHERE id = ?", (json.dumps(state), session_id))


def mutate_state(session_id: str, update: Callable[[dict], None]) -> dict | None:
    """Apply a small guarded change to the latest state, atomically.

    Autosaves must never write a stale whole-session snapshot over a completed step.
    Do not run network/model calls inside this transaction.
    """
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT state_json FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        state = json.loads(row["state_json"])
        update(state)
        c.execute("UPDATE sessions SET state_json = ? WHERE id = ?", (json.dumps(state), session_id))
    return state


def log_event(
    session_id: str,
    phase: str,
    kind: str,
    *,
    player_text: str | None = None,
    audio_path: str | None = None,
    asr_text: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    raw_output: str | None = None,
    rule_decision: str | None = None,
    hint_level: int | None = None,
    xp: int | None = None,
    payload: Any = None,
    connection: sqlite3.Connection | None = None,
) -> None:
    with _using_connection(connection) as c:
        c.execute(
            """INSERT INTO events (session_id, ts, phase, kind, player_text, audio_path, asr_text, model,
               prompt_version, raw_output, rule_decision, hint_level, xp, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id, now_iso(), phase, kind, player_text, audio_path, asr_text, model,
                prompt_version, raw_output, rule_decision, hint_level, xp,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            ),
        )


def list_events(session_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM events WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def get_processed(session_id: str, request_id: str, *, connection: sqlite3.Connection | None = None) -> dict | None:
    with _using_connection(connection) as c:
        row = c.execute(
            "SELECT response_json FROM processed_requests WHERE session_id = ? AND request_id = ?",
            (session_id, request_id),
        ).fetchone()
    return json.loads(row["response_json"]) if row else None


def put_processed(session_id: str, request_id: str, response: dict,
                  *, connection: sqlite3.Connection | None = None) -> None:
    with _using_connection(connection) as c:
        c.execute(
            "INSERT OR IGNORE INTO processed_requests (session_id, request_id, response_json) VALUES (?,?,?)",
            (session_id, request_id, json.dumps(response)),
        )


def award_xp(session_id: str, kind: str, amount: int, *, connection: sqlite3.Connection | None = None) -> bool:
    """Award once per (session, kind). Returns True if newly awarded."""
    with _using_connection(connection) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO xp_awards (session_id, kind, amount, ts) VALUES (?,?,?,?)",
            (session_id, kind, amount, now_iso()),
        )
        return cur.rowcount == 1


def total_xp(session_id: str, *, connection: sqlite3.Connection | None = None) -> int:
    with _using_connection(connection) as c:
        row = c.execute("SELECT COALESCE(SUM(amount), 0) AS t FROM xp_awards WHERE session_id = ?", (session_id,)).fetchone()
    return int(row["t"])


class StateChanged(RuntimeError):
    """A prepared action no longer matches the session snapshot it was based on."""


def commit_session_action(
    session_id: str,
    *,
    expected_state: dict,
    state: dict,
    event: dict,
    response: dict,
    request_id: str | None = None,
    xp_award: tuple[str, int] | None = None,
) -> dict | None:
    """Commit one completed action and its replay response as a single unit.

    Build the new state, model output, event and public response before calling this
    function. No model/network calls or callbacks run in the transaction. The cached
    response gets the XP total from this transaction, including any new award.
    """
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if request_id is not None:
            cached = get_processed(session_id, request_id, connection=c)
            if cached is not None:
                return cached
        row = c.execute("SELECT state_json FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        if json.loads(row["state_json"]) != expected_state:
            raise StateChanged("the conversation changed while this action was being prepared")

        save_state(session_id, state, connection=c)
        awarded = 0
        if xp_award is not None and award_xp(session_id, *xp_award, connection=c):
            awarded = xp_award[1]
        log_event(session_id, **{**event, "xp": awarded}, connection=c)
        completed = {**response, "xp": total_xp(session_id, connection=c)}
        if request_id is not None:
            put_processed(session_id, request_id, completed, connection=c)
    return completed


def read_session_snapshot(session_id: str) -> dict | None:
    """Read export evidence from one consistent, read-only SQLite snapshot.

    This path neither creates a data directory/database nor initializes the schema.
    It returns stored evidence only; it must never generate missing model feedback.
    """
    path = get_settings().db_path
    if not path.is_file():
        return None
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        row = conn.execute("SELECT created_at, state_json FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        events = conn.execute("SELECT * FROM events WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        xp = conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM xp_awards WHERE session_id = ?",
                          (session_id,)).fetchone()["total"]
        return {"created_at": row["created_at"], "state": json.loads(row["state_json"]),
                "events": [dict(event) for event in events], "xp": int(xp)}
    finally:
        conn.rollback()  # End the read transaction without issuing any write.
        conn.close()
