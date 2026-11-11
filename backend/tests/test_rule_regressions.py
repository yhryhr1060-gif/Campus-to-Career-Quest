"""Previously reproducible agreement, withdrawal and saved-time failures."""
import json

import pytest

from app.llm.mock import mock_npc_turn
from app.llm.schemas import CandidateFact, NpcTurn
from app.rules.engine import (
    confirm_by_button, evaluate_turn, new_agreement_state, plan_from_dict, summarize_state,
)


def say(chapter, state, text, previous=""):
    output = NpcTurn.model_validate(mock_npc_turn(text, previous))
    return evaluate_turn(chapter, state, output.candidate_facts,
                         player_text=text, npc_previous=previous)


def offer(chapter, *, pending=False, text="I can send the full report by Saturday at noon."):
    state = new_agreement_state()
    state["pushbacks"] = 0 if pending else 1
    decision = say(chapter, state, text)
    assert decision.kind == ("follow_up" if pending else "restate")
    return state, decision.reply_override


@pytest.mark.parametrize("text", [
    "Yes. But I cannot promise that.",
    "Yes. If I feel better.",
    "Yes. Maybe I can manage it.",
    "Yes. Actually, wait a moment.",
    "Yes? I can probably do it.",
    "Yes. On second thought, let me check.",
])
def test_confirmation_checks_the_whole_turn(chapter, text):
    state, previous = offer(chapter)
    decision = say(chapter, state, text, previous)
    assert decision.kind == "confirm_unclear"
    assert state["status"] == "proposed"
    assert state["confirmed"] is None


@pytest.mark.parametrize("text", [
    "Yes. I'll make sure it's on time.",
    "Yes, no problem.",
    "I confirm. Thank you.",
])
def test_clear_unqualified_confirmation_still_works(chapter, text):
    state, previous = offer(chapter)
    assert say(chapter, state, text, previous).kind == "confirmed"


@pytest.mark.parametrize("source,mode,text,quote", [
    ("npc_previous", "assert", "I need to think.", "Yes"),
    ("email", "assert", "I need to think.", "Yes"),
    ("player", "conditional", "Yes", "Yes"),
    ("player", "question", "Yes", "Yes"),
    ("player", "quoted", "Yes", "Yes"),
    ("player", "assert", "My friend said yes.", "yes"),
    ("player", "assert", "Yesterday was difficult.", "Yes"),
])
def test_model_cannot_confirm_using_the_wrong_source_or_mode(chapter, source, mode, text, quote):
    state, _ = offer(chapter)
    fact = CandidateFact(field="confirm", quote=quote, source=source, polarity="affirm", mode=mode)
    decision = evaluate_turn(chapter, state, [fact], player_text=text, npc_previous="Yes", email="Yes")
    assert decision.dropped
    assert not decision.became_confirmed
    assert state["status"] == "proposed"


@pytest.mark.parametrize("pending", [False, True])
def test_refused_plan_cannot_return_from_pending_or_draft(chapter, pending):
    state, previous = offer(chapter, pending=pending)
    assert say(chapter, state, "No, I cannot do Saturday.", previous).kind == "withdrawn"
    assert state["pending"] is None and state["proposal"] is None
    assert not state["draft"]["final_due"]
    assert say(chapter, state, "What can I do?").kind == "none"
    assert confirm_by_button(chapter, state).kind == "nothing_to_confirm"
    # The refusal is not a permanent ban: a later explicit offer is allowed.
    assert say(chapter, state, "I can now send it by Saturday at noon.").kind == "restate"


@pytest.mark.parametrize("pending", [False, True])
def test_invalid_replacement_does_not_leave_an_old_confirmable_plan(chapter, pending):
    state, previous = offer(chapter, pending=pending)
    decision = say(chapter, state, "Actually, I can send it next Wednesday.", previous)
    assert decision.kind == "constraint_change"
    assert state["pending"] is None and state["proposal"] is None
    assert say(chapter, state, "What can I do?").kind == "none"
    assert confirm_by_button(chapter, state).kind == "nothing_to_confirm"


@pytest.mark.parametrize("pending", [False, True])
def test_incomplete_replacement_cannot_confirm_the_old_full_plan(chapter, pending):
    state, previous = offer(chapter, pending=pending)
    fact = CandidateFact(field="submission_plan", value="staged", quote="split the work",
                         source="player", polarity="affirm", mode="assert")
    decision = evaluate_turn(chapter, state, [fact], player_text="I'd like to split the work.",
                             npc_previous=previous)
    assert decision.kind == "incomplete"
    assert state["pending"] is None and state["proposal"] is None
    assert confirm_by_button(chapter, state).kind == "nothing_to_confirm"
    assert say(chapter, state, "What can I do?").kind == "none"


def test_refusal_and_new_partial_offer_does_not_inherit_rejected_date(chapter):
    state, previous = offer(chapter)
    decision = say(chapter, state, "I cannot do Saturday, but I can send the outline by Friday.", previous)
    assert decision.kind == "incomplete"
    assert state["draft"]["outline_due"] == "FRI"
    assert state["draft"]["final_due"] is None
    assert confirm_by_button(chapter, state).kind == "nothing_to_confirm"


def test_refused_partial_draft_does_not_complete_a_later_plan(chapter):
    state = new_agreement_state()
    state["pushbacks"] = 1
    assert say(chapter, state, "I can send an outline by Friday.").kind == "incomplete"
    assert say(chapter, state, "I cannot send an outline by Friday.").kind == "withdrawn"
    decision = say(chapter, state, "I can send it by Sunday.")
    assert decision.kind == "restate"
    assert state["proposal"]["submission_plan"] == "full"
    assert state["proposal"]["outline_due"] is None


@pytest.mark.parametrize("use_button", [False, True])
def test_today_survives_save_reload_and_confirmation(chapter, use_button):
    state, previous = offer(chapter, text="I can send the full report today at 6 pm.")
    state = json.loads(json.dumps(state))
    assert state["proposal"]["final_due"] == "WED 18:00"
    assert state["proposal"]["_times"]["final_due"] == {"day": 2, "minute": 1080}
    public = summarize_state(state, chapter)["proposal"]
    assert set(public) == {"submission_plan", "outline_due", "final_due", "describe", "notes"}
    assert "6 pm on Wednesday" in public["describe"]
    decision = confirm_by_button(chapter, state) if use_button else say(chapter, state, "Yes.", previous)
    assert decision.kind == "confirmed"
    assert plan_from_dict(state["confirmed"], chapter).final_due.day == chapter.now.day


def test_legacy_today_save_is_read_without_a_week_jump(chapter):
    state, _ = offer(chapter)
    state["proposal"] = {"submission_plan": "full", "outline_due": None, "final_due": "WED 18:00"}
    assert confirm_by_button(chapter, state).kind == "confirmed"
    assert state["confirmed"]["_times"]["final_due"]["day"] == 2


def test_today_in_partial_draft_survives_completion(chapter):
    state = new_agreement_state()
    state["pushbacks"] = 1
    assert say(chapter, state, "I can send the outline today at 6 pm.").kind == "incomplete"
    state = json.loads(json.dumps(state))
    assert say(chapter, state, "I can send the report by Friday.").kind == "restate"
    assert confirm_by_button(chapter, state).kind == "confirmed"
    assert plan_from_dict(state["confirmed"], chapter).outline_due.day == 2


def test_saved_time_metadata_cannot_disagree_with_displayed_deadline(chapter):
    state, _ = offer(chapter)
    state["proposal"]["final_due"] = "NEXT WED 18:00"
    assert confirm_by_button(chapter, state).kind == "confirm_rejected_by_rules"
    assert state["pending"] is None and state["draft"]["final_due"] is None
