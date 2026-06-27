"""Ollama gateway. Uses /api/chat with `format` for JSON-schema-constrained output."""

from __future__ import annotations

import json
from typing import Any

import httpx

from jobhunt.errors import GatewayError

# Options the app pins on every structured call so behavior is defined in-repo
# and identical regardless of which model is configured. Three parts:
#
#   num_ctx: the score/tailor prompts run ~6k+ tokens. Ollama's default context
#   is 4096 and OLLAMA_CONTEXT_LENGTH is NOT reliably set on this box, so without
#   an explicit num_ctx the prompt is silently truncated to 4096 — the schema
#   instruction falls off the end and the model emits prose instead of JSON.
#   (qwen-custom only worked because it baked num_ctx 16384.) Pinning it here is
#   what lets jobhunt run bare qwen3.5:9b. Pinned at 32768 (2026-06-04): on
#   Ollama 0.30.3 qwen3.5:9b Q4_K_M stays 100% GPU-resident at 32k (measured
#   5.6 GB on the 10 GB card), so the extra headroom is free. The score/tailor
#   prompts still run ~6k tokens, so MAX_DESC_CHARS / MAX_POLICY_CHARS in
#   pipeline.score need no change — 32k is pure headroom, not a reason to feed
#   longer inputs. The q8_0 build was rejected: it spills to CPU at both 16k and
#   32k on this card and the bench showed no quality gain over Q4_K_M.
#
#   sampler params: qwen3.5:9b ships `presence_penalty 1.5` — Qwen's
#   recommendation for *thinking/chat* mode, where it breaks reasoning-loop
#   repetition. We run `think=false` emitting schema-constrained JSON, where that
#   penalty is misapplied (it discourages the repeated tokens structured output
#   needs: JSON field names, the verbatim JD keywords the tailor must echo), so
#   we drop it to 0 and otherwise keep Qwen's recommended nucleus sampling.
#
#   num_predict: the generation ceiling and the safety net for the dropped
#   presence_penalty above. With think=false the model is *supposed* to emit only
#   schema-constrained JSON, but on some inputs qwen3.5:9b reasons IN-BAND — it
#   opens a JSON string (e.g. a `reasons[]` item) and pours a stream-of-conscious
#   monologue into it, never closing the string, generating until it exhausts
#   num_ctx (~16k tokens ≈ 210s). That blows past the 240s ReadTimeout below and
#   stalls the whole scan (measured 2026-05-31: a thin Adzuna junior-coop JD hit
#   8000 tokens, done_reason=length, 28KB of unterminated JSON). 4096 sits well
#   above the largest legitimate output (tailor at 700 words ≈ ~2.2k tokens) so it
#   never truncates real work, while bounding each generation to ~50s. A
#   pathological JD is then abandoned in ~100s end-to-end (the ~50s cap × the one
#   invalid-JSON retry complete_json does below) — a fast, logged failure instead
#   of the prior 240s-per-attempt ReadTimeout that stalled the whole scan.
#
# Override any of these per call via the `options` kwarg; the `temperature`
# kwarg always wins.
_DEFAULT_OPTIONS: dict[str, Any] = {
    "num_ctx": 32768,
    "num_predict": 4096,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 0.0,
}


async def complete_json(
    *,
    base_url: str,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    temperature: float = 0.0,
    timeout_s: float = 240.0,
    keep_alive: str | int | None = -1,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a chat completion to Ollama and return the parsed JSON object.

    `base_url` may end with `/v1` (OpenAI-compatible) or be a bare host. We hit the
    native /api/chat endpoint either way for the format-as-schema feature.

    Options are app-owned: `_DEFAULT_OPTIONS` is sent on every call so
    structured-task behavior is defined in-repo, not by the model's Modelfile or
    by server env. This includes `num_ctx=32768` (the prompts exceed Ollama's
    4096 default; without it they truncate and the model emits prose) and
    `presence_penalty=0` (qwen's chat/thinking default fights structured JSON).
    Keep `MAX_DESC_CHARS`/`MAX_POLICY_CHARS` in `pipeline.score` aligned with the
    pinned `num_ctx`. Pass `options=` to override per call; the `temperature`
    kwarg always wins.

    `keep_alive` defaults to `-1` (load forever) so the hot model stays resident
    across scan/apply runs without paying a 5-15 s reload. This matches the
    server-side `OLLAMA_KEEP_ALIVE=-1`; the per-call value is what Ollama uses,
    so making it explicit here keeps behavior consistent regardless of env.
    Pass `keep_alive=None` to omit the key entirely and let the server-side
    `OLLAMA_KEEP_ALIVE` govern residency (used by the manual bench script).
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
        # Per-call `options` override the app defaults; the explicit
        # `temperature` kwarg always wins over either.
        "options": {**_DEFAULT_OPTIONS, **(options or {}), "temperature": temperature},
    }
    # keep_alive=None omits the key so Ollama's server-side OLLAMA_KEEP_ALIVE
    # governs residency. The default (-1) still pins the model for active runs.
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
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
        if not isinstance(content, str) or not content:
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
