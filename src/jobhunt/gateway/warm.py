"""Pre-warm an Ollama-resident model.

Shared by `scan` and `apply`. The first real call to a cold model pays the
load cost on top of the 180s gateway timeout. We also send a prompt large
enough that Ollama's KV cache lands in the right size band — a tiny 2-token
"ok" warmup forces a KV-cache realloc on the first real prompt, which on
q5_0 quantized cache + flash attention can take 90-120 seconds (observed:
second-call freeze in scan).
"""

from __future__ import annotations

import typer

from jobhunt.config import Config
from jobhunt.errors import GatewayError
from jobhunt.gateway.client import complete_json


_WARMUP_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}

# Realistic-sized warmup prompt. Target ~6-8 KB of input (similar to a real
# score call's system + verified + JD blob) so Ollama allocates its KV cache
# to a size that won't need reallocation on the first scoring call.
_WARMUP_FILLER = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
    "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
    "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum. "
) * 12  # ≈6 KB


async def warm_model(cfg: Config, task: str = "score") -> None:
    """Send a JSON-schema chat to the model configured for `task` with a
    realistic-sized prompt so the KV cache is sized correctly from the start.

    `task` defaults to "score" because that's the slot used at the start of
    every scan + apply run. All three task slots (score, tailor, cover) point
    at the same model on the user's setup (one model hot in VRAM at a time
    per AGENTS.md), so warming any one of them keeps the others warm too.

    Failures are non-fatal — we log and let the caller proceed; if the
    gateway is genuinely down, the next real call will surface the error
    with a clearer context anyway.
    """
    model = cfg.gateway.tasks.get(task, "")
    if not model:
        return
    typer.echo(f"{task}: warming {model}...")

    # Build a primer from verified.json when available — most representative
    # of the score call's actual shape. Falls back to filler-only when the
    # profile hasn't been generated yet.
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    primer = ""
    if verified_path.is_file():
        try:
            primer = verified_path.read_text(encoding="utf-8")
        except OSError:
            primer = ""
    user = (
        "# Warmup\n"
        "The following is filler to size the KV cache. Respond with "
        '{"ok": true}.\n\n'
        f"## Verified facts (sample)\n{primer}\n\n"
        f"## Job description (filler)\n{_WARMUP_FILLER}"
    )
    try:
        await complete_json(
            base_url=cfg.gateway.base_url,
            model=model,
            system="Return JSON.",
            user=user,
            schema=_WARMUP_SCHEMA,
        )
    except GatewayError as e:
        typer.echo(f"  ! warm-up failed (continuing): {e}", err=True)
