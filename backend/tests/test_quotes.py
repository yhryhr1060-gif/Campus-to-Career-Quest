"""Quotes from the model only count when they are really in the learner's text."""
from app.llm import mock
from app.rules.quotes import quote_in
from app.services import prepare, session

DRAFT = ("Dear Prof. Chen,\n\nI want to ask for more time for the project report. I has been sick for three days, "
         "so I can't finish it. Can I give it to you next Wednesday? Sorry.\n\nThanks,\nAlex")


def test_quote_matching():
    assert quote_in("I has been sick", DRAFT)
    assert quote_in("i HAS   been sick", DRAFT)                 # case and spacing only
    assert quote_in("can’t finish it", DRAFT)                 # curly apostrophe
    assert not quote_in("I have been sick", DRAFT)
    assert not quote_in("", DRAFT)


def _to_prepare():
    sid, _ = session.create()
    return sid


def test_feedback_items_follow_the_design_example():
    sid = _to_prepare()
    out = prepare.get_feedback(sid, DRAFT)
    # Whole-draft check: serious grammar first, missing task information early, optional style last.
    assert [(i["type"], i["layer"]) for i in out["items"]] == [("grammar", 1), ("task_info", 1), ("politeness", 3)]
    assert out["items"][0]["quote"] == "I has been"


def test_fabricated_feedback_quote_is_dropped(monkeypatch):
    sid = _to_prepare()
    monkeypatch.setattr(mock, "mock_feedback", lambda d: {"items": [
        {"type": "grammar", "judgement": "clear_error", "quote": "I has been ill since Monday", "suggestion": "x", "reason": "y"},
        {"type": "politeness", "judgement": "optional", "quote": "I want to ask for more time",
         "suggestion": "Would it be possible to …?", "reason": "polite"},
    ]})
    out = prepare.get_feedback(sid, DRAFT)
    assert [i["quote"] for i in out["items"]] == ["I want to ask for more time"]


def test_fabricated_summary_quote_is_dropped(monkeypatch):
    sid = _to_prepare()
    monkeypatch.setattr(mock, "mock_summary", lambda e: {"fields": [
        {"field": "situation", "value": "ill", "quote": "I has been sick for three days"},
        {"field": "proposal", "value": "next Wednesday", "quote": "hand it in next Wednesday at 9 am"},
    ]})
    out = prepare.send_email(sid, DRAFT)
    assert [f["field"] for f in out["prep"]["summary"]] == ["situation"]


def test_summary_does_not_fill_in_what_was_not_said():
    sid = _to_prepare()
    email = "Dear Prof. Chen, I have been ill. Would it be possible to hand in the report next week? Thanks, Alex"
    out = prepare.send_email(sid, email)
    by = {f["field"]: f["quote"] for f in out["prep"]["summary"]}
    assert "next week" in by["proposal"]
    assert all(quote_in(q, email) for q in by.values())
