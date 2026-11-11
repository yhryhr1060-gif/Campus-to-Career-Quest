"""Strategy praise needs a checked action and the learner's own evidence, not keywords."""
import json

import pytest

from app.services import negotiate, outcome, prepare, session


def fact(field="final_due", quote="Sunday", **overrides):
    return {"field": field, "value": "SUN" if field != "confirm" else None,
            "quote": quote, "source": "player", "polarity": "affirm", "mode": "assert", **overrides}


def event(text, *, kind="restate", facts=None, dropped=None, event_kind="turn"):
    return {
        "phase": "negotiate", "kind": event_kind, "player_text": text, "rule_decision": kind,
        "payload_json": json.dumps({"decision": {
            "kind": kind, "plan": {"submission_plan": "full", "final_due": "SUN 18:00"},
            "became_confirmed": kind == "confirmed", "dropped": dropped or [],
        }}),
        "raw_output": json.dumps({"raw": [json.dumps({
            "reply": "So that's the report by Sunday. Can you confirm?",
            "candidate_facts": facts if facts is not None else [fact()],
        })]}),
    }


def praises(lines, events=None):
    return [item for item in outcome.strategy_feedback(lines, events=events) if item["kind"] == "did_well"]


@pytest.mark.parametrize("text", [
    "I can't submit it by Sunday.",
    "My friend said I could send it by Sunday.",
    "I will send nothing.",
    "I could send it by Sunday.",
    "I've been sick.",
])
def test_keywords_without_verified_events_never_produce_praise(text):
    assert not praises([text])


@pytest.mark.parametrize("text", [
    "I can't submit it by Sunday.",
    "My friend said I could send it by Sunday.",
    "I will send nothing by Sunday.",
])
def test_refusals_and_reported_speech_not_praised_even_if_model_mislabels_them(text):
    assert not praises([text], [event(text)])


def test_checked_player_proposal_is_praised_with_real_quote():
    text = "I could send the report by Sunday."
    assert praises([text], [event(text)]) == [{
        "move": "alternative", "kind": "did_well",
        "text": "You put forward a concrete plan that passed the deadline checks", "quote": text,
    }]


@pytest.mark.parametrize("source", ["email", "npc_previous"])
def test_other_sources_do_not_become_player_proposal_evidence(source):
    text = "Sunday sounds familiar."
    assert not praises([text], [event(text, facts=[fact(source=source)])])


@pytest.mark.parametrize("changed", [
    {"quote": "Saturday"}, {"polarity": "negate"}, {"mode": "quoted"},
])
def test_unsubstantiated_or_unaccepted_fact_is_not_praised(changed):
    text = "I could send it by Sunday."
    assert not praises([text], [event(text, facts=[fact(**changed)])])


def test_rule_dropped_fact_does_not_support_praise():
    text = "I could send it by Sunday."
    assert not praises([text], [event(text, dropped=[{"reason": "quote_not_found", "fact": fact()}])])


@pytest.mark.parametrize("kind", ["none", "incomplete", "rejected", "constraint_change", "model_failed"])
def test_unaccepted_proposal_is_not_praised(kind):
    text = "I could send it by Sunday."
    assert not praises([text], [event(text, kind=kind)])


def test_conditional_offer_can_be_a_proposal_but_not_a_commitment():
    text = "If I finish the outline, could I send the report by Sunday?"
    rows = praises([text], [event(text, facts=[fact(mode="conditional")])])
    assert [item["move"] for item in rows] == ["alternative"]


@pytest.mark.parametrize("text", [
    "Yes, if I finish in time.", "Yes. I can't send it.",
    "My friend said yes.", "Yes, I will send nothing.", "Yes, maybe.",
])
def test_qualified_or_reported_confirmation_is_never_praised(text):
    assert not praises([text], [event(text, kind="confirmed", facts=[fact("confirm", "yes")])])


def test_text_confirmation_is_described_without_claiming_independent_language_use():
    text = "Yes, that's right. I'll make sure it's on time."
    rows = praises([text], [event(text, kind="confirmed", facts=[fact("confirm", "Yes")])])
    assert rows == [{"move": "commitment", "kind": "did_well",
                     "text": "You confirmed the restated plan in your reply", "quote": text}]


def test_button_confirmation_is_not_language_evidence():
    text = "Yes."
    assert not praises([text], [event(text, kind="confirmed", event_kind="confirm_button",
                                      facts=[fact("confirm", "Yes")])])


def test_prior_attempt_is_excluded_even_when_the_same_text_is_repeated():
    text = "I could send it by Sunday."
    events = [event(text), {"phase": "negotiate", "kind": "retry"}, event(text, kind="model_failed")]
    assert not praises([text], events)


@pytest.mark.parametrize("value", [None, "", "not JSON", "[]", '{"raw": []}'])
def test_missing_or_old_raw_logs_preserve_response_shape(value):
    text = "I could send it by Sunday."
    logged = event(text)
    logged["raw_output"] = value
    result = outcome.strategy_feedback([text], events=[logged])
    assert 1 <= len(result) <= 2
    assert all(item["kind"] == "try_next" and item["quote"] is None for item in result)


def test_malformed_decision_log_cannot_supply_positive_feedback():
    text = "I could send it by Sunday."
    logged = event(text)
    payload = json.loads(logged["payload_json"])
    payload["decision"]["dropped"] = None
    logged["payload_json"] = json.dumps(payload)
    assert not praises([text], [logged])


def test_outcome_reads_real_events_and_keeps_button_confirmation_separate():
    sid, _ = session.create()
    prepare.send_email(sid, "Dear Prof. Chen, I've been ill. Could I submit next Wednesday? Thanks, Alex")
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "", "")
    negotiate.start(sid)
    negotiate.turn(sid, "I could send the report by Saturday.", request_id="strategy-1")
    negotiate.turn(sid, "I could send the report by Saturday.", request_id="strategy-2")
    negotiate.confirm(sid)
    negotiate.finish(sid)
    result = outcome.outcome(sid)
    positive = [item for item in result["strategy"] if item["kind"] == "did_well"]
    assert [item["move"] for item in positive] == ["alternative"]
    assert positive[0]["quote"] == "I could send the report by Saturday."
