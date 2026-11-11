"""Rule engine. These are the guarantees the game depends on: the model proposes, the server decides."""
import pytest

from app.llm.mock import mock_npc_turn
from app.llm.schemas import CandidateFact, NpcTurn
from app.rules.engine import check_plan, confirm_by_button, evaluate_turn, new_agreement_state, plan_from_dict


def fact(field, value, quote, polarity="affirm", mode="assert", source="player"):
    return CandidateFact(field=field, value=value, quote=quote, polarity=polarity, mode=mode, source=source)


YES = fact("confirm", None, "Yes")


def pushed_back_state():
    """State after Prof. Chen has already pushed back once (so a legal plan goes straight to restatement)."""
    s = new_agreement_state()
    s["pushbacks"] = 1
    return s


def say(chapter, state, text, npc_previous=""):
    """Run one learner turn through the mock model and the rule engine."""
    turn = NpcTurn.model_validate(mock_npc_turn(text, npc_previous))
    return evaluate_turn(chapter, state, turn.candidate_facts, player_text=text, npc_previous=npc_previous)


# ------------------------------------------------------------------ both legal endings

def test_full_ending_can_be_confirmed(chapter):
    s = pushed_back_state()
    d = evaluate_turn(chapter, s, [fact("final_due", "SUN 18:00", "6 pm on Sunday")],
                      player_text="I could hand in the whole report by 6 pm on Sunday.")
    assert d.kind == "restate" and s["status"] == "proposed"
    assert "6 pm on Sunday" in d.reply_override
    d = evaluate_turn(chapter, s, [YES], player_text="Yes, that's right.")
    assert d.kind == "confirmed" and d.became_confirmed
    assert plan_from_dict(s["confirmed"], chapter).to_dict() == {
        "submission_plan": "full", "outline_due": None, "final_due": "SUN 18:00"}


def test_staged_ending_can_be_confirmed(chapter):
    s = pushed_back_state()
    text = "I could send you the outline by Friday and the full report by Sunday evening."
    d = evaluate_turn(chapter, s, [fact("outline_due", "FRI", "Friday"), fact("final_due", "SUN", "Sunday")],
                      player_text=text)
    assert d.kind == "restate"
    # days without a clock time are restated with concrete times, and must still be confirmed
    assert d.reply_override == ("So that's the outline by 6 pm on Friday, and the full report by 6 pm on Sunday"
                                " — can you confirm that?")
    assert s["status"] == "proposed"
    d = evaluate_turn(chapter, s, [YES], player_text="Yes. I'll make sure they're on time.")
    assert d.kind == "confirmed"
    assert plan_from_dict(s["confirmed"], chapter).to_dict() == {
        "submission_plan": "staged", "outline_due": "FRI 18:00", "final_due": "SUN 18:00"}


def test_limits_are_upper_bounds(chapter):
    s = pushed_back_state()
    d = evaluate_turn(chapter, s, [fact("final_due", "SAT 12:00", "Saturday at noon")],
                      player_text="Could I send it by Saturday at noon?")
    assert d.kind == "restate"
    assert "noon on Saturday" in d.reply_override
    assert evaluate_turn(chapter, s, [YES], player_text="Yes.").kind == "confirmed"
    assert s["confirmed"]["final_due"] == "SAT 12:00"


# ------------------------------------------------------------------ limits

def test_after_sunday_1800_is_refused_and_fires_constraint_once(chapter):
    s = new_agreement_state()
    d = evaluate_turn(chapter, s, [fact("final_due", "NEXT WED", "next Wednesday", mode="question")],
                      player_text="Can I give it to you next Wednesday?")
    assert d.kind == "constraint_change" and d.constraint_fired_now
    assert "conference" in d.reply_override
    assert s["status"] == "none" and s["constraint_fired"]
    d = evaluate_turn(chapter, s, [fact("final_due", "MON 09:00", "Monday at 9 am")],
                      player_text="What about Monday at 9 am?")
    assert d.kind == "rejected" and not d.constraint_fired_now
    assert s["status"] == "none"


@pytest.mark.parametrize("code,quote", [("SUN 20:00", "8 pm on Sunday"), ("SUN 18:01", "18:01 on Sunday"),
                                        ("NEXT MON", "next week")])
def test_anything_after_the_limit_never_becomes_a_proposal(chapter, code, quote):
    s = pushed_back_state()
    d = evaluate_turn(chapter, s, [fact("final_due", code, quote)], player_text=f"I could do it by {quote}.")
    assert d.kind in ("constraint_change", "rejected")
    assert s["status"] == "none" and s["proposal"] is None


def test_outline_after_friday_is_refused(chapter):
    s = pushed_back_state()
    d = evaluate_turn(chapter, s, [fact("outline_due", "SAT", "Saturday"), fact("final_due", "SUN", "Sunday")],
                      player_text="The outline on Saturday and the report on Sunday.")
    assert d.kind == "rejected" and s["status"] == "none"


def test_time_already_passed_is_refused(chapter):
    s = pushed_back_state()
    d = evaluate_turn(chapter, s, [fact("final_due", "TODAY 13:00", "today at 1 pm")],
                      player_text="I could send it today at 1 pm.")
    assert d.kind == "rejected"


# ------------------------------------------------------------------ negation, conditions, vague times

def test_negation_never_becomes_an_agreement(chapter):
    s = pushed_back_state()
    d = say(chapter, s, "I can't submit on Sunday.")
    assert s["status"] == "none" and s["proposal"] is None
    assert "final_due:SUN" in s["excluded"]
    d = say(chapter, s, "I don't think I can do Friday.")
    assert s["status"] == "none"
    assert d.kind == "none"


def test_no_later_than_is_not_a_refusal(chapter):
    s = pushed_back_state()
    d = say(chapter, s, "I can submit it no later than Sunday.")
    assert d.kind == "restate" and s["status"] == "proposed"


def test_conditional_offer_is_a_proposal_not_an_agreement(chapter):
    s = pushed_back_state()
    d = say(chapter, s, "If I send the outline on Friday, could I finish on Sunday?")
    assert d.kind == "restate"
    assert s["status"] == "proposed" and s["confirmed"] is None


def test_vague_question_is_restated_and_waits(chapter):
    s = pushed_back_state()
    d = say(chapter, s, "Maybe Sunday?")
    assert d.kind == "restate" and "6 pm on Sunday" in d.reply_override
    assert s["status"] == "proposed" and s["confirmed"] is None


def test_quoted_speech_is_ignored(chapter):
    s = pushed_back_state()
    d = say(chapter, s, "My friend said Sunday is OK.")
    assert s["status"] == "none"
    assert any(x["reason"] == "quoted_speech" for x in d.dropped)


def test_hedged_or_conditional_yes_does_not_confirm(chapter):
    for text in ("Yes, maybe.", "Yes, if I'm feeling better.", "OK, I think so?"):
        s = pushed_back_state()
        evaluate_turn(chapter, s, [fact("final_due", "SUN 18:00", "6 pm on Sunday")], player_text="By 6 pm on Sunday.")
        assert s["status"] == "proposed"
        d = say(chapter, s, text, npc_previous="So that's the full report by 6 pm on Sunday — can you confirm that?")
        assert d.kind == "confirm_unclear", text
        assert s["status"] == "proposed"


def test_yes_but_refusal_withdraws_the_proposal(chapter):
    s = pushed_back_state()
    say(chapter, s, "I could send the outline by Friday and the full report by Sunday.")
    assert s["status"] == "proposed"
    d = say(chapter, s, "No, I can't do Friday.")
    assert d.kind == "withdrawn" and s["status"] == "none"


# ------------------------------------------------------------------ confirmation

def test_no_confirmed_without_the_learner_confirming(chapter):
    s = pushed_back_state()
    evaluate_turn(chapter, s, [fact("final_due", "SUN 18:00", "6 pm on Sunday")], player_text="6 pm on Sunday.")
    assert s["status"] == "proposed"
    # repeating the plan, asking about it, or chatting is not a confirmation
    for text, facts in [("6 pm on Sunday.", [fact("final_due", "SUN 18:00", "6 pm on Sunday")]),
                        ("Is 6 pm on Sunday okay?", [fact("final_due", "SUN 18:00", "6 pm on Sunday", mode="question")]),
                        ("I've done about half.", [])]:
        evaluate_turn(chapter, s, facts, player_text=text)
        assert s["status"] == "proposed" and s["confirmed"] is None


def test_yes_without_a_proposal_does_nothing(chapter):
    s = pushed_back_state()
    d = evaluate_turn(chapter, s, [YES], player_text="Yes.")
    assert d.kind == "none" and s["status"] == "none"


def test_first_legal_plan_gets_one_follow_up_first(chapter):
    s = new_agreement_state()
    d = say(chapter, s, "I could send the whole report by Saturday.")
    assert d.kind == "follow_up" and s["status"] == "none"
    d = say(chapter, s, "I've finished the code, but the write-up is about half done.")
    assert d.kind == "restate" and s["status"] == "proposed"
    assert s["proposal"]["final_due"] == "SAT 18:00"


def test_changed_plan_replaces_old_proposal(chapter):
    s = pushed_back_state()
    say(chapter, s, "I could send the outline by Friday and the full report by Sunday.")
    old = dict(s["proposal"])
    d = say(chapter, s, "Actually, I could send the whole report by Saturday.")
    assert d.kind == "restate" and s["proposal"] != old
    assert plan_from_dict(s["proposal"], chapter).to_dict() == {
        "submission_plan": "full", "outline_due": None, "final_due": "SAT 18:00"}
    say(chapter, s, "Yes.", npc_previous=d.reply_override)
    assert s["confirmed"]["final_due"] == "SAT 18:00"


def test_yes_to_a_time_prof_chen_asked_about(chapter):
    s = pushed_back_state()
    d = say(chapter, s, "Yes, that works.", npc_previous="Would Saturday at 6 p.m. work?")
    assert d.kind == "restate"            # still restated and confirmed, never agreed in one step
    assert s["proposal"]["final_due"] == "SAT 18:00"


def test_fabricated_quote_is_dropped(chapter):
    s = pushed_back_state()
    d = evaluate_turn(chapter, s, [fact("final_due", "SUN 18:00", "by Sunday at six")],
                      player_text="I need a bit more time, please.")
    assert d.kind == "none" and s["status"] == "none"
    assert d.dropped[0]["reason"] == "quote_not_found"


def test_confirm_button_requires_a_proposal_and_rechecks(chapter):
    s = pushed_back_state()
    assert confirm_by_button(chapter, s).kind == "nothing_to_confirm"
    evaluate_turn(chapter, s, [fact("final_due", "SUN 18:00", "6 pm on Sunday")], player_text="6 pm on Sunday.")
    s["proposal"]["final_due"] = "NEXT WED 18:00"     # tampered state must not get through
    d = confirm_by_button(chapter, s)
    assert d.kind == "confirm_rejected_by_rules" and s["status"] == "none"


def test_check_plan_both_endings(chapter):
    assert check_plan(chapter, plan_from_dict({"submission_plan": "full", "outline_due": None,
                                               "final_due": "SUN 18:00"}, chapter)) is None
    assert check_plan(chapter, plan_from_dict({"submission_plan": "staged", "outline_due": "FRI 18:00",
                                               "final_due": "SUN 18:00"}, chapter)) is None
    assert check_plan(chapter, plan_from_dict({"submission_plan": "staged", "outline_due": "SUN 17:00",
                                               "final_due": "SUN 18:00"}, chapter)) == "outline_late"
