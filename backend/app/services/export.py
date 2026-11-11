"""Versioned, read-only records. Explicit allowlists keep raw model/state data out of downloads."""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import json
import math
import re
import zipfile

from .. import db
from .session import NotFound

SCHEMA_VERSION = "ccq-record@1"
PHASES = {"session", "explore", "prepare", "negotiate", "outcome"}
KINDS = {"session_created", "phase_entered", "hotspot_viewed", "location_entered", "player_named", "feedback", "email_sent",
         "back_to_edit", "summary_confirmed", "prediction", "opening", "opening_failed", "turn",
         "turn_failed", "confirm_button", "hint_opened", "retry", "resumed", "language_notes", "outcome_shown",
         "speech_transcribed", "speech_failed", "feedback_review", "recheck", "feedback_decision"}
SPEECH_KINDS = {"speech_transcribed", "speech_failed"}
SPEECH_ERRORS = {"audio_too_large", "audio_too_long", "audio_too_short", "invalid_wav", "invalid_wav_size",
                 "unsupported_audio_format", "audio_silent", "no_speech_recognized", "transcript_too_long",
                 "unsupported_backend", "model_directory", "model_missing", "model_incomplete",
                 "dependencies_missing", "audio_processing_failed", "speech_busy", "model_load_failed",
                 "recognition_failed", "invalid_transcript", "model_unavailable"}
MODEL_KINDS = {"feedback", "feedback_review", "recheck", "email_sent", "opening", "opening_failed", "turn", "turn_failed", "language_notes"}
DECISIONS = {"none", "ok", "no_lines", "model_failed", "invalid_opening_source", "fallback_opening", "already_confirmed",
             "confirm_rejected_by_rules", "confirmed", "withdrawn", "confirm_unclear", "incomplete",
             "constraint_change", "rejected", "follow_up", "restate", "nothing_to_confirm", "no_model_needed"}
MODES = {"mock", "live", "custom", "unknown"}
INPUT_MODES = {"typed", "voice", "voice_edited"}


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _json_dict(value) -> dict:
    try:
        return _dict(json.loads(value)) if isinstance(value, str) else {}
    except (ValueError, TypeError):
        return {}


def _number(value, minimum=0):
    return value if type(value) is int and value >= minimum else None


def _duration(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 20 else None


def _boolean(value):
    return value if type(value) is bool else None


def _enum(value, allowed, default=None):
    return value if isinstance(value, str) and value in allowed else default


def _meta(value):
    # Metadata is separate from learner-authored text. Unexpected legacy values are withheld.
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./:@+\-]{1,120}", value) else None


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _elapsed(origin, value):
    target = _time(value)
    return round((target - origin).total_seconds(), 3) if origin and target and target >= origin else None


def _mode(event, raw, kind):
    if kind not in MODEL_KINDS or event.get("rule_decision") in ("no_lines", "no_model_needed"):
        return "not_applicable", "no_model_operation"
    if "execution_mode" in raw:
        recorded = _enum(raw["execution_mode"], MODES)
        return (recorded, "recorded_transport_branch") if recorded else ("unknown", "invalid_transport_metadata")
    if event.get("model") == "mock":
        return "mock", "legacy_model_label"
    return "unknown", "missing_transport_metadata"


def _events(snapshot, include_text, warnings):
    origin = _time(snapshot.get("created_at"))
    source = _list(snapshot.get("events"))
    cursor = None
    rows = []
    for sequence, value in enumerate(source, 1):
        event = _dict(value)
        payload = _json_dict(event.get("payload_json"))
        raw = _json_dict(event.get("raw_output"))
        phase = _enum(event.get("phase"), PHASES, "unknown")
        kind = _enum(event.get("kind"), KINDS, "unknown")
        if phase == "unknown" or kind == "unknown":
            warnings.append({"code": "event_metadata_invalid", "sequence": sequence})
        explicit = _number(payload.get("attempt"), 1)
        invalid_attempt = "attempt" in payload and explicit is None
        if kind == "session_created" and phase == "session":
            # A later creation record cannot retroactively identify earlier events.
            if sequence == 1:
                cursor = 1
            else:
                cursor = None
                warnings.append({"code": "attempt_boundary_ambiguous", "sequence": sequence})
        if phase == "negotiate" and kind == "retry":
            # Missing retry metadata is a gap, not permission to invent the next attempt number.
            if explicit is not None and (cursor is None or explicit > cursor):
                cursor = explicit
            else:
                cursor = None
                warnings.append({"code": "attempt_boundary_ambiguous", "sequence": sequence})
        elif phase in ("negotiate", "outcome") and explicit is not None and cursor is not None and explicit != cursor:
            warnings.append({"code": "attempt_sequence_conflict", "sequence": sequence})
            cursor = None
        attempt = (explicit if explicit is not None else cursor) if phase in ("negotiate", "outcome") and not invalid_attempt else None
        assignment = "explicit" if explicit is not None and attempt is not None else "event_sequence" if attempt is not None else "unassigned"
        decision = event.get("rule_decision") or ""
        parts = decision.split("+") if isinstance(decision, str) else []
        base = parts[0] if parts and parts[0] in DECISIONS else "unknown"
        failure = kind in ("turn_failed", "opening_failed") or base in ("model_failed", "invalid_opening_source", "fallback_opening")
        completed = kind == "turn" and phase == "negotiate" and payload.get("counted") is True and not failure
        legacy_turn = kind == "turn" and phase == "negotiate" and payload.get("counted") is not True and not failure
        checked = payload if kind == "confirm_button" else _dict(payload.get("decision"))
        confirmation = ("button" if kind == "confirm_button" else "text") if (
            phase == "negotiate" and kind in ("turn", "confirm_button") and base == "confirmed"
            and checked.get("became_confirmed") is True and checked.get("kind", "confirmed") == "confirmed"
            and not failure) else None
        latencies = raw.get("latency_ms")
        valid_latencies = isinstance(latencies, list) and bool(latencies) and all(_number(n) is not None for n in latencies)
        mode, mode_source = _mode(event, raw, kind)
        row = {
            "sequence": sequence, "elapsed_seconds": _elapsed(origin, event.get("ts")),
            "phase": phase, "kind": kind, "attempt": attempt, "attempt_source": assignment,
            "completed_learner_turn": completed, "unclassified_legacy_turn": legacy_turn,
            "technical_failure": failure, "rule_decision": base,
            "reply_guarded": "reply_guarded" in parts, "paused": "paused" in parts,
            "confirmation_method": confirmation,
            "reported_input_mode": _enum(payload.get("input_mode"), INPUT_MODES),
            "learner_characters": len(event["player_text"]) if isinstance(event.get("player_text"), str) else None,
            "hint_level": _number(event.get("hint_level")), "xp_awarded": _number(event.get("xp")),
            "model": _meta(event.get("model")), "prompt_version": _meta(event.get("prompt_version")),
            "model_mode": mode, "model_mode_source": mode_source,
            "model_attempts": len(latencies) if mode != "not_applicable" and valid_latencies else None,
            "model_latency_ms": sum(latencies) if mode != "not_applicable" and valid_latencies else None,
            "model_error_count": len(raw["errors"]) if mode != "not_applicable" and isinstance(raw.get("errors"), list) else None,
            "speech_mode": "local" if kind in SPEECH_KINDS and payload.get("execution_mode") == "local" else None,
            "speech_backend": _enum(payload.get("backend"), {"parakeet", "canary"}) if kind in SPEECH_KINDS else None,
            "speech_duration_seconds": _duration(payload.get("duration_seconds")) if kind in SPEECH_KINDS else None,
            "speech_latency_ms": _number(payload.get("latency_ms")) if kind in SPEECH_KINDS else None,
            "speech_transcript_characters": _number(payload.get("transcript_chars")) if kind in SPEECH_KINDS else None,
            "speech_error_code": _enum(payload.get("error_code"), SPEECH_ERRORS) if kind == "speech_failed" else None,
        }
        if include_text:
            row["player_text"] = event.get("player_text") if isinstance(event.get("player_text"), str) else None
            # Only the line actually shown, never model_reply, candidate facts or raw provider output.
            row["npc_reply"] = payload.get("reply") if kind in ("turn", "opening") and isinstance(payload.get("reply"), str) else None
            row["backup_plan"] = payload.get("backup_plan") if kind == "prediction" and isinstance(payload.get("backup_plan"), str) else None
        rows.append(row)
    return rows


FEEDBACK_TYPES = {"grammar", "spelling", "punctuation", "capitalization", "accuracy", "logic", "task_info",
                  "politeness", "naturalness", "positive"}
JUDGEMENTS = {"clear_error", "expression_issue", "optional", "uncertain"}
FEEDBACK_FLAGS = {"changes_fact", "adds_unstated_fact", "changes_negation", "large_rewrite", "style_downgraded",
                  "not_reviewed", "reviewer_disagrees", "reviewer_unsure"}
RECHECK_STATUSES = {"resolved", "still_present", "kept_by_user", "cannot_judge"}
RECHECK_BASES = {"sentence_unchanged", "learner_kept", "ai_recheck", "ai_unavailable", "ai_no_answer",
                 "ai_unsure", "evidence_not_in_draft", "evidence_missing", "context_recheck_needed"}
FEEDBACK_STATES = {"none", "checked_this_version", "rechecked_this_version", "changed_since_check"}
_TEXT = lambda value: value if isinstance(value, str) else None  # noqa: E731


def _draft_versions(prep: dict, include_text: bool) -> tuple[list[dict], list[dict]]:
    """Per-version feedback use, from stored evidence only. Texts appear only when opted in."""
    versions = []
    for raw in _list(prep.get("versions")):
        version = _dict(raw)
        feedback, recheck = _dict(version.get("feedback")), _dict(version.get("recheck"))
        items = [_dict(i) for i in _list(feedback.get("items"))]
        results = [_dict(r) for r in _list(recheck.get("results"))]
        row = {
            "version": _number(version.get("version"), 1),
            "origin": _enum(version.get("origin"), {"check", "recheck", "send"}),
            "characters": len(version["text"]) if isinstance(version.get("text"), str) else None,
            "feedback_status": _enum(feedback.get("status"), {"ok", "unavailable", "pending"}, "none"),
            "feedback_prompt_version": _meta(feedback.get("prompt_version")),
            "review_status": _enum(feedback.get("review_status"), {"ok", "unavailable", "partial", "skipped"}),
            "review_retryable": _boolean(feedback.get("review_retryable")),
            "feedback_item_limit": _number(feedback.get("item_limit"), 1),
            "feedback_possibly_incomplete": _boolean(feedback.get("possibly_incomplete")),
            "feedback_over_limit_count": _number(feedback.get("over_limit_count")),
            "points_shown": len(items) if feedback.get("status") == "ok" else None,
            "points_dropped_by_rules": _number(feedback.get("dropped_count")),
            "points_by_type": dict(sorted(Counter(_enum(i.get("type"), FEEDBACK_TYPES, "unknown") for i in items).items())),
            "points_by_judgement": dict(sorted(Counter(_enum(i.get("judgement"), JUDGEMENTS, "unknown") for i in items).items())),
            "rule_and_review_flags": dict(sorted(Counter(f for i in items for f in _list(i.get("flags")) if f in FEEDBACK_FLAGS).items())),
            "points_learner_kept": sum(v == "keep" for v in _dict(version.get("decisions")).values()),
            "recheck_base_version": _number(recheck.get("base_version"), 1),
            "recheck_status": _enum(recheck.get("status"), {"ok", "partial", "unavailable"}),
            "recheck_retryable": _boolean(recheck.get("retryable")),
            "recheck_by_status": dict(sorted(Counter(_enum(r.get("status"), RECHECK_STATUSES, "unknown") for r in results).items())),
            "recheck_by_basis": dict(sorted(Counter(_enum(r.get("basis"), RECHECK_BASES, "unknown") for r in results).items())),
        }
        if include_text:
            row["text"] = _TEXT(version.get("text"))
            row["points"] = [{"id": _meta(i.get("id")), "type": _enum(i.get("type"), FEEDBACK_TYPES, "unknown"),
                              "judgement": _enum(i.get("judgement"), JUDGEMENTS, "unknown"),
                              "layer": _number(i.get("layer"), 1),
                              "flags": [f for f in _list(i.get("flags")) if f in FEEDBACK_FLAGS],
                              "kept_by_learner": _dict(version.get("decisions")).get(i.get("id")) == "keep",
                              "quote": _TEXT(i.get("quote")), "suggestion": _TEXT(i.get("suggestion")),
                              "reason": _TEXT(i.get("reason")), "review_note": _TEXT(i.get("review_note"))} for i in items]
            row["recheck_results"] = [{"id": _meta(r.get("id")), "status": _enum(r.get("status"), RECHECK_STATUSES, "unknown"),
                                       "basis": _enum(r.get("basis"), RECHECK_BASES, "unknown"),
                                       "retryable": _boolean(r.get("retryable")),
                                       "current_quote": _TEXT(r.get("current_quote")), "note": _TEXT(r.get("note"))} for r in results]
        versions.append(row)
    sends = []
    for raw in _list(prep.get("send_decisions")):
        decision = _dict(raw)
        sends.append({"version": _number(decision.get("version"), 1),
                      "feedback_state": _enum(decision.get("feedback_state"), FEEDBACK_STATES, "unknown"),
                      "base_version": _number(decision.get("base_version"), 1),
                      "open_points": len(_list(decision.get("open_points"))),
                      "kept_points": len(_list(decision.get("kept_points"))),
                      "resolved_points": len(_list(decision.get("resolved_points"))),
                      "uncertain_points": len(_list(decision.get("uncertain_points"))),
                      "acknowledged_open_points": _boolean(decision.get("acknowledged_open_points"))})
    return versions, sends


def _exploration_ids(chapter_id) -> dict[str, set[str]]:
    try:
        from ..content import get_chapter
        chapter = get_chapter(chapter_id) if isinstance(chapter_id, str) else None
    except Exception:
        chapter = None
    if chapter is None:
        return {"areas": set(), "clues": set(), "expressions": set()}
    clues = {h.id for h in chapter.hotspots if h.note} | {f"{h.id}:{t.id}" for h in chapter.hotspots for t in h.topics if t.note}
    return {"areas": {a.id for a in chapter.areas}, "clues": clues, "expressions": {e.id for e in chapter.expressions}}


def build_record(sid: str, *, include_text: bool = False) -> dict:
    snapshot = db.read_session_snapshot(sid)
    if snapshot is None:
        raise NotFound(sid)
    include_text = include_text is True
    state = _dict(snapshot.get("state"))
    warnings = []
    if not isinstance(snapshot.get("state"), dict):
        warnings.append({"code": "state_snapshot_invalid"})
    events = _events(snapshot, include_text, warnings)
    attempts = []
    conversations = []
    archived = _list(state.get("archived_attempts"))
    raw_sources = [(neg, True) for neg in archived] + [(state.get("negotiation"), False)]
    sources = [(neg, old) for neg, old in raw_sources if isinstance(neg, dict)]
    if len(sources) != len(raw_sources):
        warnings.append({"code": "attempt_snapshot_invalid"})
    numbers = Counter(_number(neg.get("attempt"), 1) for neg, _ in sources)
    for raw_neg, is_archived in sources:
        neg = _dict(raw_neg)
        number = _number(neg.get("attempt"), 1)
        attributed = number is not None and numbers[number] == 1
        activity = [e for e in events if attributed and e["attempt"] == number]
        ag = _dict(neg.get("agreement"))
        status = _enum(ag.get("status"), ("none", "proposed", "confirmed"), "unknown")
        valid_turns = sum(e["completed_learner_turn"] for e in activity) if attributed else None
        unknown_turns = sum(e["unclassified_legacy_turn"] for e in activity) if attributed else None
        saved_count = _number(neg.get("turn_count"))
        lines = [_dict(t) for t in _list(neg.get("turns"))]
        lines_valid = isinstance(neg.get("turns"), list) and all(
            _enum(t.get("speaker"), ("player", "npc")) is not None and isinstance(t.get("text"), str) for t in lines)
        players = sum(t.get("speaker") == "player" for t in lines) if lines_valid else None
        methods = sorted({e["confirmation_method"] for e in activity if e["confirmation_method"]})
        method = methods[0] if len(methods) == 1 else "mixed" if methods else "unknown" if status in ("confirmed", "unknown") else "none"
        hints = [e for e in activity if e["kind"] == "hint_opened"]
        known_hints = attributed and all(e["hint_level"] is not None for e in hints)
        activity_observed = any(_enum(t.get("speaker"), ("player", "npc")) and isinstance(t.get("text"), str) for t in lines) or any(
            e["kind"] in ("opening", "opening_failed", "turn", "turn_failed", "confirm_button") for e in activity)
        row = {
            "attempt": number, "archived": is_archived,
            "activity_observed": True if activity_observed else False if attributed and lines_valid else None,
            "agreement_status": status,
            "ending": _dict(ag.get("confirmed")).get("submission_plan") if status == "confirmed" and _dict(ag.get("confirmed")).get("submission_plan") in ("full", "staged") else None,
            "paused": _boolean(neg.get("paused")), "confirmation_method": method,
            "completed_learner_turns": valid_turns, "unclassified_legacy_turns": unknown_turns,
            "state_turn_count": saved_count, "recorded_player_lines": players,
            "technical_failures": sum(e["technical_failure"] for e in activity) if attributed else None,
            "speech_transcriptions": sum(e["kind"] == "speech_transcribed" for e in activity) if attributed else None,
            "speech_failures": sum(e["kind"] == "speech_failed" for e in activity) if attributed else None,
            "hint_requests": len(hints) if attributed else None,
            "highest_hint_level": max((e["hint_level"] for e in hints), default=0) if known_hints else None,
            "distinct_hint_levels": len({e["hint_level"] for e in hints if e["hint_level"] > 0}) if known_hints else None,
            "model_modes": sorted({e["model_mode"] for e in activity if e["model_mode"] != "not_applicable"}),
        }
        if saved_count != valid_turns or players != valid_turns:
            warnings.append({"code": "turn_counts_need_review", "attempt": number})
        if number is None:
            warnings.append({"code": "attempt_number_missing", "attempt": None})
        elif not attributed:
            warnings.append({"code": "attempt_number_duplicate", "attempt": number})
        if not lines_valid or row["paused"] is None:
            warnings.append({"code": "attempt_state_fields_unknown", "attempt": number})
        if hints and not known_hints:
            warnings.append({"code": "hint_level_unknown", "attempt": number})
        if status == "confirmed" and method == "unknown":
            warnings.append({"code": "confirmation_method_unknown", "attempt": number})
        attempts.append(row)
        if include_text:
            conversations.append({"attempt": number, "archived": is_archived,
                                  "turns": [{"speaker": t["speaker"], "text": t["text"]}
                                            for t in lines if t.get("speaker") in ("player", "npc") and isinstance(t.get("text"), str)]})
    for event in events:
        if event["unclassified_legacy_turn"]:
            warnings.append({"code": "legacy_turn_counting_unknown", "sequence": event["sequence"]})
        if event["model_mode"] == "unknown":
            warnings.append({"code": "model_mode_unknown", "sequence": event["sequence"]})
        if event["elapsed_seconds"] is None:
            warnings.append({"code": "event_time_unknown", "sequence": event["sequence"]})
        if event["phase"] in ("negotiate", "outcome") and event["attempt"] not in {r["attempt"] for r in attempts if r["attempt"] is not None}:
            warnings.append({"code": "event_attempt_unmatched", "sequence": event["sequence"]})
    prep = _dict(state.get("prep"))
    preparation = {"completed": _boolean(prep.get("done")), "summary_confirmed": _boolean(prep.get("summary_confirmed"))}
    if preparation["completed"] is None or preparation["summary_confirmed"] is None:
        warnings.append({"code": "preparation_status_unknown"})
    for field in ("draft", "email_final", "prediction", "backup_plan"):
        value = prep.get(field)
        preparation[field + "_characters"] = len(value) if isinstance(value, str) else None
        if include_text:
            preparation[field] = value if isinstance(value, str) else None
    preparation["draft_versions"], preparation["send_decisions"] = _draft_versions(prep, include_text)
    # Exploration: content ids only (which places, clues and expressions were met). The nickname is never exported.
    chapter_ids = _exploration_ids(state.get("chapter_id"))
    exploration = {
        "places_visited": [a for a in _list(state.get("places_visited")) if a in chapter_ids["areas"]],
        "clues_found": [c for c in _list(state.get("notes")) if c in chapter_ids["clues"]],
        "clues_available": len(chapter_ids["clues"]),
        "expressions_met_in_story": [e for e in _list(state.get("expressions_seen")) if e in chapter_ids["expressions"]],
        "nickname_chosen": isinstance(state.get("player_name"), str),
    }
    mode_counts = Counter(e["model_mode"] for e in events if e["model_mode"] != "not_applicable")
    result = {
        "schema_version": SCHEMA_VERSION,
        "record_id": "R-" + hashlib.sha256(sid.encode("utf-8")).hexdigest()[:16].upper(),
        "generated_at": db.now_iso(),
        "chapter": {"id": _meta(state.get("chapter_id")), "version": _meta(state.get("chapter_version"))},
        "includes_text": include_text,
        "summary": {
            "current_phase": _enum(state.get("phase"), PHASES, "unknown"),
            "preparation_completed": _boolean(prep.get("done")),
            "attempts_recorded": len(attempts), "attempts_with_activity": sum(a["activity_observed"] is True for a in attempts),
            "attempts_with_confirmed_agreement": sum(a["agreement_status"] == "confirmed" for a in attempts),
            "completed_learner_turns": sum(e["completed_learner_turn"] for e in events),
            "unclassified_legacy_turns": sum(e["unclassified_legacy_turn"] for e in events),
            "technical_failures": sum(e["technical_failure"] for e in events),
            "speech_transcriptions": sum(e["kind"] == "speech_transcribed" for e in events),
            "speech_failures": sum(e["kind"] == "speech_failed" for e in events),
            "technical_failures_by_phase": dict(sorted(Counter(e["phase"] for e in events if e["technical_failure"]).items())),
            "model_operations_by_mode": dict(sorted(mode_counts.items())),
            "observed_span_seconds": max((e["elapsed_seconds"] for e in events if e["elapsed_seconds"] is not None), default=None),
            "xp": _number(snapshot.get("xp")),
        },
        "exploration": exploration, "preparation": preparation, "attempts": attempts, "events": events, "data_quality_warnings": warnings,
        "interpretation": [
            "These are descriptive records, not language scores or evidence of learning gains.",
            "expressions_met_in_story means the text containing an expression was opened; it is exposure, not use or mastery.",
            "XP, replay and agreement completion do not establish learning or independent transfer.",
            "Live means the provider transport branch was selected; failures are separate and no successful API call is implied.",
            "Do not pool mock, live, custom and unknown model operations as one AI condition.",
            "Elapsed time includes idle time; input mode is client-reported, not verified speech use.",
            "Local speech events count recorded recognition results, not learner turns, speech proficiency or accepted replies.",
            "Speech failures are separate from dialogue technical_failures; rejected HTTP uploads may have no speech event.",
            "Missing legacy evidence remains unknown. Inspect warnings before analysis.",
            "Draft versions show feedback given with the feedback in view: supported performance, not independent ability.",
            "Feedback points, review flags and recheck statuses are automated output, not validated judgements; "
            "counts say nothing about errors the feedback missed.",
            "Record IDs are pseudonyms. Text-inclusive records may identify people; review before sharing.",
        ],
    }
    if include_text:
        result["conversations"] = conversations
    return result


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
        return "'" + value  # Quoting a CSV cell alone does not stop spreadsheet formula evaluation.
    return value


def _csv(rows: list[dict], fields: list[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows({key: _csv_value(row.get(key)) for key in fields} for row in rows)
    return buffer.getvalue().encode("utf-8-sig")


def make_archive(sid: str, *, include_text: bool = False) -> tuple[bytes, str]:
    record = build_record(sid, include_text=include_text)
    record_id = record["record_id"]
    event_rows = [{"record_id": record_id, **e} for e in record["events"]]
    attempt_rows = [{"record_id": record_id, **a} for a in record["attempts"]]
    readme = """Campus to Career Quest — play-through record (ccq-record@1)

Files: record.json is the complete structured record; events.csv has one row per logged event;
attempts.csv has one row per saved attempt (including an initialized but not yet started attempt).
All files describe one consistent read snapshot. Downloading does not run a model or change progress.

Privacy: record_id is a stable hash-derived pseudonym for this session, not its usable session ID.
Default records exclude learner/NPC text. Text is included only when includes_text=true.
Raw provider outputs/errors, dropped facts, private rules, ASR text, audio paths, request IDs,
database event IDs and raw event timestamps are never exported. Text-inclusive files can contain
personal information. This export is not a guarantee of anonymity or research consent.

Definitions:
- completed_learner_turns: negotiate/turn events with counted exactly true, excluding known failures.
- unclassified_legacy_turns: turn events without an explicit true count flag; not assumed valid.
- technical_failures: failed opening/turn events, model_failed decisions, invalid opening source,
  or a recorded legacy fallback_opening (whose underlying cause may be unknown).
  These are service/response problems, not learner mistakes. Model attempts can include retries;
  model_error_count includes failed internal tries even if the final operation succeeded.
- agreement_status and ending: recorded game state, not a new evaluation using today's rules.
- confirmation_method: text/button only from a successful rule confirmation event; unknown otherwise.
- preparation.draft_versions: one row per checked/rechecked/sent email version. Feedback counts are
  automated points that passed rule checks (quote present in that version, reason and suggestion given).
  rule_and_review_flags mark doubt (possible change of date/number/negation, second-pass disagreement,
  not reviewed). recheck_by_status: resolved / still_present / kept_by_user / cannot_judge, with the basis
  (unchanged sentence by rule, learner's choice, or an automated recheck). None of these is a human rating.
- preparation.send_decisions: what was open when the learner chose to send. Sending is never blocked by
  language feedback; open_points is a description, not a fault.
- reported_input_mode: client-provided typed/voice/voice_edited; not evidence of actual speech use.
- speech_transcriptions/speech_failures: recorded ASR outcomes, separate from dialogue technical_failures.
  They do not consume learner turns. A transcription may be discarded, edited or never sent. Invalid
  HTTP uploads and stale-session requests can be rejected before logging, so these are not request totals.
  speech_mode=local, speech_backend, duration, latency, transcript character count and an allowlisted
  error code describe recognition only; ASR is excluded from dialogue model_operations_by_mode.
  Silence/format/recognition failures do not measure language ability. No audio or raw ASR text is exported.
- attempts_with_activity: an opening/turn/failure/confirmation or recorded conversation exists.
- hint_requests: event count, including repeated clicks; distinct_hint_levels/highest_hint_level
  are separate. outcome_shown may repeat and is never interpreted as extra completion.
- observed_span_seconds/elapsed_seconds: from creation to recorded events, including idle time.
  They are not active-task duration. Missing/invalid times become null (empty cells in CSV).
- model_mode: mock/live/custom/unknown. New records log the selected transport branch. An older
  literal mock model label is labelled legacy_model_label; older non-mock labels remain unknown.
  Live does not mean a request succeeded. Keep these conditions separate in analysis.
- state_turn_count and recorded_player_lines are retained for reconciliation, not used to silently
  replace missing event evidence. data_quality_warnings marks mismatches and unknown legacy fields.
- missing or invalid saved flags remain null. Missing/duplicate attempt numbers leave that snapshot's
  event-derived counts null. A missing retry boundary or conflicting explicit attempt stops sequence
  attribution until a new valid boundary; events are not silently assigned to the previous attempt.

The two CSV files join on record_id and attempt (when known); events.sequence is local to this export.
CSV files use UTF-8 with a BOM. Formula-like string cells are prefixed with an apostrophe for safe
spreadsheet viewing; JSON preserves the exact included text. Null is an empty CSV cell, not zero.
XP, completion and replay measure game behaviour, not learning gains or independent transfer.
Only a separate, approved evaluation design can support such claims.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("record.json", json.dumps(record, ensure_ascii=False, indent=2))
        archive.writestr("events.csv", _csv(event_rows, list(event_rows[0]) if event_rows else ["record_id", "sequence"]))
        archive.writestr("attempts.csv", _csv(attempt_rows, list(attempt_rows[0]) if attempt_rows else ["record_id", "attempt"]))
        archive.writestr("README.txt", readme)
    return buffer.getvalue(), f"ccq-record-{record_id}.zip"
