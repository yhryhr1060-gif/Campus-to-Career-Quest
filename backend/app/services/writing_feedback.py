"""Writing-feedback loop for the Prepare step.

explore -> write -> check a draft VERSION -> learner revises -> recheck earlier points -> learner
decides to send. Three ideas shape this module:

1. Feedback belongs to the exact draft version it was requested for. A slow model result is stored
   on that version only; it can never replace or relabel a newer draft.
2. The model proposes; code decides what may be shown. Quotes must be in that version, every point
   needs a suggestion, a reason and a type, and suggestions that touch dates, numbers or negation
   are marked uncertain by rule. A second model pass may add doubt; it never certifies correctness.
3. The learner decides. Open points produce a reminder, never a gate. Only real limits (empty text,
   length) stop a send, and those are reported separately from language advice.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .. import db
from ..content import get_chapter
from ..limits import MAX_EMAIL_CHARS, validate_length
from ..llm import mock
from ..llm.client import call_json
from ..llm.prompt_templates import VERSIONS, render
from ..llm.schemas import DraftFeedbackOut, FeedbackReviewOut, RecheckOut
from ..rules.feedback_guard import is_large_rewrite, meaning_flags
from ..rules.quotes import normalize, quote_in, sentence_containing
from ..session_serialization import serialized_session
from .session import Conflict, NotFound, load, public_state, chapter_for

MAX_VERSIONS = 30        # stored draft versions per session; reaching it never blocks sending
MAX_ITEMS = 12           # points kept per version; the interface reveals them layer by layer
MAX_REQUEST_IDS = 128    # remembered attempts per operation and version; bounds saved metadata
STYLE_TYPES = ("politeness", "naturalness")
MECHANICS_TYPES = ("spelling", "punctuation", "capitalization")
RETRYABLE_BASES = {"ai_unavailable", "ai_no_answer", "evidence_not_in_draft", "evidence_missing", "context_recheck_needed"}
DISCLAIMER = ("AI feedback can be wrong and cannot find every error. "
              "It is advice: you decide what to change and when to send.")


def ensure_prep(prep: dict) -> dict:
    """Older saved sessions have no version list; add the fields without touching anything else."""
    prep.setdefault("versions", [])
    prep.setdefault("send_decisions", [])
    return prep


def _require_editing(state: dict) -> None:
    if state["phase"] not in ("explore", "prepare") or state["prep"]["email_final"]:
        raise Conflict("the email has already been sent")


def _update(sid: str, change) -> dict:
    state = db.mutate_state(sid, change)
    if state is None:
        raise NotFound(sid)
    return state


def _find(prep: dict, number: int) -> dict | None:
    return next((v for v in ensure_prep(prep)["versions"] if v.get("version") == number), None)


def version_for(prep: dict, text: str, origin: str) -> dict:
    """Return the version holding exactly this text, creating it only when the text is new."""
    versions = ensure_prep(prep)["versions"]
    if versions and versions[-1]["text"] == text:
        return versions[-1]
    if len(versions) >= MAX_VERSIONS:
        raise Conflict(f"This email already has {MAX_VERSIONS} saved versions, the most this game keeps. "
                       "No further checks can be stored, but you can still send your email.", code="version_limit")
    version = {"version": len(versions) + 1, "text": text, "created_at": db.now_iso(), "origin": origin,
               "feedback": None, "recheck": None, "decisions": {}}
    versions.append(version)
    return version


def base_version(prep: dict) -> dict | None:
    """The latest version that has a completed whole-draft check."""
    for version in reversed(ensure_prep(prep)["versions"]):
        if (version.get("feedback") or {}).get("status") == "ok":
            return version
    return None


def _issues(version: dict) -> list[dict]:
    return [i for i in version["feedback"]["items"] if i["type"] != "positive"]


def _request_version(prep: dict, operation: str, request_id: str | None, text: str) -> dict | None:
    if not request_id:
        return None
    for version in ensure_prep(prep)["versions"]:
        if request_id in version.get("request_ids", {}).get(operation, []):
            if version["text"] != text:
                raise Conflict("This request id belongs to different draft text.", code="request_id_conflict")
            return version
    return None


def _remember_request(version: dict, operation: str, request_id: str | None) -> None:
    if request_id:
        ids = version.setdefault("request_ids", {}).setdefault(operation, [])
        if request_id not in ids:
            ids.append(request_id)
            del ids[:-MAX_REQUEST_IDS]


def _review_retryable(feedback: dict) -> bool:
    return feedback.get("status") == "ok" and any(
        i.get("type") != "positive" and not i.get("reviewed", False) for i in feedback.get("items", []))


# ---------------------------------------------------------------- whole-draft check

def _layer(item: dict) -> int:
    if item["type"] == "task_info" or (item["type"] == "grammar" and item["judgement"] == "clear_error" and item["serious"]):
        return 1          # serious grammar first; missing task information is raised early
    if item["type"] in ("grammar", "accuracy", "logic", *MECHANICS_TYPES):
        return 2
    return 3              # politeness, naturalness and praise are optional reading


def screen_items(raw_items: list[dict], text: str, context: str) -> tuple[list[dict], list[dict]]:
    """Apply the rule checks to model output for one version. Returns (kept, dropped)."""
    kept, dropped, seen, corrections = [], [], set(), set()
    norm_text = normalize(text)
    for raw in raw_items:
        item = {**raw, "quote": raw["quote"].strip(), "suggestion": raw.get("suggestion", "").strip(),
                "reason": raw.get("reason", "").strip(), "flags": []}
        key = (item["type"], normalize(item["quote"]))

        def drop(why: str) -> None:
            dropped.append({**raw, "drop_reason": why})

        if not quote_in(item["quote"], text):
            drop("quote_not_in_version"); continue
        if item["type"] in MECHANICS_TYPES and item["quote"] not in text:
            drop("quote_not_in_version"); continue
        if not item["reason"]:
            drop("reason_missing"); continue
        if key in seen:
            drop("duplicate"); continue
        correction = (normalize(item["quote"]), item["suggestion"])
        if item["type"] != "positive" and correction in corrections:
            drop("duplicate"); continue
        if item["type"] == "positive":
            item.update(judgement="optional", serious=False, suggestion="")
        else:
            if not item["suggestion"]:
                drop("suggestion_missing"); continue
            # Capitalization and punctuation are meaningful edits, not normalized-away changes.
            same = (item["suggestion"] == item["quote"] if item["type"] in MECHANICS_TYPES
                    else normalize(item["suggestion"]) == normalize(item["quote"]))
            if same:
                drop("suggestion_unchanged"); continue
            if item["type"] in STYLE_TYPES and item["judgement"] in ("clear_error", "expression_issue"):
                item["judgement"] = "optional"       # style is never reported as an error
                item["serious"] = False
                item["flags"].append("style_downgraded")
            if item["type"] == "task_info" and item["judgement"] == "clear_error":
                item["judgement"] = "expression_issue"   # missing information is not a language error
            flags = meaning_flags(item["quote"], item["suggestion"], allowed_context=context)
            if flags:
                item["flags"] += flags
                item["judgement"] = "uncertain"
            if item["type"] in ("grammar", "accuracy") and is_large_rewrite(item["quote"], item["suggestion"]):
                item["flags"].append("large_rewrite")
        seen.add(key)
        if item["type"] != "positive":
            corrections.add(correction)
        item["layer"] = _layer(item)
        item["position"] = norm_text.find(normalize(item["quote"]))
        kept.append(item)
    kept.sort(key=lambda i: (i["layer"], i["position"]))
    for extra in kept[MAX_ITEMS:]:
        dropped.append({k: extra[k] for k in ("type", "judgement", "quote", "suggestion", "reason")} | {"drop_reason": "over_limit"})
    kept = kept[:MAX_ITEMS]
    return kept, dropped


def apply_review(items: list[dict], reviews: list[dict] | None) -> str:
    """Merge the second pass. It can only add doubt: agreement is recorded, never shown as proof."""
    issues = [i for i in items if i["type"] != "positive"]
    if not issues:
        return "skipped"
    if reviews is None:
        for item in issues:
            if not item.get("reviewed"):
                item["reviewed"] = False
                if "not_reviewed" not in item["flags"]:
                    item["flags"].append("not_reviewed")
        return "partial" if any(i.get("reviewed") for i in issues) else "unavailable"
    by_id, duplicate_ids = {}, set()
    for review in reviews:
        if review["id"] in by_id:
            duplicate_ids.add(review["id"])
        by_id[review["id"]] = review
    for item in issues:
        if item.get("reviewed"):
            continue                  # a retry only fills unfinished work
        item["flags"] = [flag for flag in item["flags"] if flag != "not_reviewed"]
        review = by_id.get(item["id"])
        if review is None or item["id"] in duplicate_ids:
            item["reviewed"] = False
            item["flags"].append("not_reviewed")
            continue
        item["reviewed"] = True
        if review["verdict"] != "agree":
            item["judgement"] = "uncertain"
            item["flags"].append("reviewer_disagrees" if review["verdict"] == "disagree" else "reviewer_unsure")
            if review.get("note", "").strip():
                item["review_note"] = review["note"].strip()
    unfinished = sum(not i.get("reviewed", False) for i in issues)
    return "ok" if not unfinished else "unavailable" if unfinished == len(issues) else "partial"


def _context(chapter, text: str, notes: list[str]) -> str:
    """Facts a suggestion may reuse without inventing anything: the draft itself, the task card and
    the clues this learner has found. Facts behind places they have not explored are not included."""
    return " ".join([text, *chapter.known_facts(notes)])


def _check_response(sid: str, state: dict, version: dict, reused: bool) -> dict:
    feedback = version.get("feedback") or {}
    available = feedback.get("status") == "ok"
    return {**public_state(sid, state), "available": available, "version": version["version"],
            "is_current": state["prep"]["draft"] == version["text"], "reused": reused,
            "items": feedback.get("items", []) if available else [],
            "review_status": feedback.get("review_status"), "review_retryable": _review_retryable(feedback),
            "disclaimer": DISCLAIMER}


@serialized_session
def check_draft(sid: str, draft: str, request_id: str | None = None) -> dict:
    """Whole-draft check for one version. Repeating the request for unchanged text returns the stored
    result; a failed check is retried on the same version instead of creating another."""
    state = load(sid)
    _require_editing(state)
    validate_length(draft, MAX_EMAIL_CHARS, "Email")
    if not draft.strip():
        raise Conflict("the draft is empty", code="draft_empty")
    previous = _request_version(state["prep"], "feedback", request_id, draft)
    if previous is not None:
        return _check_response(sid, state, previous, reused=True)
    number = None

    def begin(current):
        nonlocal number
        _require_editing(current)
        # Save the requested text first, so a later autosave is never overwritten by this request.
        current["prep"]["draft"] = draft
        version = version_for(current["prep"], draft, "check")
        number = version["version"]
        if (version.get("feedback") or {}).get("status") != "ok":
            version["feedback"] = {"status": "pending"}
    state = _update(sid, begin)
    version = _find(state["prep"], number)
    if version["feedback"]["status"] == "ok" and not _review_retryable(version["feedback"]):
        if request_id:
            state = _update(sid, lambda current: _remember_request(_find(current["prep"], number), "feedback", request_id))
            version = _find(state["prep"], number)
        return _check_response(sid, state, version, reused=True)

    chapter = chapter_for(state)
    first_pass_needed = version["feedback"]["status"] != "ok"
    res, review, dropped = None, None, []
    feedback = deepcopy(version["feedback"])
    if first_pass_needed:
        prompt = render("prep_feedback", chapter=chapter, npc=chapter.npc, draft=draft,
                        known_facts=chapter.known_facts(state["notes"]))
        res = call_json(DraftFeedbackOut, [{"role": "user", "content": prompt}],
                        prompt_version=VERSIONS["prep_feedback"], mock=lambda: mock.mock_feedback(draft))
    if res is not None and res.ok:
        shown, dropped = screen_items([i.model_dump() for i in res.data.items], draft, _context(chapter, draft, state["notes"]))
        for index, item in enumerate(shown, 1):
            item["id"] = f"v{number}-{index}"
        over_limit = sum(i["drop_reason"] == "over_limit" for i in dropped)
        invalid = sum(i["drop_reason"] not in ("over_limit", "duplicate") for i in dropped)
        feedback = {"status": "ok", "prompt_version": VERSIONS["prep_feedback"], "items": shown,
                    "review_status": "unavailable" if any(i["type"] != "positive" for i in shown) else "skipped",
                    "dropped_count": len(dropped), "over_limit_count": over_limit, "invalid_count": invalid,
                    "item_limit": MAX_ITEMS, "possibly_incomplete": len(shown) >= MAX_ITEMS or bool(over_limit or invalid),
                    "checked_at": db.now_iso()}
        # Persist the first pass before the second call. A restart can resume just the review.
        def save_first_pass(current):
            _require_editing(current)
            target = _find(current["prep"], number)
            if target is None or target["text"] != draft:
                raise Conflict("this draft version is no longer available")
            target["feedback"] = deepcopy(feedback)
            current["prep"]["feedback_requests"] += 1
        _update(sid, save_first_pass)
        db.log_event(sid, "prepare", "feedback", player_text=draft, model=res.model,
                     prompt_version=res.prompt_version, raw_output=res.raw_for_log(), rule_decision="ok",
                     payload={"version": number, "shown": shown, "dropped": dropped})
    elif res is not None:
        feedback = {"status": "unavailable", "checked_at": db.now_iso()}

    shown = feedback.get("items", [])
    to_review = [i for i in shown if i["type"] != "positive" and not i.get("reviewed")]
    if feedback["status"] == "ok" and to_review:
        review_prompt = render("prep_feedback_review", npc=chapter.npc, draft=draft, items=to_review)
        review = call_json(FeedbackReviewOut, [{"role": "user", "content": review_prompt}],
                           prompt_version=VERSIONS["prep_feedback_review"], max_attempts=1,
                           mock=lambda: mock.mock_feedback_review(to_review))
        feedback["review_status"] = apply_review(shown, [r.model_dump() for r in review.data.reviews] if review.ok else None)
        feedback["review_checked_at"] = db.now_iso()
    feedback["review_retryable"] = _review_retryable(feedback)

    def finish(current):
        _require_editing(current)
        target = _find(current["prep"], number)
        if target is None or target["text"] != draft:
            raise Conflict("this draft version is no longer available")
        target["feedback"] = feedback
        _remember_request(target, "feedback", request_id)
        if first_pass_needed and not res.ok:
            current["prep"]["feedback_requests"] += 1
    state = _update(sid, finish)
    if res is not None and not res.ok:
        db.log_event(sid, "prepare", "feedback", player_text=draft, model=res.model,
                     prompt_version=res.prompt_version, raw_output=res.raw_for_log(),
                     rule_decision="ok" if res.ok else "model_failed",
                     payload={"version": number, "shown": shown, "dropped": dropped})
    if review is not None:
        db.log_event(sid, "prepare", "feedback_review", model=review.model, prompt_version=review.prompt_version,
                     raw_output=review.raw_for_log(), rule_decision=("ok" if not feedback["review_retryable"] else
                                                                   "incomplete" if review.ok else "model_failed"),
                     payload={"version": number, "review_status": feedback["review_status"]})
    return _check_response(sid, state, _find(state["prep"], number), reused=False)


# ---------------------------------------------------------------- learner decisions on single points

def set_decision(sid: str, version_number: int, item_id: str, decision: str) -> dict:
    """Record that the learner keeps their own wording for one point (or withdraw that choice)."""
    if decision not in ("keep", "clear"):
        raise Conflict("unknown decision")

    changed = False

    def change(current):
        nonlocal changed
        _require_editing(current)
        version = _find(current["prep"], version_number)
        if version is None or (version.get("feedback") or {}).get("status") != "ok":
            raise NotFound(f"version {version_number}")
        if not any(i["id"] == item_id for i in _issues(version)):
            raise NotFound(item_id)
        if decision == "keep":
            changed = version["decisions"].get(item_id) != "keep"
            version["decisions"][item_id] = "keep"
        else:
            changed = item_id in version["decisions"]
            version["decisions"].pop(item_id, None)
    state = _update(sid, change)
    if changed:
        db.log_event(sid, "prepare", "feedback_decision",
                     payload={"version": version_number, "item": item_id, "decision": decision})
    return public_state(sid, state)


# ---------------------------------------------------------------- recheck of earlier points

def _sentence_unchanged(item: dict, old_text: str, new_text: str) -> bool:
    return sentence_containing(item["quote"], old_text).strip() in new_text


def _local_rule_applies(item: dict) -> bool:
    """Only a tiny auditable agreement pattern can bypass contextual model assessment.

    A type label such as 'grammar' is not proof that an issue is context independent. Mechanics
    also goes to the model: changing a word's role can make its original capitalization valid.
    """
    return (item["type"] == "grammar" and item["judgement"] == "clear_error"
            and not item.get("flags")
            and bool(re.fullmatch(r"I (?:has|is)(?: been)?", item["quote"].strip())))


def _row_retryable(row: dict) -> bool:
    return row.get("basis") in RETRYABLE_BASES


def _recheck_status(results: list[dict]) -> str:
    failed = sum(_row_retryable(r) for r in results)
    return "ok" if not failed else "unavailable" if failed == len(results) else "partial"


def _upgrade_recheck(recheck: dict | None, base: dict) -> dict | None:
    """Do not trust old cached sentence-only decisions for context-dependent points."""
    if not recheck or recheck.get("base_version") != base["version"]:
        return recheck
    result = deepcopy(recheck)
    items = {i["id"]: i for i in _issues(base)}
    for row in result.get("results", []):
        item = items.get(row.get("id"))
        undo_keep = (item and row.get("basis") == "learner_kept"
                     and base.get("decisions", {}).get(row["id"]) != "keep")
        if undo_keep and _local_rule_applies(item):
            row.update(status="still_present", basis="sentence_unchanged")
        elif item and (undo_keep or row.get("basis") == "sentence_unchanged") and not _local_rule_applies(item):
            row.update(status="cannot_judge", basis="context_recheck_needed", current_quote="",
                       note="This earlier sentence-only check needs the revised context.")
        row["retryable"] = _row_retryable(row)
    result["status"] = _recheck_status(result.get("results", []))
    result["retryable"] = any(_row_retryable(r) for r in result.get("results", []))
    return result


def _recheck_complete(recheck: dict | None, base: dict) -> bool:
    return bool(recheck) and recheck.get("base_version") == base["version"] and \
        {r["id"] for r in recheck.get("results", [])} == {i["id"] for i in _issues(base)} and \
        not any(_row_retryable(r) for r in recheck.get("results", []))


@serialized_session
def recheck(sid: str, draft: str, request_id: str | None = None) -> dict:
    """Compare a revised draft with the points raised on the last checked version.

    Statuses: resolved, still_present, kept_by_user, cannot_judge. Whether the learner copied the
    suggested wording is never used as evidence. Context-dependent issues always see the whole
    revised draft, even when the originally quoted words have not changed."""
    state = load(sid)
    _require_editing(state)
    validate_length(draft, MAX_EMAIL_CHARS, "Email")
    if not draft.strip():
        raise Conflict("the draft is empty", code="draft_empty")
    previous = _request_version(state["prep"], "recheck", request_id, draft)
    if previous is not None:
        return _recheck_response(sid, state, previous, reused=True)
    base = base_version(state["prep"])
    if base is None:
        raise Conflict("there is no checked version to compare with yet", code="no_feedback_to_recheck")
    base_number, number = base["version"], None

    def begin(current):
        nonlocal number
        _require_editing(current)
        current["prep"]["draft"] = draft
        number = version_for(current["prep"], draft, "recheck")["version"]
    state = _update(sid, begin)
    base, target = _find(state["prep"], base_number), _find(state["prep"], number)
    target["recheck"] = _upgrade_recheck(target.get("recheck"), base)
    if _recheck_complete(target.get("recheck"), base):
        if request_id:
            state = _update(sid, lambda current: _remember_request(_find(current["prep"], number), "recheck", request_id))
            target = _find(state["prep"], number)
        return _recheck_response(sid, state, target, reused=True)

    decisions = base["decisions"]
    existing = target.get("recheck") or {}
    results = ({row["id"]: deepcopy(row) for row in existing.get("results", []) if not _row_retryable(row)}
               if existing.get("base_version") == base_number else {})
    ask = []
    for item in _issues(base):
        if item["id"] in results:
            continue                       # successful and genuinely uncertain rows survive retries
        unchanged = _sentence_unchanged(item, base["text"], draft)
        kept = decisions.get(item["id"]) == "keep" and unchanged
        if kept or (unchanged and _local_rule_applies(item)):
            results[item["id"]] = {"id": item["id"], "status": "kept_by_user" if kept else "still_present",
                                   "basis": "learner_kept" if kept else "sentence_unchanged",
                                   "current_quote": item["quote"], "note": "", "retryable": False}
        else:
            ask.append(item)

    res = None
    if ask:
        prompt = render("prep_recheck", old_text=base["text"], new_text=draft, items=ask)
        res = call_json(RecheckOut, [{"role": "user", "content": prompt}], prompt_version=VERSIONS["prep_recheck"],
                        mock=lambda: mock.mock_recheck(base["text"], draft, ask))
        verdicts, duplicate_ids = {}, set()
        if res.ok:
            for verdict in res.data.results:
                if verdict.id in verdicts:
                    duplicate_ids.add(verdict.id)
                verdicts[verdict.id] = verdict
        for item in ask:
            verdict = verdicts.get(item["id"])
            row = {"id": item["id"], "status": "cannot_judge", "current_quote": "", "note": ""}
            if not res.ok:
                row["basis"] = "ai_unavailable"
            elif verdict is None or item["id"] in duplicate_ids:
                row["basis"] = "ai_no_answer"
            else:
                quote = verdict.current_quote.strip()
                row["note"] = verdict.note.strip()
                valid_quote = quote in draft if item["type"] in MECHANICS_TYPES else quote_in(quote, draft)
                if quote and not valid_quote:
                    row["basis"] = "evidence_not_in_draft"     # the model cited words the learner did not write
                elif verdict.status == "unclear":
                    row["basis"] = "ai_unsure"
                    row["current_quote"] = quote
                elif not quote and (verdict.status == "still_present" or quote_in(item["quote"], draft)):
                    row["basis"] = "evidence_missing"
                else:
                    row.update(status=verdict.status, basis="ai_recheck", current_quote=quote)
            row["retryable"] = _row_retryable(row)
            results[item["id"]] = row

    ordered = [results[i["id"]] for i in _issues(base)]

    def finish(current):
        _require_editing(current)
        current_target = _find(current["prep"], number)
        if current_target is None or current_target["text"] != draft:
            raise Conflict("this draft version is no longer available")
        current_target["recheck"] = {"base_version": base_number, "results": ordered, "checked_at": db.now_iso(),
                                     "used_model": bool(ask), "status": _recheck_status(ordered),
                                     "retryable": any(_row_retryable(r) for r in ordered)}
        _remember_request(current_target, "recheck", request_id)
    state = _update(sid, finish)
    db.log_event(sid, "prepare", "recheck", player_text=draft,
                 model=res.model if res else None, prompt_version=res.prompt_version if res else None,
                 raw_output=res.raw_for_log() if res else None,
                 rule_decision=("ok" if not any(_row_retryable(r) for r in ordered) else
                                "incomplete" if res.ok else "model_failed") if res else "no_model_needed",
                 payload={"version": number, "base_version": base_number, "results": ordered})
    return _recheck_response(sid, state, _find(state["prep"], number), reused=False)


def _recheck_response(sid: str, state: dict, version: dict, reused: bool) -> dict:
    base = _find(state["prep"], version["recheck"]["base_version"])
    version = {**version, "recheck": _upgrade_recheck(version["recheck"], base)} if base else version
    return {**public_state(sid, state), "version": version["version"],
            "base_version": version["recheck"]["base_version"], "results": version["recheck"]["results"],
            "available": not any(_row_retryable(r) for r in version["recheck"]["results"]),
            "retryable": any(_row_retryable(r) for r in version["recheck"]["results"]),
            "status": _recheck_status(version["recheck"]["results"]),
            "is_current": state["prep"]["draft"] == version["text"], "reused": reused, "disclaimer": DISCLAIMER}


# ---------------------------------------------------------------- what is still open for a given text

def review_state(prep: dict, text: str) -> dict:
    """Describe, for the text about to be sent, which earlier points are open. Used for the reminder
    and for the send record. It never blocks anything."""
    base = base_version(prep)
    if base is None:
        return {"feedback_state": "none", "base_version": None, "open": [], "kept": [], "resolved": [], "uncertain": []}
    decisions = base["decisions"]
    issues = _issues(base)
    uncertain = [i["id"] for i in issues if i["judgement"] == "uncertain"]
    latest = prep["versions"][-1]
    statuses: dict[str, str] = {}
    if text == base["text"]:
        state = "checked_this_version"
        statuses = {i["id"]: "open" for i in issues}
    elif latest["text"] == text and (latest.get("recheck") or {}).get("base_version") == base["version"]:
        state = "rechecked_this_version"
        statuses = {r["id"]: r["status"] for r in _upgrade_recheck(latest["recheck"], base)["results"]}
    else:
        state = "changed_since_check"
        statuses = {i["id"]: "not_rechecked" for i in issues}
    for item_id, status in statuses.items():
        keeps = decisions.get(item_id) == "keep"       # the learner's latest choice wins over a stored label
        if keeps and status in ("open", "still_present"):
            statuses[item_id] = "kept_by_user"
        elif not keeps and status == "kept_by_user":
            statuses[item_id] = "still_present"
    return {"feedback_state": state, "base_version": base["version"],
            "open": [i for i, s in statuses.items() if s in ("open", "still_present", "cannot_judge", "not_rechecked")],
            "kept": [i for i, s in statuses.items() if s == "kept_by_user"],
            "resolved": [i for i, s in statuses.items() if s == "resolved"],
            "uncertain": uncertain}


def public_versions(prep: dict) -> list[dict]:
    """Versions as the interface needs them. A check that was interrupted (for example by closing
    the app) is reported as unavailable so it can simply be requested again."""
    out = []
    for version in ensure_prep(prep)["versions"]:
        feedback = deepcopy(version.get("feedback"))
        if feedback and feedback.get("status") == "pending":
            feedback = {"status": "unavailable"}
        elif feedback and feedback.get("status") == "ok":
            feedback["review_retryable"] = _review_retryable(feedback)
            if feedback["review_retryable"]:
                feedback["review_status"] = ("partial" if any(i.get("reviewed") for i in feedback.get("items", [])
                                                               if i["type"] != "positive") else "unavailable")
            feedback.setdefault("item_limit", MAX_ITEMS)
            feedback.setdefault("possibly_incomplete", len(feedback.get("items", [])) >= MAX_ITEMS or
                                bool(feedback.get("dropped_count")))
        recheck = version.get("recheck")
        base = _find(prep, recheck["base_version"]) if recheck else None
        if base and (base.get("feedback") or {}).get("status") == "ok":
            recheck = _upgrade_recheck(recheck, base)
        out.append({"version": version["version"], "text": version["text"], "created_at": version.get("created_at"),
                    "origin": version.get("origin"), "feedback": feedback, "recheck": recheck,
                    "decisions": version.get("decisions", {})})
    return out
