"""Round 6: finite mock outputs test retry/state contracts, not AI language quality."""
import threading
from collections import Counter
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.content import get_chapter
from app.llm import mock
from app.llm.prompt_templates import render
from app.main import app
from app.services import prepare, session, writing_feedback as wf

DRAFT = "Dear Prof. Chen, I has been sick for three days. Could I submit the report by Sunday? Thanks, Alex"
REVISED = DRAFT.replace("I has", "I have")


def issue(**kw):
    return {"type": "grammar", "judgement": "clear_error", "serious": True,
            "quote": "I has been", "suggestion": "I have been", "reason": "Use have after I."} | kw


def start(monkeypatch, items=None, draft=DRAFT):
    if items is None:
        items = [issue()]
    monkeypatch.setattr(mock, "mock_feedback", lambda text: {"items": deepcopy(items)})
    sid = session.create()[0]
    return sid, wf.check_draft(sid, draft)


def events(sid, kind):
    return [e for e in db.list_events(sid) if e["kind"] == kind]


def test_second_review_retry_preserves_first_pass_ids_and_decisions(monkeypatch):
    calls = Counter()
    original = wf.call_json

    def counted(schema, *args, **kwargs):
        calls[schema.__name__] += 1
        return original(schema, *args, **kwargs)

    monkeypatch.setattr(wf, "call_json", counted)
    working = mock.mock_feedback_review
    monkeypatch.setattr(mock, "mock_feedback_review", lambda items: (_ for _ in ()).throw(httpx.ReadTimeout("test")))
    sid, first = start(monkeypatch)
    assert first["available"] and first["review_retryable"]
    assert first["review_status"] == "unavailable"
    wf.set_decision(sid, 1, first["items"][0]["id"], "keep")
    monkeypatch.setattr(mock, "mock_feedback_review", working)
    retried = wf.check_draft(sid, DRAFT, request_id="explicit-review-retry")
    assert retried["review_status"] == "ok" and not retried["review_retryable"]
    assert calls == {"DraftFeedbackOut": 1, "FeedbackReviewOut": 2}
    old, new = first["items"][0], retried["items"][0]
    assert {k: old[k] for k in ("id", "type", "quote", "suggestion", "reason", "judgement")} == {
        k: new[k] for k in ("id", "type", "quote", "suggestion", "reason", "judgement")}
    assert new["flags"].count("not_reviewed") == 0
    stored = session.load(sid)["prep"]
    assert len(stored["versions"]) == 1 and stored["feedback_requests"] == 1
    assert stored["versions"][0]["decisions"] == {old["id"]: "keep"}
    assert len(events(sid, "feedback")) == 1 and len(events(sid, "feedback_review")) == 2


def test_interruption_after_first_pass_restores_first_pass_and_only_retries_review(monkeypatch):
    sid = session.create()[0]
    original = wf.call_json

    def interrupted(schema, *args, **kwargs):
        if schema.__name__ == "FeedbackReviewOut":
            raise KeyboardInterrupt("simulated process interruption")
        return original(schema, *args, **kwargs)

    monkeypatch.setattr(wf, "call_json", interrupted)
    with pytest.raises(KeyboardInterrupt):
        wf.check_draft(sid, DRAFT)
    restored = session.public_state(sid, session.load(sid))
    feedback = restored["prep"]["versions"][0]["feedback"]
    assert feedback["status"] == "ok" and feedback["items"] and feedback["review_retryable"]
    assert session.load(sid)["prep"]["feedback_requests"] == 1 and len(events(sid, "feedback")) == 1
    calls = []

    def retry(schema, *args, **kwargs):
        calls.append(schema.__name__)
        return original(schema, *args, **kwargs)

    monkeypatch.setattr(wf, "call_json", retry)
    assert not wf.check_draft(sid, DRAFT)["review_retryable"]
    assert calls == ["FeedbackReviewOut"] and len(events(sid, "feedback")) == 1


def test_review_missing_ids_retry_only_missing_and_preserve_valid_doubt(monkeypatch):
    requested = []

    def partial(items):
        requested.append([i["id"] for i in items])
        return {"reviews": [{"id": items[0]["id"], "verdict": "unsure", "note": "Context is ambiguous."}]}

    monkeypatch.setattr(mock, "mock_feedback_review", partial)
    sid, first = start(monkeypatch, [issue(), issue(type="accuracy", quote="sick for three days",
                                                 suggestion="unwell for three days", reason="Meaning is unclear.")])
    assert first["review_status"] == "partial" and first["review_retryable"]
    doubted = deepcopy(first["items"][0])
    wf.check_draft(sid, DRAFT)
    assert requested == [["v1-1", "v1-2"], ["v1-2"]]
    saved = session.load(sid)["prep"]["versions"][0]["feedback"]
    assert saved["items"][0] == doubted
    assert saved["review_status"] == "ok" and not saved["review_retryable"]
    assert all(i["judgement"] == "uncertain" for i in saved["items"])


@pytest.mark.parametrize("result,basis", [
    ({"results": []}, "ai_no_answer"),
    ({"results": [{"id": "v1-1", "status": "resolved", "current_quote": "invented text"}]}, "evidence_not_in_draft"),
    ({"results": [{"id": "v1-1", "status": "still_present", "current_quote": ""}]}, "evidence_missing"),
    ({"results": [{"id": "v1-1", "status": "resolved", "current_quote": "I have been"},
                  {"id": "v1-1", "status": "still_present", "current_quote": "I have been"}]}, "ai_no_answer"),
])
def test_invalid_recheck_is_retryable_on_same_version(monkeypatch, result, basis):
    sid, _ = start(monkeypatch)
    monkeypatch.setattr(mock, "mock_recheck", lambda *args: result)
    failed = wf.recheck(sid, REVISED)
    assert failed["status"] == "unavailable" and failed["retryable"] and not failed["available"]
    assert failed["results"][0]["basis"] == basis and failed["results"][0]["status"] == "cannot_judge"
    monkeypatch.setattr(mock, "mock_recheck", lambda *args: {"results": [
        {"id": "v1-1", "status": "resolved", "current_quote": "I have been sick", "note": "Agreement is correct."}]})
    retried = wf.recheck(sid, REVISED)
    assert not retried["reused"] and retried["status"] == "ok" and not retried["retryable"]
    assert retried["results"][0]["status"] == "resolved"
    assert len(session.load(sid)["prep"]["versions"]) == 2


def test_genuine_uncertainty_is_complete_but_not_resolved(monkeypatch):
    sid, _ = start(monkeypatch)
    monkeypatch.setattr(mock, "mock_recheck", lambda *args: {"results": [
        {"id": "v1-1", "status": "unclear", "current_quote": "I have been sick", "note": "The intended meaning is not clear."}]})
    first = wf.recheck(sid, REVISED)
    assert first["available"] and not first["retryable"] and first["status"] == "ok"
    assert first["results"][0]["status"] == "cannot_judge" and first["results"][0]["basis"] == "ai_unsure"
    monkeypatch.setattr(wf, "call_json", lambda *a, **k: pytest.fail("valid uncertainty is cached"))
    assert wf.recheck(sid, REVISED)["reused"]
    snapshot = wf.review_state(session.load(sid)["prep"], REVISED)
    assert snapshot["open"] == ["v1-1"] and snapshot["resolved"] == []
    assert prepare.send_email(sid, REVISED, acknowledged_open_points=True)["prep"]["email_final"] == REVISED


def test_partial_recheck_keeps_valid_rows_and_retries_only_failure(monkeypatch):
    sid, _ = start(monkeypatch, [issue(), issue(type="accuracy", quote="sick for three days",
                                                 suggestion="unwell for three days", reason="Meaning is unclear.")])
    requested = []

    def respond(old, new, items):
        requested.append([i["id"] for i in items])
        item = items[0]
        return {"results": [{"id": item["id"], "status": "unclear", "current_quote": "I have been sick",
                             "note": "Cannot infer the intended meaning."}]}

    monkeypatch.setattr(mock, "mock_recheck", respond)
    first = wf.recheck(sid, REVISED)
    assert first["status"] == "partial" and first["retryable"]
    retried = wf.recheck(sid, REVISED)
    assert requested == [["v1-1", "v1-2"], ["v1-2"]]
    assert retried["results"][0] == first["results"][0]
    assert retried["status"] == "ok" and not retried["retryable"]


@pytest.mark.parametrize("operation", ["feedback", "recheck"])
def test_overlapping_duplicate_failure_id_runs_one_attempt(monkeypatch, operation):
    if operation == "feedback":
        sid = session.create()[0]
        target, text, schema_name = wf.check_draft, DRAFT, "DraftFeedbackOut"
    else:
        sid, _ = start(monkeypatch)
        target, text, schema_name = wf.recheck, REVISED, "RecheckOut"
    entered, release = threading.Event(), threading.Event()
    original, calls = wf.call_json, []

    def fail(schema, *args, **kwargs):
        calls.append(schema.__name__)
        entered.set()
        assert release.wait(5)
        kwargs["mock"] = lambda: (_ for _ in ()).throw(httpx.ReadTimeout("mock timeout"))
        return original(schema, *args, **kwargs)

    monkeypatch.setattr(wf, "call_json", fail)
    responses, errors = [], []

    def worker():
        try:
            responses.append(target(sid, text, request_id="same-click"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    threads[0].start()
    assert entered.wait(5)
    threads[1].start()
    release.set()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert errors == [] and len(responses) == 2
    assert sorted(r["reused"] for r in responses) == [False, True]
    assert calls == [schema_name] and len(events(sid, operation)) == 1
    target(sid, text, request_id="explicit-retry")
    assert calls == [schema_name, schema_name] and len(events(sid, operation)) == 2


def test_review_retry_late_result_and_old_replay_never_restore_old_draft(monkeypatch):
    monkeypatch.setattr(mock, "mock_feedback_review", lambda items: {"reviews": []})
    sid, _ = start(monkeypatch)
    original = wf.call_json

    def edit_while_reviewing(*args, **kwargs):
        prepare.save_draft(sid, REVISED)
        return original(*args, **kwargs)

    monkeypatch.setattr(wf, "call_json", edit_while_reviewing)
    out = wf.check_draft(sid, DRAFT, request_id="slow-old-review")
    assert not out["is_current"] and out["prep"]["draft"] == REVISED
    monkeypatch.setattr(wf, "call_json", lambda *a, **k: pytest.fail("duplicate ID must not call model"))
    replay = wf.check_draft(sid, DRAFT, request_id="slow-old-review")
    assert replay["reused"] and replay["prep"]["draft"] == REVISED and not replay["is_current"]
    assert replay["prep"]["versions"][0]["text"] == DRAFT


def test_recheck_late_result_does_not_overwrite_newer_autosave(monkeypatch):
    sid, _ = start(monkeypatch)
    latest = REVISED + " I appreciate your time."
    original = wf.call_json

    def edit(*args, **kwargs):
        prepare.save_draft(sid, latest)
        return original(*args, **kwargs)

    monkeypatch.setattr(wf, "call_json", edit)
    result = wf.recheck(sid, REVISED)
    assert result["prep"]["draft"] == latest and not result["is_current"]
    assert result["prep"]["versions"][-1]["text"] == REVISED


@pytest.mark.parametrize("kind,before,after", [
    ("spelling", "recieve", "receive"),
    ("punctuation", "report,,", "report,"),
    ("capitalization", "i am", "I am"),
])
def test_mechanics_points_survive_screening_and_case_only_revision_gets_rechecked(monkeypatch, kind, before, after):
    draft = f"Dear Prof. Chen, {before} asking for an extension for my report. Thanks, Alex"
    fixed = draft.replace(before, after)
    sid, first = start(monkeypatch, [issue(type=kind, quote=before, suggestion=after, serious=False)], draft=draft)
    assert len(first["items"]) == 1 and first["items"][0]["type"] == kind
    assert first["items"][0]["judgement"] == "clear_error" and first["items"][0]["layer"] == 2
    requested = []

    def answer(old, new, items):
        requested.extend(items)
        return {"results": [{"id": "v1-1", "status": "resolved", "current_quote": after, "note": "Correct now."}]}

    monkeypatch.setattr(mock, "mock_recheck", answer)
    result = wf.recheck(sid, fixed)
    assert requested and result["results"][0]["status"] == "resolved"
    assert session.load(sid)["prep"]["draft"] == fixed


def test_false_lowercase_quote_against_correct_capitalization_is_rejected():
    kept, dropped = wf.screen_items([issue(type="capitalization", quote="i am", suggestion="I am")], "I am ill.", "")
    assert kept == [] and dropped[0]["drop_reason"] == "quote_not_in_version"


def test_unchanged_pronoun_can_be_resolved_by_new_context(monkeypatch):
    before = "Dear Prof. Chen, I discussed the report and presentation. It needs more time. Thanks, Alex"
    after = before.replace("I discussed the report and presentation.", "I am writing about the report.")
    sid, first = start(monkeypatch, [issue(type="accuracy", judgement="expression_issue", quote="It needs more time.",
                                         suggestion="The report needs more time.", reason="The referent of It is unclear.")], draft=before)
    observed = []

    def contextual(old, new, items):
        observed.append(new)
        return {"results": [{"id": "v1-1", "status": "resolved", "current_quote": "I am writing about the report. It needs more time.",
                             "note": "The preceding sentence identifies the report."}]}

    monkeypatch.setattr(mock, "mock_recheck", contextual)
    result = wf.recheck(sid, after)
    assert first["items"][0]["quote"] in after and first["items"][0]["suggestion"] not in after
    assert observed == [after] and result["results"][0]["status"] == "resolved"


@pytest.mark.parametrize("kind", ["grammar", "accuracy", "logic", "task_info", "capitalization"])
def test_nonlocal_or_uncertain_issue_never_uses_sentence_only_rule(monkeypatch, kind):
    sid, _ = start(monkeypatch, [issue(type=kind, judgement="uncertain")])
    calls = []
    monkeypatch.setattr(mock, "mock_recheck", lambda *args: calls.append(1) or {"results": [
        {"id": "v1-1", "status": "unclear", "current_quote": "I has been", "note": "Context is insufficient."}]})
    result = wf.recheck(sid, DRAFT + " Please consider this request.")
    assert calls == [1] and result["results"][0]["basis"] == "ai_unsure"


@pytest.mark.parametrize("count", [12, 15])
def test_feedback_cap_is_explicit_and_duplicate_issues_do_not_consume_slots(monkeypatch, count):
    text = "Dear Prof. Chen, " + " ".join(f"typo{n}" for n in range(count)) + ". Thanks, Alex"
    items = [issue(type="spelling", quote=f"typo{n}", suggestion=f"fixed{n}", serious=False) for n in range(count)]
    items.insert(1, deepcopy(items[0]))
    sid, out = start(monkeypatch, items, draft=text)
    feedback = out["prep"]["versions"][0]["feedback"]
    assert len(out["items"]) == 12 and feedback["item_limit"] == 12
    assert feedback["over_limit_count"] == count - 12 and feedback["possibly_incomplete"]
    assert len({(i["quote"], i["suggestion"]) for i in out["items"]}) == 12
    refreshed = session.public_state(sid, session.load(sid))["prep"]["versions"][0]["feedback"]
    assert refreshed["possibly_incomplete"] and refreshed["over_limit_count"] == count - 12


def test_meaning_protection_survives_review_retry(monkeypatch):
    monkeypatch.setattr(mock, "mock_feedback_review", lambda items: {"reviews": []})
    sid, first = start(monkeypatch, [issue(quote="by Sunday", suggestion="by Monday")])
    assert first["items"][0]["judgement"] == "uncertain" and "changes_fact" in first["items"][0]["flags"]
    monkeypatch.setattr(mock, "mock_feedback_review", lambda items: {"reviews": [
        {"id": i["id"], "verdict": "agree"} for i in items]})
    retried = wf.check_draft(sid, DRAFT)
    assert retried["items"][0]["judgement"] == "uncertain" and "changes_fact" in retried["items"][0]["flags"]
    assert session.load(sid)["prep"]["draft"] == DRAFT


def test_repeated_keep_and_clear_decisions_have_one_event_each(monkeypatch):
    sid, _ = start(monkeypatch)
    for choice in ["keep", "keep", "clear", "clear"]:
        wf.set_decision(sid, 1, "v1-1", choice)
    assert len(events(sid, "feedback_decision")) == 2


def test_old_cached_context_rule_is_retryable_after_refresh(monkeypatch):
    sid, _ = start(monkeypatch, [issue(type="accuracy")])

    def legacy(current):
        current["prep"]["draft"] = REVISED
        target = wf.version_for(current["prep"], REVISED, "recheck")
        target["recheck"] = {"base_version": 1, "results": [
            {"id": "v1-1", "status": "still_present", "basis": "sentence_unchanged", "current_quote": "I has been", "note": ""}]}

    db.mutate_state(sid, legacy)
    public = session.public_state(sid, session.load(sid))
    old = public["prep"]["versions"][-1]["recheck"]
    assert old["retryable"] and old["results"][0]["basis"] == "context_recheck_needed"
    monkeypatch.setattr(mock, "mock_recheck", lambda *args: {"results": [
        {"id": "v1-1", "status": "resolved", "current_quote": "I have been sick"}]})
    assert wf.recheck(sid, REVISED)["results"][0]["status"] == "resolved"


def test_undo_keep_reopens_context_check_instead_of_labeling_it_still_present(monkeypatch):
    original = "Dear Prof. Chen, I discussed the report and presentation. It needs more time. Thanks, Alex"
    revised = original.replace("I discussed the report and presentation.", "I mean the report.")
    sid, _ = start(monkeypatch, [issue(type="accuracy", quote="It needs more time.",
                                     suggestion="The report needs more time.", reason="Unclear referent.")], draft=original)
    wf.set_decision(sid, 1, "v1-1", "keep")
    assert wf.recheck(sid, revised)["results"][0]["status"] == "kept_by_user"
    public = wf.set_decision(sid, 1, "v1-1", "clear")
    reopened = public["prep"]["versions"][-1]["recheck"]
    assert reopened["retryable"] and reopened["results"][0]["status"] == "cannot_judge"
    assert reopened["results"][0]["basis"] == "context_recheck_needed"
    monkeypatch.setattr(mock, "mock_recheck", lambda *args: {"results": [
        {"id": "v1-1", "status": "resolved", "current_quote": "I mean the report. It needs more time."}]})
    result = wf.recheck(sid, revised)
    assert not result["reused"] and result["results"][0]["status"] == "resolved"


def test_http_request_ids_replay_failed_attempt_and_reject_different_text(monkeypatch):
    with TestClient(app) as http:
        sid = http.post("/api/sessions").json()["session_id"]
        monkeypatch.setattr(mock, "mock_feedback_review", lambda items: {"reviews": []})
        url = f"/api/sessions/{sid}/prep/feedback"
        first = http.post(url, json={"draft": DRAFT, "request_id": "attempt1"}).json()
        again = http.post(url, json={"draft": DRAFT, "request_id": "attempt1"}).json()
        assert first["review_retryable"] and not first["reused"] and again["reused"]
        assert len(events(sid, "feedback_review")) == 1
        conflict = http.post(url, json={"draft": REVISED, "request_id": "attempt1"})
        assert conflict.status_code == 409 and conflict.json()["code"] == "request_id_conflict"


def test_prompt_covers_mechanics_context_and_honest_limit():
    chapter = get_chapter("chapter2")
    prompt = render("prep_feedback", chapter=chapter, npc=chapter.npc, draft=DRAFT)
    assert "spelling" in prompt and "punctuation" in prompt and "capitalization" in prompt
    assert "Do not comment on spelling" not in prompt
    assert "at most 12" in prompt and "not a claim of completeness" in prompt
    recheck = render("prep_recheck", old_text=DRAFT, new_text=REVISED, items=[])
    assert "WHOLE revised email" in recheck and "do NOT need to use any particular wording" in recheck
