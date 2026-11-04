"""The one JSON envelope automated callers read from stdout (Q-K, 2026-11-01).

`{"schema": 1, "command", "ok", "exit_code", "error": null | {"class"},
...payload}`. Under `--json`, stdout carries that object and nothing else.
Every human line the command prints goes to stderr instead, so the caller
can parse stdout whole. The error names only its class: a message can carry
paths or model output, and the caller may store what it reads.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from typing import Any

import click

from jobhunt.errors import JobHuntError

SCHEMA = 1
_ENVELOPE_KEYS = frozenset({"schema", "command", "ok", "exit_code", "error"})


def _exit_code(exc: BaseException) -> int:
    if isinstance(exc, JobHuntError):
        return exc.exit_code
    if isinstance(exc, click.exceptions.Exit):
        return exc.exit_code
    return 1


@contextmanager
def json_output(command: str) -> Iterator[dict[str, Any]]:
    """Redirect stdout to stderr for the body, then print the envelope.

    The body fills the yielded dict with its payload. An exception escaping
    the body is reported in the envelope and re-raised, so `cli._run` still
    sets the process exit code.
    """
    real_stdout = sys.stdout
    payload: dict[str, Any] = {}
    exit_code = 0
    error: dict[str, str] | None = None
    try:
        with redirect_stdout(sys.stderr):
            yield payload
    except BaseException as exc:
        exit_code = _exit_code(exc)
        if exit_code != 0:
            error = {"class": type(exc).__name__}
        raise
    finally:
        shadowed = _ENVELOPE_KEYS & payload.keys()
        if shadowed and exit_code == 0:
            exit_code, error = 1, {"class": "ValueError"}
        envelope: dict[str, Any] = {
            "schema": SCHEMA,
            "command": command,
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "error": error,
        }
        if not shadowed:
            envelope |= payload
        real_stdout.write(json.dumps(envelope) + "\n")
        real_stdout.flush()
        if shadowed:
            raise ValueError(f"payload key shadows the envelope: {sorted(shadowed)}")
