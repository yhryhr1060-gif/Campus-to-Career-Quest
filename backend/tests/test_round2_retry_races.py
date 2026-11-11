"""A dropped HTTP connection can leave a live request behind its retry.

These use real threads and real reentrant locks. Events place the second request
at the lock boundary while the first model call is still deliberately blocked.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, RLock, local

from app import db, session_serialization
from app.services import negotiate, prepare, session


def ready():
    sid, _ = session.create()
    prepare.send_email(sid, "Dear Prof. Chen, I have been ill for three days. "
                            "Could I hand in my report next Wednesday? Thanks, Alex")
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "He may ask for an earlier date.", "Offer an outline first.")
    negotiate.start(sid)
    return sid


def overlapping_turns(monkeypatch, sid, first_request, second_request):
    first_model_entered = Event()
    second_lock_entered = Event()
    second_found_unlocked = Event()
    release_first_model = Event()
    roles = local()
    calls = []
    calls_guard = Lock()
    original_call_json = negotiate.call_json

    class ObservedRLock:
        """Only observes lock entry; exclusion is provided by a real RLock."""

        def __init__(self):
            self.lock = RLock()

        def __enter__(self):
            if getattr(roles, "request", None) == "second":
                # A nonblocking probe proves this is the same lock already held
                # by the first request, rather than merely a started thread.
                if self.lock.acquire(blocking=False):
                    self.lock.release()
                    second_found_unlocked.set()
                second_lock_entered.set()
            return self.lock.__enter__()

        def __exit__(self, *args):
            return self.lock.__exit__(*args)

    def blocked_model(*args, **kwargs):
        with calls_guard:
            calls.append(args[1])
            first = len(calls) == 1
        if first:
            first_model_entered.set()
            assert release_first_model.wait(15), "test did not release the first model request"
        return original_call_json(*args, **kwargs)

    def invoke(role, request):
        roles.request = role
        return negotiate.turn(sid, request["text"], request_id=request["id"])

    monkeypatch.setattr(session_serialization, "RLock", ObservedRLock)
    monkeypatch.setattr(negotiate, "call_json", blocked_model)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke, "first", first_request)
        try:
            assert first_model_entered.wait(10), "first request never reached the model"
            second = executor.submit(invoke, "second", second_request)
            assert second_lock_entered.wait(10), "second request never reached the session lock"
            assert not second_found_unlocked.is_set(), "the in-flight request did not hold the same session lock"
            # The first model is still blocked. A second model call here would prove
            # the requests were not actually serialized.
            with calls_guard:
                assert len(calls) == 1
        finally:
            release_first_model.set()
        return first.result(timeout=15), second.result(timeout=15), calls


def test_overlapping_same_id_reuses_one_model_result_and_one_turn(monkeypatch):
    sid = ready()
    request = {"id": "overlap-same-0001", "text": "I have been ill and need more time."}
    first, repeated, calls = overlapping_turns(monkeypatch, sid, request, request)

    assert first == repeated
    assert first["available"] is True
    assert len(calls) == 1
    state = session.load(sid)
    assert state["negotiation"]["turn_count"] == 1
    assert [t["text"] for t in state["negotiation"]["turns"] if t["speaker"] == "player"] == [request["text"]]
    turns = [event for event in db.list_events(sid) if event["kind"] == "turn"]
    assert len(turns) == 1
    assert json.loads(turns[0]["payload_json"])["request_id"] == request["id"]
    assert db.get_processed(sid, request["id"]) == first


def test_overlapping_different_ids_preserve_both_turns_in_order(monkeypatch):
    sid = ready()
    first_request = {"id": "overlap-first-0001", "text": "I have been ill and need more time."}
    second_request = {"id": "overlap-second-0002", "text": "Could you explain what you need from me?"}
    first, second, calls = overlapping_turns(monkeypatch, sid, first_request, second_request)

    assert len(calls) == 2
    assert first["negotiation"]["turn_count"] == 1
    assert second["negotiation"]["turn_count"] == 2
    state = session.load(sid)
    assert state["negotiation"]["turn_count"] == 2
    assert [t["text"] for t in state["negotiation"]["turns"] if t["speaker"] == "player"] == [
        first_request["text"], second_request["text"],
    ]
    # The second request must build its model context from the newly saved first turn.
    assert {"role": "user", "content": first_request["text"]} in calls[1][:-1]
    assert calls[1][-1] == {"role": "user", "content": second_request["text"]}
    turns = [event for event in db.list_events(sid) if event["kind"] == "turn"]
    payloads = [json.loads(event["payload_json"]) for event in turns]
    assert [payload["turn_no"] for payload in payloads] == [1, 2]
    assert [payload["request_id"] for payload in payloads] == [first_request["id"], second_request["id"]]
