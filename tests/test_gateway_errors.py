"""Gateway request shape and error reporting against llama-server.

Every failure path must produce a non-empty str(exception) so the user-facing
CLI lines aren't blank. Regression: a `scan` run once printed `! adzuna_ca:…:
request failed: ` with nothing after the colon because httpx.ReadTimeout()
formats to empty. Now we always include the exception class name and the model.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jobhunt.errors import GatewayError
from jobhunt.gateway.client import complete_json

_ORIG_ASYNC_CLIENT = httpx.AsyncClient
_BASE_URL = "http://localhost:8080/v1"
_MODEL = "lite"


def _client(handler):
    return _ORIG_ASYNC_CLIENT(transport=httpx.MockTransport(handler))


def _ok(content: str = '{"ok": true}', finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": content},
                }
            ]
        },
    )


async def _call(**kw: Any) -> dict[str, Any]:
    return await complete_json(
        base_url=kw.pop("base_url", _BASE_URL),
        model=_MODEL,
        system="s",
        user="u",
        schema=kw.pop("schema", {"type": "object"}),
        **kw,
    )


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record the last request's URL and JSON body."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _ok()

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    return captured


# --- error reporting -----------------------------------------------------------


@pytest.mark.asyncio
async def test_read_timeout_message_is_non_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")  # default str() is empty

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    with pytest.raises(GatewayError) as exc:
        await _call()
    msg = str(exc.value)
    assert msg
    assert _MODEL in msg
    assert "ReadTimeout" in msg


@pytest.mark.asyncio
async def test_connect_error_includes_class_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("")

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    with pytest.raises(GatewayError) as exc:
        await _call()
    msg = str(exc.value)
    assert _MODEL in msg
    assert "ConnectError" in msg


@pytest.mark.asyncio
async def test_http_error_status_includes_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Loading model")

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    with pytest.raises(GatewayError) as exc:
        await _call()
    msg = str(exc.value)
    assert "503" in msg
    assert _MODEL in msg


@pytest.mark.asyncio
async def test_context_overflow_400_names_the_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The router rejects an oversized prompt loudly. The body shape below is
    captured from a live llama-server 400; the type must reach the message so
    an overflow is recognisable in scan output rather than a bare 400."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "message": (
                        "request (45013 tokens) exceeds the available context "
                        "size (32768 tokens), try increasing it"
                    ),
                    "type": "exceed_context_size_error",
                    "n_prompt_tokens": 45013,
                    "n_ctx": 32768,
                }
            },
        )

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    with pytest.raises(GatewayError) as exc:
        await _call()
    msg = str(exc.value)
    assert "400" in msg
    assert "exceed_context_size_error" in msg
    assert "45013 tokens" in msg


@pytest.mark.asyncio
async def test_length_finish_fails_fast_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A generation cut off at max_tokens is the in-band reasoning runaway. The
    reinforcement retry cannot fix it, so exactly one request is made."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _ok('{\n  "reasons": ["and then', finish_reason="length")

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    with pytest.raises(GatewayError, match="max_tokens"):
        await _call()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_non_object_output_retries_once_with_reinforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _ok("[1, 2]") if len(bodies) == 1 else _ok('{"ok": true}')

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    assert await _call() == {"ok": True}
    assert len(bodies) == 2
    assert "REMINDER" in bodies[1]["messages"][-1]["content"]


# --- request shape -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["http://localhost:8080/v1", "http://localhost:8080/v1/",
                                      "http://localhost:8080"])
async def test_posts_openai_chat_completions(
    monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
    captured = _capture(monkeypatch)
    await _call(base_url=base_url)
    assert captured["url"] == "http://localhost:8080/v1/chat/completions"


@pytest.mark.asyncio
async def test_schema_is_nested_inside_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """llama-server silently IGNORES a schema placed at the top level of
    `response_format` (verified live: it returns unconstrained JSON, no error).
    The schema must sit at response_format.json_schema.schema."""
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    captured = _capture(monkeypatch)
    await _call(schema=schema)
    rf = captured["body"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == schema
    assert rf["json_schema"]["name"]
    assert "schema" not in rf
    # No Ollama-native structured-output key.
    assert "format" not in captured["body"]


@pytest.mark.asyncio
async def test_thinking_disabled_via_chat_template_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture(monkeypatch)
    await _call()
    body = captured["body"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "think" not in body
    assert body["stream"] is False


@pytest.mark.asyncio
async def test_no_ollama_residency_or_context_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The router owns context (32K) and residency (600s idle unload). Sending
    Ollama's keys would be dead weight at best."""
    captured = _capture(monkeypatch)
    await _call()
    body = captured["body"]
    for key in ("keep_alive", "num_ctx", "num_predict", "options"):
        assert key not in body


@pytest.mark.asyncio
async def test_payload_pins_default_sampler(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app owns its sampler. Each key is sent explicitly because an omitted
    key falls back to the router preset (lite: top-k 40, min-p 0.05,
    repeat-penalty 1.05), which would silently govern structured output."""
    captured = _capture(monkeypatch)
    await _call()
    body = captured["body"]
    # max_tokens bounds in-band reasoning runaways; 4096 > the largest legit
    # output (tailor ~2.2k tokens) so it never truncates real work.
    assert body["max_tokens"] == 4096
    assert body["presence_penalty"] == 0
    assert body["repeat_penalty"] == 1.0
    assert body["top_p"] == 0.95
    assert body["top_k"] == 20
    assert body["min_p"] == 0
    assert body["temperature"] == 0.0


@pytest.mark.asyncio
async def test_options_override_and_temperature_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-call options override the defaults; the temperature kwarg beats both."""
    captured = _capture(monkeypatch)
    await _call(temperature=0.7, options={"presence_penalty": 0.3, "temperature": 0.1})
    body = captured["body"]
    assert body["presence_penalty"] == 0.3  # per-call override beat default
    assert body["temperature"] == 0.7  # kwarg beat the value inside options
