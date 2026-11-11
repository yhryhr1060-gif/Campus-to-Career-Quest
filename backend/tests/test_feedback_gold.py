"""The expected-answer cases are well formed and the scorer compares against them correctly.

These tests do NOT measure feedback quality: that needs a real model (tools/feedback_gold_check.py
--live) and human-reviewed expectations. Here the "model output" is written by hand, right and wrong.
"""
from app.rules.feedback_guard import meaning_flags
from app.rules.quotes import quote_in
from tools.feedback_gold_check import load_cases, score_case

CASES = {c["id"]: c for c in load_cases()}


def shown(**kw):
    return {"type": "grammar", "judgement": "clear_error", "quote": "", "suggestion": "", "reason": "r"} | kw


def test_every_expected_span_really_is_in_its_email():
    assert len(CASES) >= 8
    for case in CASES.values():
        for expected in case["must_flag"]:
            assert quote_in(expected["words"], case["email"]), (case["id"], expected["words"])
            assert expected["types"]
        for acceptable in case["must_not_error"]:
            assert quote_in(acceptable, case["email"]), (case["id"], acceptable)


def test_scorer_passes_a_correct_answer_and_fails_a_miss():
    case = CASES["agreement-and-tense"]
    good = [shown(quote="I has been sick"), shown(quote="didn't went")]
    assert score_case(case, good)["passed"]
    assert score_case(case, good[:1])["missed"] == ["didn't went"]
    assert score_case(case, [])["passed"] is False


def test_scorer_fails_when_acceptable_wording_is_called_an_error_but_allows_optional_advice():
    case = CASES["direct-but-correct"]
    assert score_case(case, [shown(quote="I need more time")])["false_errors"] == ["I need more time for the report."]
    polite = shown(type="politeness", judgement="optional", quote="I need more time for the report")
    assert score_case(case, [polite])["passed"]


def test_scorer_does_not_count_an_optional_or_wrong_type_point_as_finding_the_error():
    case = CASES["since-for"]
    assert not score_case(case, [shown(type="politeness", judgement="optional", quote="since three days")])["passed"]
    assert not score_case(case, [shown(type="logic", judgement="expression_issue", quote="since three days")])["passed"]
    assert score_case(case, [shown(type="grammar", judgement="uncertain", quote="sick since three days")])["passed"]


def test_no_gold_case_claims_human_review_that_has_not_happened():
    assert all(score_case(c, [])["human_reviewed"] is False for c in CASES.values())


def test_rule_guard_protects_the_negation_case_from_a_meaning_changing_fix():
    email = CASES["negation-must-survive"]["email"]
    assert "changes_negation" in meaning_flags("I can't finish the report by Friday", "I can finish the report by Friday",
                                               allowed_context=email)
    assert "changes_fact" in meaning_flags("Could I have until Sunday?", "Could I have until Monday?", allowed_context=email)
