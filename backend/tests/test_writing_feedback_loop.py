"""Writing-feedback loop: version binding, rule screening, review, recheck and the send decision.

Rule behaviour is asserted directly. Model behaviour is replaced by hand-written outputs (good and
bad) so the tests never ask a model to grade itself; see tests/fixtures/feedback_gold.yaml and
tools/feedback_gold_check.py for the separate, human-expected language cases.
"""
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.llm import client, mock
from app.main import app
from app.rules.feedback_guard import is_large_rewrite, meaning_flags
from app.services import export, negotiate, outcome, prepare, session, writing_feedback as wf
from app.services.session import Conflict

V1 = ("Dear Prof. Chen,\n\nI want to ask for more time for the project report. I has been sick for three days, "
      "so I can't finish it. Can I give it to you next Wednesday? Sorry.\n\nThanks,\nAlex")
V2 = V1.replace("I has been sick", "I have been sick")
GOOD = ("Dear Prof. Chen,\n\nUnfortunately, I have been sick for three days, so I haven't been able to finish "
        "the project report by Friday. Would it be possible to hand it in next Wednesday?\n\nThanks,\nAlex")


def point(**kw):
    return {"type": "grammar", "judgement": "clear_error", "serious": True, "quote": "I has been",
            "suggestion": "I have been", "reason": "Use 'have' after 'I'."} | kw


def use_feedback(monkeypatch, items):
    monkeypatch.setattr(mock, "mock_feedback", lambda draft: {"items": items})


def new_session():
    return session.create()[0]


def versions(sid):
    return session.public_state(sid, session.load(sid))["prep"]["versions"]


# ---------------------------------------------------------------- version binding

def test_old_feedback_is_kept_and_marked_as_belonging_to_its_version():
    sid = new_session()
    first = prepare.get_feedback(sid, V1)
    assert first["version"] == 1 and first["is_current"] and first["available"]
    prepare.save_draft(sid, V2)                       # the learner starts revising
    saved = versions(sid)
    assert len(saved) == 1 and saved[0]["text"] == V1  # feedback stays with the text it was written for
    assert saved[0]["feedback"]["items"] == first["items"]
    assert session.load(sid)["prep"]["draft"] == V2
    second = prepare.get_feedback(sid, V2)
    assert second["version"] == 2
    saved = versions(sid)
    assert [v["version"] for v in saved] == [1, 2] and saved[0]["feedback"]["items"] == first["items"]
    assert all(i["id"].startswith("v1-") for i in saved[0]["feedback"]["items"])
    assert all(i["id"].startswith("v2-") for i in saved[1]["feedback"]["items"])


def test_late_result_is_stored_on_its_own_version_and_never_replaces_a_newer_draft(monkeypatch):
    sid = new_session()
    original = wf.call_json

    def learner_edits_while_model_runs(*args, **kwargs):
        prepare.save_draft(sid, V2)
        return original(*args, **kwargs)
    monkeypatch.setattr(wf, "call_json", learner_edits_while_model_runs)
    result = prepare.get_feedback(sid, V1)
    assert result["version"] == 1 and result["is_current"] is False
    assert session.load(sid)["prep"]["draft"] == V2
    assert versions(sid)[0]["text"] == V1
    assert all(writing_quote_in(i["quote"], V1) for i in result["items"])


def writing_quote_in(quote, text):
    from app.rules.quotes import quote_in
    return quote_in(quote, text)


def test_refresh_restores_versions_feedback_recheck_and_decisions():
    sid = new_session()
    first = prepare.get_feedback(sid, V1)
    wf.set_decision(sid, 1, first["items"][-1]["id"], "keep")
    wf.recheck(sid, V2)
    with TestClient(app) as http:                      # what the page receives after a reload
        prep = http.get(f"/api/sessions/{sid}").json()["prep"]
    assert prep["draft"] == V2 and [v["version"] for v in prep["versions"]] == [1, 2]
    assert prep["versions"][0]["feedback"]["status"] == "ok"
    assert prep["versions"][0]["decisions"] == {first["items"][-1]["id"]: "keep"}
    assert prep["versions"][1]["recheck"]["base_version"] == 1


def test_sessions_saved_before_this_increment_still_load_and_send():
    sid = new_session()
    state = session.load(sid)
    del state["prep"]["versions"], state["prep"]["send_decisions"]
    db.save_state(sid, state)
    assert session.public_state(sid, session.load(sid))["prep"]["versions"] == []
    assert prepare.send_email(sid, GOOD)["prep"]["email_final"] == GOOD


def test_interrupted_check_is_reported_as_retryable_not_as_feedback():
    sid = new_session()
    def crash_mid_check(current):
        current["prep"]["draft"] = V1
        wf.version_for(current["prep"], V1, "check")["feedback"] = {"status": "pending"}
    db.mutate_state(sid, crash_mid_check)
    assert versions(sid)[0]["feedback"] == {"status": "unavailable"}
    assert prepare.get_feedback(sid, V1)["available"] and len(versions(sid)) == 1


# ---------------------------------------------------------------- repeat clicks, failure, retry

def test_repeated_request_for_unchanged_text_reuses_the_result(monkeypatch):
    sid = new_session()
    calls = []
    original = wf.call_json
    monkeypatch.setattr(wf, "call_json", lambda *a, **k: calls.append(1) or original(*a, **k))
    first = prepare.get_feedback(sid, V1)
    again = prepare.get_feedback(sid, V1)
    assert again["reused"] and again["items"] == first["items"] and len(versions(sid)) == 1
    assert len(calls) == 2                              # one check + one review, not four
    assert session.load(sid)["prep"]["feedback_requests"] == 1


def test_overlapping_double_click_runs_one_check(monkeypatch):
    sid = new_session()
    calls, gate = [], threading.Event()
    original = wf.call_json

    def slow(*a, **k):
        calls.append(1)
        gate.wait(2)
        return original(*a, **k)
    monkeypatch.setattr(wf, "call_json", slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(prepare.get_feedback(sid, V1))) for _ in range(2)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(10)
    assert len(results) == 2 and sorted(r["reused"] for r in results) == [False, True]
    assert len(calls) == 2 and len(versions(sid)) == 1


@pytest.mark.parametrize("failure", ["timeout", "invalid_json", "wrong_shape"])
def test_failed_check_is_unavailable_keeps_the_draft_and_retries_on_the_same_version(monkeypatch, failure):
    sid = new_session()
    monkeypatch.setenv("LLM_MOCK", "0")

    def bad(_messages):
        if failure == "timeout":
            raise httpx.ReadTimeout("provider did not answer")
        return "not json at all" if failure == "invalid_json" else '{"items": [{"type": "spelling"}]}'
    monkeypatch.setattr(client, "http_transport", bad)
    failed = prepare.get_feedback(sid, V1)
    assert failed["available"] is False and failed["items"] == []
    assert session.load(sid)["prep"]["draft"] == V1
    assert versions(sid)[0]["feedback"]["status"] == "unavailable"     # never shown as "no problems found"

    monkeypatch.setenv("LLM_MOCK", "1")
    retried = prepare.get_feedback(sid, V1)
    assert retried["available"] and not retried["reused"] and retried["version"] == 1
    assert len(versions(sid)) == 1
    decisions = [e["rule_decision"] for e in db.list_events(sid) if e["kind"] == "feedback"]
    assert decisions == ["model_failed", "ok"]


# ---------------------------------------------------------------- rule screening of model output

def test_points_without_real_quote_reason_or_suggestion_are_not_shown(monkeypatch):
    sid = new_session()
    use_feedback(monkeypatch, [
        point(quote="I has been ill since Monday"),                    # words the learner never wrote
        point(reason="   "),                                            # no reason
        point(quote="so I can't finish it", suggestion=""),             # no suggestion
        point(quote="Can I give it to you", suggestion="can i give it to you"),   # nothing changed
        point(quote="Sorry", type="politeness", judgement="optional", suggestion="I apologise", reason="More formal."),
    ])
    out = prepare.get_feedback(sid, V1)
    assert [i["quote"] for i in out["items"]] == ["Sorry"]
    event = [e for e in db.list_events(sid) if e["kind"] == "feedback"][-1]
    import json
    reasons = [d["drop_reason"] for d in json.loads(event["payload_json"])["dropped"]]
    assert reasons == ["quote_not_in_version", "reason_missing", "suggestion_missing", "suggestion_unchanged"]
    assert versions(sid)[0]["feedback"]["dropped_count"] == 4


def test_every_shown_point_has_quote_from_its_version_suggestion_reason_and_type():
    sid = new_session()
    for item in prepare.get_feedback(sid, V1)["items"]:
        assert writing_quote_in(item["quote"], V1) and item["reason"] and item["type"] and item["id"]
        assert item["suggestion"] or item["type"] == "positive"


def test_layers_put_serious_grammar_first_task_information_early_and_style_last(monkeypatch):
    sid = new_session()
    use_feedback(monkeypatch, [
        point(type="naturalness", judgement="optional", serious=False, quote="Sorry", suggestion="I'm sorry", reason="r"),
        point(type="logic", judgement="expression_issue", serious=False, quote="so I can't finish it",
              suggestion="so I couldn't work on it", reason="r"),
        point(type="task_info", judgement="expression_issue", serious=False, quote="for more time",
              suggestion="for more time for the report", reason="r"),
        point(),
        point(type="grammar", judgement="clear_error", serious=False, quote="Can I give it", suggestion="May I give it", reason="r"),
    ])
    out = prepare.get_feedback(sid, V1)
    assert [(i["type"], i["layer"]) for i in out["items"]] == [
        ("task_info", 1), ("grammar", 1), ("logic", 2), ("grammar", 2), ("naturalness", 3)]


def test_style_is_never_reported_as_an_error(monkeypatch):
    sid = new_session()
    use_feedback(monkeypatch, [point(type="politeness", judgement="clear_error", quote="Can I give it to you",
                                     suggestion="Could I give it to you", reason="More polite.")])
    item = prepare.get_feedback(sid, V1)["items"][0]
    assert item["judgement"] == "optional" and "style_downgraded" in item["flags"] and item["layer"] == 3


@pytest.mark.parametrize("quote, suggestion, flag", [
    ("next Wednesday", "next Friday", "changes_fact"),                    # a date is replaced
    ("for three days", "for five days", "changes_fact"),                  # a number is replaced
    ("so I can't finish it", "so I can finish it", "changes_negation"),   # the meaning is reversed
    ("Can I give it to you", "Can I give it to you at 9 am on Monday", "adds_unstated_fact"),  # invented detail
])
def test_suggestions_that_change_meaning_are_marked_uncertain_by_rule_even_if_the_reviewer_agrees(
        monkeypatch, quote, suggestion, flag):
    sid = new_session()
    use_feedback(monkeypatch, [point(quote=quote, suggestion=suggestion)])
    item = prepare.get_feedback(sid, V1)["items"][0]      # the mock reviewer agrees with everything
    assert item["judgement"] == "uncertain" and flag in item["flags"] and item["reviewed"] is True


def test_guard_allows_ordinary_corrections_and_facts_already_stated():
    assert meaning_flags("I has been sick for three days", "I have been sick for 3 days") == []
    assert meaning_flags("I can't finish it", "I cannot finish it") == []
    assert meaning_flags("I didn't went", "I didn't go") == []
    assert meaning_flags("can't finish it", "can't finish it by Friday",
                         allowed_context="The report is due Friday at 18:00.") == []
    assert meaning_flags("May I ask", "Could I ask") == []            # modal "may" is not the month
    assert meaning_flags("on May 3", "on 3 May") == []
    assert not is_large_rewrite("I has been sick", "I have been sick")
    assert is_large_rewrite("I has been sick", "Unfortunately my health was poor during the whole of this week")


def test_reviewer_doubt_marks_points_uncertain_and_is_not_a_guarantee(monkeypatch):
    sid = new_session()
    use_feedback(monkeypatch, [
        point(),
        point(quote="so I can't finish it", type="accuracy", judgement="expression_issue",
              suggestion="so I couldn't finish it", reason="Past ability."),
        point(quote="Can I give it to you", serious=False, suggestion="May I give it to you", reason="r"),
    ])
    monkeypatch.setattr(mock, "mock_feedback_review", lambda items: {"reviews": [
        {"id": items[0]["id"], "verdict": "agree", "note": ""},
        {"id": items[1]["id"], "verdict": "disagree", "note": "The original is acceptable here."},
        {"id": "v9-99", "verdict": "disagree", "note": "unknown id is ignored"},
    ]})
    out = prepare.get_feedback(sid, V1)
    by_quote = {i["quote"]: i for i in out["items"]}
    assert by_quote["I has been"]["judgement"] == "clear_error" and by_quote["I has been"]["reviewed"]
    doubted = by_quote["so I can't finish it"]
    assert doubted["judgement"] == "uncertain" and "reviewer_disagrees" in doubted["flags"]
    assert doubted["review_note"] == "The original is acceptable here."
    unreviewed = by_quote["Can I give it to you"]
    assert unreviewed["reviewed"] is False and "not_reviewed" in unreviewed["flags"]
    assert "cannot find every error" in out["disclaimer"]


@pytest.mark.parametrize("failure", ["timeout", "invalid"])
def test_review_failure_keeps_the_points_but_says_they_were_not_double_checked(monkeypatch, failure):
    sid = new_session()

    def broken_review(items):
        if failure == "timeout":
            raise httpx.ReadTimeout("slow")
        return {"reviews": [{"id": "x", "verdict": "certainly"}]}
    monkeypatch.setattr(mock, "mock_feedback_review", broken_review)
    out = prepare.get_feedback(sid, V1)
    assert out["available"] and out["review_status"] == "unavailable"
    issues = [i for i in out["items"] if i["type"] != "positive"]
    assert issues and all(i["reviewed"] is False and "not_reviewed" in i["flags"] for i in issues)
    kinds = [(e["kind"], e["rule_decision"]) for e in db.list_events(sid) if e["kind"].startswith("feedback")]
    assert kinds == [("feedback", "ok"), ("feedback_review", "model_failed")]


def test_no_points_is_not_presented_as_error_free(monkeypatch):
    sid = new_session()
    use_feedback(monkeypatch, [])
    out = prepare.get_feedback(sid, GOOD)
    assert out["available"] and out["items"] == [] and out["review_status"] == "skipped"
    assert "cannot find every error" in out["disclaimer"]


# ---------------------------------------------------------------- recheck after revising

def checked(monkeypatch=None, items=None):
    sid = new_session()
    if items is not None:
        use_feedback(monkeypatch, items)
    return sid, prepare.get_feedback(sid, V1)


def statuses(result):
    return {r["id"]: (r["status"], r["basis"]) for r in result["results"]}


def test_untouched_sentence_is_still_present_by_rule_without_asking_the_model(monkeypatch):
    sid, first = checked(monkeypatch, [point()])
    monkeypatch.setattr(wf, "call_json", lambda *a, **k: pytest.fail("no model call is needed"))
    result = wf.recheck(sid, V1.replace("Sorry.", "I am sorry for the trouble."))
    assert statuses(result) == {"v1-1": ("still_present", "sentence_unchanged")}
    assert result["version"] == 2 and result["base_version"] == 1
    event = [e for e in db.list_events(sid) if e["kind"] == "recheck"][-1]
    assert event["rule_decision"] == "no_model_needed" and event["model"] is None


def test_point_the_learner_chose_to_keep_is_reported_as_kept_not_as_a_fault(monkeypatch):
    sid, first = checked(monkeypatch, [point(), point(type="politeness", judgement="optional", quote="Sorry",
                                                       suggestion="I apologise", reason="More formal.")])
    kept = next(i["id"] for i in first["items"] if i["type"] == "politeness")
    wf.set_decision(sid, 1, kept, "keep")
    result = wf.recheck(sid, V2)
    assert statuses(result)[kept] == ("kept_by_user", "learner_kept")
    wf.set_decision(sid, 1, kept, "clear")
    assert wf.review_state(session.load(sid)["prep"], V2)["kept"] == []


def test_fix_in_the_learners_own_words_counts_as_resolved(monkeypatch):
    sid, _ = checked(monkeypatch, [point()])
    own_way = V1.replace("I has been sick for three days", "I was ill for three days")   # not the suggested "I have been"
    monkeypatch.setattr(mock, "mock_recheck", lambda old, new, items: {"results": [
        {"id": "v1-1", "status": "resolved", "current_quote": "I was ill for three days", "note": "Agreement is correct now."}]})
    assert statuses(wf.recheck(sid, own_way)) == {"v1-1": ("resolved", "ai_recheck")}


def test_copying_the_suggestion_is_not_treated_as_proof(monkeypatch):
    sid, _ = checked(monkeypatch, [point(quote="I has been sick", suggestion="I have been sick")])
    pasted = V1.replace("I has been sick for three days", "I have been sick since three days")   # suggestion pasted, new error
    monkeypatch.setattr(mock, "mock_recheck", lambda old, new, items: {"results": [
        {"id": "v1-1", "status": "unclear", "current_quote": "I have been sick since three days", "note": "Changed, but not clearly right."}]})
    result = wf.recheck(sid, pasted)
    assert "I have been sick" in pasted and statuses(result) == {"v1-1": ("cannot_judge", "ai_unsure")}


def test_recheck_that_cites_words_not_in_the_new_draft_is_cannot_judge(monkeypatch):
    sid, _ = checked(monkeypatch, [point()])
    monkeypatch.setattr(mock, "mock_recheck", lambda old, new, items: {"results": [
        {"id": "v1-1", "status": "resolved", "current_quote": "I have been unwell all week", "note": ""}]})
    assert statuses(wf.recheck(sid, V2)) == {"v1-1": ("cannot_judge", "evidence_not_in_draft")}


def test_changed_wording_with_the_same_problem_is_still_present(monkeypatch):
    sid, _ = checked(monkeypatch, [point()])
    revised = V1.replace("I has been sick", "I has been very sick")
    result = wf.recheck(sid, revised)                    # mock re-applies the same pattern to the new text
    assert statuses(result) == {"v1-1": ("still_present", "ai_recheck")}


def test_missing_information_added_in_the_same_sentence_is_not_judged_by_substring(monkeypatch):
    sid, first = checked()
    info = next(i for i in first["items"] if i["type"] == "task_info")
    revised = V1.replace("so I can't finish it.", "so I can't finish it by Friday.")
    assert info["quote"] in revised                       # the old words are still a substring
    assert statuses(wf.recheck(sid, revised))[info["id"]] == ("resolved", "ai_recheck")


@pytest.mark.parametrize("failure", ["timeout", "invalid", "no_answer"])
def test_recheck_failure_is_cannot_judge_and_can_be_retried(monkeypatch, failure):
    sid, _ = checked(monkeypatch, [point()])
    working = lambda old, new, items: {"results": [  # noqa: E731
        {"id": "v1-1", "status": "resolved", "current_quote": "I have been sick", "note": ""}]}

    def broken(old, new, items):
        if failure == "timeout":
            raise httpx.ReadTimeout("slow")
        return {"results": [{"id": "v1-1", "status": "fixed"}]} if failure == "invalid" else {"results": []}
    monkeypatch.setattr(mock, "mock_recheck", broken)
    result = wf.recheck(sid, V2)
    expected = "ai_no_answer" if failure == "no_answer" else "ai_unavailable"
    assert statuses(result) == {"v1-1": ("cannot_judge", expected)}
    # Missing answers are failed checks too, not successful uncertainty.
    assert result["available"] is False and result["retryable"] is True
    monkeypatch.setattr(mock, "mock_recheck", working)
    retried = wf.recheck(sid, V2)
    assert not retried["reused"] and statuses(retried) == {"v1-1": ("resolved", "ai_recheck")}
    assert retried["available"] and retried["retryable"] is False
    assert len(versions(sid)) == 2                        # the retry did not add a version


def test_repeated_recheck_of_unchanged_text_is_reused(monkeypatch):
    sid, _ = checked()
    first = wf.recheck(sid, V2)
    monkeypatch.setattr(wf, "call_json", lambda *a, **k: pytest.fail("already rechecked"))
    again = wf.recheck(sid, V2)
    assert again["reused"] and again["results"] == first["results"]


def test_recheck_needs_a_checked_version_and_reports_it_as_a_game_limit():
    sid = new_session()
    with pytest.raises(Conflict) as error:
        wf.recheck(sid, V1)
    assert error.value.code == "no_feedback_to_recheck"


# ---------------------------------------------------------------- the learner decides to send

def test_learner_can_send_with_open_points_and_the_decision_is_recorded():
    sid, first = checked()
    kept = first["items"][-1]["id"]
    wf.set_decision(sid, 1, kept, "keep")
    out = prepare.send_email(sid, V1, acknowledged_open_points=True)       # nothing was changed at all
    assert out["prep"]["email_final"] == V1
    decision = session.load(sid)["prep"]["send_decisions"][-1]
    assert decision["feedback_state"] == "checked_this_version" and decision["version"] == 1
    assert decision["kept_points"] == [kept] and len(decision["open_points"]) == len(first["items"]) - 1
    assert decision["acknowledged_open_points"] is True


def test_send_after_recheck_records_resolved_open_and_unjudged_points(monkeypatch):
    sid, _ = checked(monkeypatch, [point(), point(type="politeness", judgement="optional", quote="Sorry",
                                                  suggestion="I apologise", reason="More formal.")])
    monkeypatch.setattr(mock, "mock_recheck", lambda old, new, items: {"results": [
        {"id": "v1-1", "status": "resolved", "current_quote": "I have been sick", "note": ""}]})
    wf.recheck(sid, V2)
    prepare.send_email(sid, V2)
    decision = session.load(sid)["prep"]["send_decisions"][-1]
    assert decision["feedback_state"] == "rechecked_this_version" and decision["version"] == 2
    assert decision["resolved_points"] == ["v1-1"] and decision["open_points"] == ["v1-2"]


def test_send_of_text_changed_after_the_check_is_recorded_as_not_rechecked():
    sid, first = checked()
    prepare.send_email(sid, V2)
    decision = session.load(sid)["prep"]["send_decisions"][-1]
    assert decision["feedback_state"] == "changed_since_check"
    assert len(decision["open_points"]) == len([i for i in first["items"] if i["type"] != "positive"])


def test_send_without_ever_asking_for_feedback_still_works():
    sid = new_session()
    out = prepare.send_email(sid, GOOD)
    assert out["prep"]["email_final"] == GOOD
    assert session.load(sid)["prep"]["send_decisions"][-1]["feedback_state"] == "none"


def test_game_limits_are_reported_with_codes_separate_from_language_advice():
    sid = new_session()
    with TestClient(app) as http:
        base = f"/api/sessions/{sid}/prep"
        empty = http.post(f"{base}/feedback", json={"draft": "   "})
        short = http.post(f"{base}/send", json={"email": "Hi."})
        long = http.post(f"{base}/send", json={"email": "x" * 2001})
        assert (empty.status_code, empty.json()["code"]) == (409, "draft_empty")
        assert (short.status_code, short.json()["code"]) == (409, "email_too_short")
        assert long.status_code == 422
        assert session.load(sid)["prep"]["email_final"] is None
        ok = http.post(f"{base}/send", json={"email": V1, "acknowledged_open_points": False})
        assert ok.status_code == 200 and ok.json()["prep"]["email_final"] == V1   # errors in V1 do not block


def test_version_limit_stops_new_checks_but_never_sending():
    sid = new_session()
    def fill(current):
        current["prep"]["versions"] = [{"version": n, "text": f"old text {n} " * 3, "created_at": None, "origin": "check",
                                        "feedback": None, "recheck": None, "decisions": {}} for n in range(1, wf.MAX_VERSIONS + 1)]
    db.mutate_state(sid, fill)
    with pytest.raises(Conflict) as error:
        prepare.get_feedback(sid, V1)
    assert error.value.code == "version_limit"
    assert prepare.send_email(sid, V1)["prep"]["email_final"] == V1
    assert session.load(sid)["prep"]["send_decisions"][-1]["version"] is None


def test_back_to_edit_keeps_history_and_feedback_cannot_arrive_after_sending():
    sid, _ = checked()
    prepare.send_email(sid, V1)
    with pytest.raises(Conflict):
        prepare.get_feedback(sid, V2)
    with pytest.raises(Conflict):
        wf.recheck(sid, V2)
    with pytest.raises(Conflict):
        wf.set_decision(sid, 1, "v1-1", "keep")
    prepare.edit_again(sid)
    assert len(versions(sid)) == 1 and versions(sid)[0]["feedback"]["status"] == "ok"
    assert wf.recheck(sid, V2)["version"] == 2
    prepare.send_email(sid, V2)
    assert len(session.load(sid)["prep"]["send_decisions"]) == 2


# ---------------------------------------------------------------- HTTP routes, export, regression

def test_routes_cover_the_loop_and_validate_decisions():
    with TestClient(app) as http:
        sid = http.post("/api/sessions").json()["session_id"]
        base = f"/api/sessions/{sid}/prep"
        first = http.post(f"{base}/feedback", json={"draft": V1}).json()
        item = first["items"][0]["id"]
        assert http.post(f"{base}/feedback/decision", json={"version": 1, "item_id": item, "decision": "keep"}).status_code == 200
        assert http.post(f"{base}/feedback/decision", json={"version": 1, "item_id": item, "decision": "delete"}).status_code == 422
        assert http.post(f"{base}/feedback/decision", json={"version": 1, "item_id": "v1-99", "decision": "keep"}).status_code == 404
        assert http.post(f"{base}/feedback/decision", json={"version": 7, "item_id": item, "decision": "keep"}).status_code == 404
        rechecked = http.post(f"{base}/recheck", json={"draft": V2})
        assert rechecked.status_code == 200 and rechecked.json()["base_version"] == 1
        assert http.post(f"{base}/recheck", json={"draft": "x" * 2001}).status_code == 422


def test_export_summarises_versions_without_text_unless_opted_in():
    sid, first = checked()
    wf.set_decision(sid, 1, first["items"][-1]["id"], "keep")
    wf.recheck(sid, V2)
    prepare.send_email(sid, V2, acknowledged_open_points=True)
    record = export.build_record(sid)
    rows = record["preparation"]["draft_versions"]
    assert [r["version"] for r in rows] == [1, 2] and rows[0]["feedback_status"] == "ok"
    assert rows[0]["points_by_type"] == {"grammar": 1, "politeness": 1, "task_info": 1}
    assert rows[0]["points_learner_kept"] == 1 and rows[1]["recheck_base_version"] == 1
    assert sum(rows[1]["recheck_by_status"].values()) == 3
    assert record["preparation"]["send_decisions"][0]["acknowledged_open_points"] is True
    import json
    flat = json.dumps(record)
    assert "I has been" not in flat and "Subject" not in flat and "text" not in rows[0]
    assert {e["kind"] for e in record["events"]} >= {"feedback", "feedback_review", "feedback_decision", "recheck"}
    assert not [w for w in record["data_quality_warnings"] if w["code"] == "event_metadata_invalid"]
    with_text = export.build_record(sid, include_text=True)["preparation"]["draft_versions"]
    assert with_text[0]["text"] == V1 and with_text[0]["points"][0]["quote"] == "I has been"


def through_the_loop_to_the_office():
    sid, _ = checked()
    wf.recheck(sid, GOOD)
    prepare.send_email(sid, GOOD)
    prepare.confirm_summary(sid)
    out = prepare.submit_prediction(sid, "He may say Wednesday is too late.", "Outline first.")
    assert out["phase"] == "negotiate" and out["prep"]["versions"] == []    # history is not resent every turn
    negotiate.start(sid)
    return sid


def say(sid, text, counter=[0]):
    counter[0] += 1
    return negotiate.turn(sid, text, request_id=f"loop-{counter[0]:06d}")


def test_staged_ending_still_reachable_after_the_feedback_loop():
    sid = through_the_loop_to_the_office()
    say(sid, "Yes. Unfortunately, I've been sick since Monday, so I haven't been able to work on it.")
    say(sid, "I could send you the outline by Friday and the full report by Sunday evening.")
    out = say(sid, "Yes, that's right. I'll make sure they're on time.")
    assert out["negotiation"]["agreement"]["status"] == "confirmed"
    negotiate.finish(sid)
    assert outcome.outcome(sid)["ending"]["id"] == "staged"
    assert len(session.load(sid)["prep"]["versions"]) == 2      # the draft history survives the chapter


def test_full_ending_still_reachable_after_the_feedback_loop():
    sid = through_the_loop_to_the_office()
    say(sid, "I'm sorry, I've been ill.")
    say(sid, "I could finish the whole report by Saturday at noon.")
    out = negotiate.confirm(sid)
    assert out["negotiation"]["agreement"]["status"] == "confirmed"
    negotiate.finish(sid)
    assert outcome.outcome(sid)["ending"]["id"] == "full"
