"""Pre-warm an Ollama-resident model.

Shared by `scan` and `apply`. The first real call to a cold model pays the
load cost on top of the 180s gateway timeout — a trivial schema-validated
chat keeps the model resident for the subsequent real calls.
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


async def warm_model(cfg: Config, task: str = "score") -> None:
    """Send a trivial JSON-schema chat to the model configured for `task`.

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
    try:
        await complete_json(
            base_url=cfg.gateway.base_url,
            model=model,
            system="Return JSON.",
            user="ok",
            schema=_WARMUP_SCHEMA,
        )
    except GatewayError as e:
        typer.echo(f"  ! warm-up failed (continuing): {e}", err=True)
