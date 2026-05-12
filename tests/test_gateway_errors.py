"""Gateway error reporting — every failure path must produce a non-empty
str(exception) so the user-facing CLI lines aren't blank.

Regression: a `scan` run printed `! adzuna_ca:5637861726: ollama request
failed: ` with nothing after the colon because httpx.ReadTimeout() formats
to empty. Now we always include the exception class name and the model.
"""

from __future__ import annotations

import httpx
import pytest

from jobhunt.errors import GatewayError
from jobhunt.gateway.client import complete_json


_ORIG_ASYNC_CLIENT = httpx.AsyncClient


def _client(handler):
    return _ORIG_ASYNC_CLIENT(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_read_timeout_message_is_non_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")  # default str() is empty

    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *a, **kw: _client(handler),
    )
    with pytest.raises(GatewayError) as exc:
        await complete_json(
            base_url="http://localhost:11434",
            model="qwen3.5:9b",
            system="s",
            user="u",
            schema={"type": "object"},
        )
    msg = str(exc.value)
    assert msg
    assert "qwen3.5:9b" in msg
    assert "ReadTimeout" in msg


@pytest.mark.asyncio
async def test_connect_error_includes_class_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("")

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    with pytest.raises(GatewayError) as exc:
        await complete_json(
            base_url="http://localhost:11434",
            model="qwen3.5:9b",
            system="s",
            user="u",
            schema={"type": "object"},
        )
    msg = str(exc.value)
    assert "qwen3.5:9b" in msg
    assert "ConnectError" in msg


@pytest.mark.asyncio
async def test_http_error_status_includes_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="model loading")

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    with pytest.raises(GatewayError) as exc:
        await complete_json(
            base_url="http://localhost:11434",
            model="qwen3.5:9b",
            system="s",
            user="u",
            schema={"type": "object"},
        )
    msg = str(exc.value)
    assert "503" in msg
    assert "qwen3.5:9b" in msg


@pytest.mark.asyncio
async def test_payload_includes_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirm the keep_alive field is sent so models stay hot across a scan."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured.update(_json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": '{"ok": true}'}},
        )

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _client(handler))
    await complete_json(
        base_url="http://localhost:11434",
        model="qwen3.5:9b",
        system="s",
        user="u",
        schema={"type": "object"},
    )
    assert captured.get("keep_alive") == -1
