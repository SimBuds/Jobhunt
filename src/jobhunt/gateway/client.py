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
    num_ctx: int = 16384,
    timeout_s: float = 180.0,
    keep_alive: str | int = -1,
) -> dict[str, Any]:
    """Send a chat completion to Ollama and return the parsed JSON object.

    `base_url` may end with `/v1` (OpenAI-compatible) or be a bare host. We hit the
    native /api/chat endpoint either way for the format-as-schema feature.

    `num_ctx` defaults to 16384 to match the user's `OLLAMA_CONTEXT_LENGTH=16384`
    server setting. Pair with a roomier `MAX_DESC_CHARS`/`MAX_POLICY_CHARS` so the
    JD and policy aren't truncated for the score/tailor/cover slots.

    `keep_alive` defaults to `-1` (load forever) so the hot model stays resident
    across scan/apply runs without paying a 5-15 s reload. This matches the
    server-side `OLLAMA_KEEP_ALIVE=-1`; the per-call value is what Ollama uses,
    so making it explicit here keeps behavior consistent regardless of env.
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
        "think": False,
        "keep_alive": keep_alive,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    async def _post(p: dict[str, Any]) -> str:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
                r = await client.post(url, json=p)
        except httpx.HTTPError as e:
            raise GatewayError(
                f"ollama request failed (model={model}, {type(e).__name__}): {e}"
            ) from e
        if r.status_code >= 400:
            raise GatewayError(f"ollama {r.status_code} (model={model}): {r.text[:300]}")
        body = r.json()
        content = (body.get("message") or {}).get("content")
        if not content:
            raise GatewayError(f"ollama returned no content: {body!r}")
        return content

    content = await _post(payload)
    try:
        parsed: Any = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        # qwen3.5:9b occasionally ignores `format=schema` and emits markdown
        # or a JSON array. Retry once with an explicit reinforcement.
        reinforcement = (
            "\n\nREMINDER: Respond with a single JSON object matching the "
            "provided schema. Do NOT output markdown, prose, or code fences. "
            "Begin your response with `{`."
        )
        retry_payload = {
            **payload,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user + reinforcement},
            ],
        }
        content = await _post(retry_payload)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise GatewayError(
                f"ollama returned invalid JSON: {e} — {content[:200]}"
            ) from e
        if not isinstance(parsed, dict):
            raise GatewayError(f"expected object, got {type(parsed).__name__}")
    return parsed
