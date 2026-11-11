"""Invalid model output: retry once, then fall back to a neutral line — never invent a result."""
import json

from app.llm import client
from app.llm.client import call_json
from app.llm.schemas import NpcTurn
from app.services import negotiate, prepare, session

VALID = json.dumps({"reply": "I see.", "mood": "neutral", "candidate_facts": []})


def scripted(*outputs):
    calls = []

    def transport(msgs):
        calls.append(msgs)
        out = outputs[len(calls) - 1]
        if isinstance(out, Exception):
            raise out
        return out
    return transport, calls


def test_valid_first_time():
    t, calls = scripted(VALID)
    r = call_json(NpcTurn, [{"role": "user", "content": "hi"}], prompt_version="t", transport=t)
    assert r.ok and len(calls) == 1


def test_invalid_then_valid_retries_once():
    t, calls = scripted("not json at all", VALID)
    r = call_json(NpcTurn, [{"role": "user", "content": "hi"}], prompt_version="t", transport=t)
    assert r.ok and len(calls) == 2
    assert "not valid" in calls[1][0]["content"]
    assert calls[1][-1] == {"role": "user", "content": "hi"}  # preserve the learner, not a repair instruction


def test_code_fences_are_tolerated():
    t, _ = scripted("```json\n" + VALID + "\n```")
    assert call_json(NpcTurn, [], prompt_version="t", transport=t).ok


def test_invalid_twice_gives_up():
    bad_schema = json.dumps({"reply": "", "mood": "furious"})
    t, calls = scripted("{oops", bad_schema)
    r = call_json(NpcTurn, [], prompt_version="t", transport=t)
    assert not r.ok and r.data is None and len(calls) == 2 and len(r.errors) == 2


def test_transport_error_counts_as_attempt():
    t, calls = scripted(TimeoutError("slow"), VALID)
    assert call_json(NpcTurn, [], prompt_version="t", transport=t).ok


def _ready_to_negotiate():
    sid, _ = session.create()
    prepare.send_email(sid, "Dear Prof. Chen, I have been ill for three days. "
                            "Would it be possible to hand in the report next Wednesday? Thanks, Alex")
    prepare.confirm_summary(sid)
    prepare.submit_prediction(sid, "He will say no.", "Send part of it by Friday.")
    negotiate.start(sid)
    return sid


def test_npc_falls_back_to_neutral_line_and_changes_nothing(monkeypatch):
    sid = _ready_to_negotiate()
    monkeypatch.setenv("LLM_MOCK", "0")
    # The "model" claims an agreement in broken JSON, twice.
    monkeypatch.setattr(client, "http_transport",
                        lambda msgs: '{"reply": "Fine, Sunday it is!", "candidate_facts": [{"field": "confirm"')
    out = negotiate.turn(sid, "Yes, I could do it by 6 pm on Sunday.", request_id="req-000001")
    assert out["reply"] == "Sorry — could you say that again?"
    assert out["decision"] == "model_failed"
    assert out["negotiation"]["agreement"]["status"] == "none"
