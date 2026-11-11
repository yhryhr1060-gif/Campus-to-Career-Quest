"""Exercise the real transport boundary with deterministic HTTP responses, without API calls."""
import json

import httpx
import pytest

from app.llm import client
from app.llm.schemas import NpcTurn

VALID = {"reply": "When could you finish?", "mood": "neutral", "candidate_facts": []}


def endpoint(monkeypatch, *responses):
    calls = []
    monkeypatch.setenv("LLM_MOCK", "0")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "test-key-never-send")
    monkeypatch.delenv("LLM_EXTRA_BODY", raising=False)

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        status, body = responses[len(calls) - 1]
        return httpx.Response(status, json=body)

    monkeypatch.setattr(httpx, "post", post)
    return calls


def completion(content, finish="stop", **message_fields):
    return 200, {"choices": [{"finish_reason": finish, "message": {"content": content, **message_fields}}]}


def test_blank_json_output_gets_a_repair_without_changing_player(monkeypatch):
    calls = endpoint(monkeypatch, completion("   \n "), completion(json.dumps(VALID)))
    messages = [{"role": "system", "content": "Play Prof. Chen."},
                {"role": "user", "content": "I can finish on Saturday."}]
    result = client.call_json(NpcTurn, messages, prompt_version="test")
    assert result.ok and len(calls) == 2
    assert "blank content" in calls[1]["messages"][0]["content"]
    assert calls[0]["messages"][-1] == calls[1]["messages"][-1] == messages[-1]
    assert messages[0]["content"] == "Play Prof. Chen."  # caller's history is not mutated
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert len(result.errors) == 1


def test_repeated_blanks_remain_a_failure_not_an_invented_reply(monkeypatch):
    calls = endpoint(monkeypatch, completion(" "), completion(None))
    result = client.call_json(NpcTurn, [{"role": "user", "content": "Hello"}], prompt_version="test")
    assert not result.ok and len(calls) == 2 and len(result.errors) == 2


def test_reasoning_is_never_used_as_the_final_answer(monkeypatch):
    endpoint(monkeypatch, completion(None, reasoning_content=json.dumps(VALID)))
    with pytest.raises(client.EmptyContentError):
        client.http_transport([])


@pytest.mark.parametrize("finish", ["length", "content_filter", "aborted", "insufficient_system_resource"])
def test_incomplete_responses_are_not_accepted_even_if_they_parse(monkeypatch, finish):
    endpoint(monkeypatch, completion(json.dumps(VALID), finish=finish))
    with pytest.raises(ValueError, match="incomplete final content"):
        client.http_transport([])


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [None]}, [],
                                  {"choices": [{"message": "invalid"}]},
                                  {"choices": [{"message": {"content": ["invalid"]}}]}])
def test_malformed_provider_envelope_is_a_controlled_failure(monkeypatch, body):
    endpoint(monkeypatch, (200, body))
    with pytest.raises(ValueError):
        client.http_transport([])


def test_http_error_does_not_echo_provider_body_or_credentials(monkeypatch):
    endpoint(monkeypatch, (401, {"error": "test-key-never-send private email"}))
    with pytest.raises(RuntimeError) as error:
        client.http_transport([])
    assert "401" in str(error.value)
    assert "test-key" not in str(error.value) and "private email" not in str(error.value)
