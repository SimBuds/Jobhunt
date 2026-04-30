"""Ollama gateway. Uses /api/chat with `format` for JSON-schema-constrained output."""

from __future__ import annotations

import json
from typing import Any

import httpx

from jobhunt.errors import GatewayError


async def complete_json(
    *,
    base_url: str,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    temperature: float = 0.0,
    num_ctx: int = 8192,
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    """Send a chat completion to Ollama and return the parsed JSON object.

    `base_url` may end with `/v1` (OpenAI-compatible) or be a bare host. We hit the
    native /api/chat endpoint either way for the format-as-schema feature.
    """
    host = base_url.rstrip("/")
    if host.endswith("/v1"):
        host = host[: -len("/v1")]
    url = f"{host}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": schema,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            r = await client.post(url, json=payload)
    except httpx.HTTPError as e:
        raise GatewayError(f"ollama request failed: {e}") from e
    if r.status_code >= 400:
        raise GatewayError(f"ollama {r.status_code}: {r.text[:300]}")
    body = r.json()
    content = (body.get("message") or {}).get("content")
    if not content:
        raise GatewayError(f"ollama returned no content: {body!r}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise GatewayError(f"ollama returned invalid JSON: {e} — {content[:200]}") from e
    if not isinstance(parsed, dict):
        raise GatewayError(f"expected object, got {type(parsed).__name__}")
    return parsed
