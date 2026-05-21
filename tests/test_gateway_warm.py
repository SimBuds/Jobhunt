"""Phase 9 tests — shared `warm_model` helper."""
from __future__ import annotations

from typing import Any

import pytest

from jobhunt.config import Config, GatewayConfig
from jobhunt.errors import GatewayError
from jobhunt.gateway import warm as warm_mod
from jobhunt.gateway.warm import warm_model


def _cfg(model: str | None = "qwen-custom:latest") -> Config:
    tasks = {"score": model} if model else {}
    return Config(gateway=GatewayConfig(tasks=tasks))


@pytest.mark.asyncio
async def test_warm_model_no_op_when_task_unset(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the task slot isn't configured, warm_model returns immediately."""
    called = {"n": 0}

    async def fake(**_: Any) -> dict[str, Any]:
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(warm_mod, "complete_json", fake)
    await warm_model(_cfg(model=None))
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_warm_model_calls_gateway_with_configured_model(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(warm_mod, "complete_json", fake)
    await warm_model(_cfg())
    assert captured["model"] == "qwen-custom:latest"
    # Schema is the trivial single-key one.
    assert "ok" in captured["schema"]["properties"]


@pytest.mark.asyncio
async def test_warm_model_swallows_gateway_errors(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warm-up failures must not bubble up — they're non-fatal."""
    async def fake(**_: Any) -> dict[str, Any]:
        raise GatewayError("connection refused")

    monkeypatch.setattr(warm_mod, "complete_json", fake)
    # Should not raise.
    await warm_model(_cfg())


@pytest.mark.asyncio
async def test_warm_model_respects_task_argument(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing `task="tailor"` should pick up the tailor model slot."""
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(warm_mod, "complete_json", fake)
    cfg = Config(gateway=GatewayConfig(tasks={
        "score": "model-a", "tailor": "model-b",
    }))
    await warm_model(cfg, task="tailor")
    assert captured["model"] == "model-b"
