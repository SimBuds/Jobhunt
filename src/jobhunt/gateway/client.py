"""llama-server gateway. POSTs /v1/chat/completions with a `json_schema`
response_format for schema-constrained output."""

from __future__ import annotations

import json
from typing import Any

import httpx

from jobhunt.errors import GatewayError

# Sampler settings the app pins on every structured call, so behavior is defined
# in-repo rather than by the router's per-model preset
# (~/.config/llama.cpp/models.ini). Every key below was verified on 2026-09-15 to
# take effect per request on /v1/chat/completions: the slot's reported params
# (`GET /slots?model=lite`) matched the request values, and reverted to the
# preset on the next request that omitted them. A key left out here is NOT
# neutral — the preset value governs instead (lite ships top-k 40, min-p 0.05,
# repeat-penalty 1.05), so anything that matters is sent explicitly.
#
#   Context is NOT set here. The router fixes each model's window at 32768 and
#   rejects an oversized prompt with HTTP 400 `exceed_context_size_error`, so
#   overflow is loud rather than silently truncated. MAX_DESC_CHARS in
#   `pipeline.score` is still sized to fit that window — see the note there.
#
#   presence_penalty / repeat_penalty: both pinned off. Structured output has to
#   repeat tokens — JSON field names, and the verbatim JD keywords the tailor
#   must echo — so a repetition penalty works directly against it. Qwen's 1.5
#   presence penalty is a thinking/chat-mode recommendation, and the preset's
#   repeat-penalty 1.05 would otherwise apply silently.
#
#   top_p / top_k / min_p: Qwen's recommended nucleus set, unchanged from the
#   Ollama-era pins so the qwen3.5:9b -> lite move changes the model file only,
#   not the sampler. Any calibration shift is then attributable to the weights.
#
#   max_tokens: the generation ceiling. With thinking disabled the model is
#   supposed to emit only schema-constrained JSON, but on some inputs Qwen3.5-9B
#   reasons IN-BAND — it opens a JSON string (a `reasons[]` item) and pours a
#   monologue into it without closing it (measured 2026-05-31: a thin Adzuna
#   junior co-op JD ran to 8000 tokens of unterminated JSON). 4096 sits well
#   above the largest legitimate output (tailor at 700 words ≈ 2.2k tokens), so
#   it never truncates real work while bounding a runaway to ~50s.
#
# Override any of these per call via the `options` kwarg; the `temperature`
# kwarg always wins.
_DEFAULT_OPTIONS: dict[str, Any] = {
    "max_tokens": 4096,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 0.0,
    "repeat_penalty": 1.0,
}


def _chat_url(base_url: str) -> str:
    """`base_url` may end with `/v1` (the configured form) or be a bare host."""
    host = base_url.rstrip("/")
    if host.endswith("/v1"):
        host = host[: -len("/v1")]
    return f"{host}/v1/chat/completions"


def _error_detail(r: httpx.Response) -> str:
    """llama-server errors are `{"error": {"type", "message"}}`; keep the type,
    since `exceed_context_size_error` is the one worth recognising at a glance."""
    try:
        err = r.json().get("error") or {}
    except ValueError:
        return r.text[:300]
    if isinstance(err, dict) and err.get("message"):
        kind = err.get("type")
        return f"{kind}: {err['message']}" if kind else str(err["message"])
    return r.text[:300]


async def complete_json(
    *,
    base_url: str,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    temperature: float = 0.0,
    timeout_s: float = 240.0,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a chat completion to llama-server and return the parsed JSON object.

    The schema goes INSIDE `response_format.json_schema.schema`. Placed at the
    top level of `response_format` instead, the server ignores it without any
    error and returns unconstrained JSON — verified live, and pinned by a test.

    Thinking is disabled through `chat_template_kwargs.enable_thinking`; the
    structured tasks want the answer, not a reasoning trace.

    There is no warm-up or keep-alive: the router loads a model on its first
    request and unloads it after 600s idle, and `timeout_s` covers a reload.

    Options are app-owned — see `_DEFAULT_OPTIONS`. Pass `options=` to override
    per call; the `temperature` kwarg always wins.
    """
    url = _chat_url(base_url)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema},
        },
        "chat_template_kwargs": {"enable_thinking": False},
        # Per-call `options` override the app defaults; the explicit
        # `temperature` kwarg always wins over either.
        **_DEFAULT_OPTIONS,
        **(options or {}),
        "temperature": temperature,
    }

    async def _post(p: dict[str, Any]) -> str:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
                r = await client.post(url, json=p)
        except httpx.HTTPError as e:
            raise GatewayError(
                f"llama-server request failed (model={model}, {type(e).__name__}): {e}"
            ) from e
        if r.status_code >= 400:
            raise GatewayError(
                f"llama-server {r.status_code} (model={model}): {_error_detail(r)}"
            )
        body = r.json()
        choice = (body.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        if choice.get("finish_reason") == "length":
            # The grammar keeps output on-schema, so a cut-off generation is
            # the in-band reasoning runaway described at `max_tokens`. The
            # reinforcement retry below cannot fix that and would burn a second
            # full-length generation, so fail fast instead.
            raise GatewayError(
                f"llama-server hit max_tokens (model={model}); "
                f"output truncated: {str(content)[:200]}"
            )
        if not isinstance(content, str) or not content:
            raise GatewayError(f"llama-server returned no content: {body!r}")
        return content

    content = await _post(payload)
    try:
        parsed: Any = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        # Qwen3.5-9B occasionally emits markdown or a JSON array despite the
        # schema. Retry once with an explicit reinforcement.
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
                f"llama-server returned invalid JSON: {e} — {content[:200]}"
            ) from e
        if not isinstance(parsed, dict):
            raise GatewayError(f"expected object, got {type(parsed).__name__}")
    return parsed
