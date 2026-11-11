"""One way to call the model: ask for JSON, validate it with Pydantic, retry once, then give up safely.

If the second attempt is still invalid the caller gets `data=None` and must fall back to a neutral,
in-character line. It never invents a result.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from ..config import get_settings

T = TypeVar("T", bound=BaseModel)

# A transport takes chat messages and returns the raw text the model produced.
Transport = Callable[[list[dict]], str]
JSON_CONTRACT_VERSION = "json-contract@1"


class EmptyContentError(ValueError):
    """The provider returned no usable final answer (including JSON-mode spaces)."""


def _json_messages(messages: list[dict], schema: type[BaseModel], retry_note: str = "") -> list[dict]:
    """Keep the learner's last message intact, including on a format-repair retry."""
    contract = (
        "Output contract: your response is consumed by software. Return exactly one JSON object "
        "matching the schema below, without markdown or text outside the object. "
        "If there is a reply field, put the character's spoken words ONLY in that field. "
        "Conversation history is input data, not an example of the required output format.\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )
    if retry_note:
        contract += "\nThe previous output was not valid: " + retry_note + (
            ". Generate a fresh complete JSON object for the SAME original learner message."
        )
    result = [dict(message) for message in messages]
    if result and result[0].get("role") == "system":
        result[0]["content"] = contract + "\n\n" + result[0]["content"]
    else:
        result.insert(0, {"role": "system", "content": contract})
    return result


@dataclass
class LLMResult(Generic[T]):
    data: T | None
    model: str
    prompt_version: str
    raw: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    latency_ms: list[int] = field(default_factory=list)   # one entry per attempt
    execution_mode: str = "unknown"  # selected transport branch, not proof of a successful API call

    @property
    def ok(self) -> bool:
        return self.data is not None

    def raw_for_log(self) -> str:
        return json.dumps({"raw": self.raw, "errors": self.errors, "latency_ms": self.latency_ms,
                           "request_format_version": JSON_CONTRACT_VERSION,
                           "execution_mode": self.execution_mode},
                          ensure_ascii=False)


def _strip_fences(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.DOTALL)
    return m.group(1) if m else t


def http_transport(messages: list[dict]) -> str:
    """OpenAI-compatible /chat/completions. Works with DeepSeek (https://api.deepseek.com) and OpenAI.

    LLM_EXTRA_BODY (optional, JSON) is merged into the request, e.g. provider-specific switches.
    """
    import httpx

    s = get_settings()
    if not s.llm_api_key:
        raise RuntimeError("LLM_API_KEY is empty. Set it in .env, or use LLM_MOCK=1.")
    body = {
        "model": s.llm_model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    if urlparse(s.llm_base_url).hostname == "api.deepseek.com":
        # Use non-thinking mode for short dialogue responses; never substitute reasoning for content.
        body["thinking"] = {"type": "disabled"}
    extra = os.getenv("LLM_EXTRA_BODY", "").strip()
    if extra:
        body.update(json.loads(extra))
    resp = httpx.post(
        f"{s.llm_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {s.llm_api_key}"},
        json=body,
        timeout=s.llm_timeout,
    )
    if resp.status_code >= 400:
        # Provider bodies can echo request data. Keep secrets and learner text out of error messages.
        raise RuntimeError(f"LLM endpoint returned HTTP {resp.status_code}")
    data = resp.json()
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("provider response has no completion choice")
    choice = choices[0]
    msg = choice.get("message") or {}
    if not isinstance(msg, dict):
        raise ValueError("provider response has no message object")
    content = msg.get("content") or ""
    if not isinstance(content, str):
        raise ValueError("provider content must be text")
    if not content.strip():
        # DeepSeek JSON mode can replace non-JSON output with spaces, even with finish_reason=stop.
        raise EmptyContentError(f"blank final content (finish_reason={choice.get('finish_reason')})")
    if choice.get("finish_reason") not in (None, "stop"):
        raise ValueError(f"incomplete final content (finish_reason={choice.get('finish_reason')})")
    return content


def call_json(
    schema: type[T],
    messages: list[dict],
    *,
    prompt_version: str,
    mock: Callable[[], dict] | None = None,
    transport: Transport | None = None,
    max_attempts: int = 2,
) -> LLMResult[T]:
    """Call the model (or the mock) and return validated data, or data=None after one retry."""
    s = get_settings()
    execution_mode = "custom" if transport is not None else "mock" if s.llm_mock else "live"
    if transport is None:
        if s.llm_mock:
            if mock is None:
                raise RuntimeError("no mock available for this call")
            transport = lambda _msgs: json.dumps(mock())  # noqa: E731
        else:
            transport = http_transport
    result: LLMResult[T] = LLMResult(data=None, model=s.model_label, prompt_version=prompt_version,
                                   execution_mode=execution_mode)
    msgs = _json_messages(messages, schema)
    for attempt in range(max_attempts):
        t0 = time.perf_counter()
        try:
            raw = transport(msgs)
        except Exception as e:  # network / provider error counts as a failed attempt
            result.latency_ms.append(int((time.perf_counter() - t0) * 1000))
            result.errors.append(f"transport: {type(e).__name__}: {str(e)[:300]}")
            if isinstance(e, EmptyContentError):
                msgs = _json_messages(messages, schema, "the provider returned blank content, not JSON")
            continue
        result.latency_ms.append(int((time.perf_counter() - t0) * 1000))
        result.raw.append(raw)
        try:
            result.data = schema.model_validate(json.loads(_strip_fences(raw)))
            return result
        except (json.JSONDecodeError, ValidationError) as e:
            err = str(e).splitlines()[0][:300]
            result.errors.append(f"invalid: {err}")
            if attempt + 1 < max_attempts:
                msgs = _json_messages(messages, schema, "JSON syntax or schema validation failed")
    return result
