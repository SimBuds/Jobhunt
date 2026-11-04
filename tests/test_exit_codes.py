"""Exit codes come from the error class (Phase 22b, 2026-11-03).

The automated caller reads one table, shared with SEO-LLM:
0 ok, 1 unhandled exception (a bug), 2 usage, 10 config or input missing or
invalid, 11 needs a person, 12 a stage failed, 13 router unavailable (retry
later), 14 reserved for "busy, retry later". Each `JobHuntError` subclass
declares its code, and `cli._run`, the installed entry point, exits with it
after one stderr line and no traceback.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from jobhunt import cli, errors
from jobhunt.gateway.client import complete_json

REPO = Path(__file__).resolve().parent.parent


def _raising(exc: BaseException):
    def app() -> None:
        raise exc

    return app


@pytest.mark.parametrize(
    ("cls", "code"),
    [
        ("ConfigError", 10),
        ("MigrationError", 10),
        ("InputError", 10),
        ("PrivacyError", 10),
        ("NeedsPersonError", 11),
        ("IngestError", 12),
        ("GatewayError", 12),
        ("PipelineError", 12),
        ("BrowserError", 12),
        ("JobHuntError", 12),
        ("GatewayUnavailableError", 13),
    ],
)
def test_domain_error_exits_with_its_class_code(
    cls: str, code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "app", _raising(getattr(errors, cls)("boom")))
    monkeypatch.setattr(sys, "argv", ["jobhunt"])
    with pytest.raises(SystemExit) as exc:
        cli._run()
    assert exc.value.code == code
    assert capsys.readouterr().err.strip() == "error: boom"


def test_unhandled_exception_still_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug is not a domain error: it keeps its traceback (Python exits 1)."""
    monkeypatch.setattr(cli, "app", _raising(RuntimeError("bug")))
    monkeypatch.setattr(sys, "argv", ["jobhunt"])
    with pytest.raises(RuntimeError):
        cli._run()


def test_debug_reraises_and_shows_detail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "app", _raising(errors.GatewayError("boom", detail="FRAGMENT")))
    monkeypatch.setattr(sys, "argv", ["jobhunt", "--debug"])
    with pytest.raises(errors.GatewayError):
        cli._run()
    assert "FRAGMENT" in capsys.readouterr().err


def test_detail_stays_hidden_without_debug(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Q-J: a model-output fragment can carry resume text, and stderr may be
    stored by the hub, so it only prints under --debug."""
    monkeypatch.setattr(cli, "app", _raising(errors.GatewayError("boom", detail="FRAGMENT")))
    monkeypatch.setattr(sys, "argv", ["jobhunt"])
    with pytest.raises(SystemExit):
        cli._run()
    assert "FRAGMENT" not in capsys.readouterr().err


def test_click_usage_error_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["jobhunt", "apply", "--no-such-flag"])
    with pytest.raises(SystemExit) as exc:
        cli._run()
    assert exc.value.code == 2


def test_installed_script_routes_through_run(tmp_path: Path) -> None:
    """The console script used to point at `app`, so `_run` never ran and a
    ConfigError printed a Rich traceback and exited 1 (seen 2026-11-01)."""
    script = Path(sys.executable).parent / "jobhunt"
    config_home = tmp_path / "cfg"
    (config_home / "jobhunt").mkdir(parents=True)
    (config_home / "jobhunt" / "config.toml").write_text("[paths\nbroken =\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("JOBHUNT_")}
    env |= {"XDG_CONFIG_HOME": str(config_home), "XDG_DATA_HOME": str(tmp_path / "data")}
    proc = subprocess.run(
        [str(script), "list"], cwd=REPO, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=60,
    )
    assert proc.returncode == 10, proc.stderr
    assert "Traceback" not in proc.stderr
    assert proc.stderr.startswith("error: ")


# --- gateway classification -------------------------------------------------

_ORIG_ASYNC_CLIENT = httpx.AsyncClient


def _serve(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    client = lambda *a, **kw: _ORIG_ASYNC_CLIENT(transport=httpx.MockTransport(handler))  # noqa: E731
    monkeypatch.setattr("httpx.AsyncClient", client)


async def _call() -> dict[str, Any]:
    return await complete_json(
        base_url="http://localhost:8080/v1", model="lite", system="s", user="u",
        schema={"type": "object"},
    )


def _raise(exc_cls):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_cls("boom", request=request)

    return handler


def _status(code: int, body: Any):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(code, text=body)
        return httpx.Response(code, json=body)

    return handler


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (_raise(httpx.ConnectError), "GatewayUnavailableError"),
        (_raise(httpx.ConnectTimeout), "GatewayUnavailableError"),
        (
            _status(503, {"error": {"code": 503, "message": "Loading model",
                                    "type": "unavailable_error"}}),
            "GatewayUnavailableError",
        ),
        (_status(500, {"error": {"code": 500, "message": "model name=lite failed to load",
                                  "type": "server_error"}}), "GatewayUnavailableError"),
        (_status(500, "proxy error: Failed to read connection"), "GatewayUnavailableError"),
        (_status(500, {"error": {"code": 500, "message": "unexpected", "type": "server_error"}}),
         "GatewayError"),
        (_status(400, {"error": {"code": 400, "message": "model 'lite' not found",
                                  "type": "invalid_request_error"}}), "ConfigError"),
        (_status(400, {"error": {"code": 400, "message": "prompt too long",
                                  "type": "exceed_context_size_error"}}), "GatewayError"),
        (_raise(httpx.ReadTimeout), "GatewayError"),
    ],
    ids=["connect-refused", "connect-timeout", "503-loading", "500-failed-to-load",
         "500-proxy-died", "500-other", "400-unknown-model", "400-context", "read-timeout"],
)
async def test_router_failures_are_classified(
    monkeypatch: pytest.MonkeyPatch, handler, expected: str
) -> None:
    _serve(monkeypatch, handler)
    with pytest.raises(errors.JobHuntError) as exc:
        await _call()
    assert type(exc.value).__name__ == expected


async def test_length_cutoff_keeps_the_fragment_out_of_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "length",
            "message": {"role": "assistant", "content": '{"summary": "RESUME TEXT'}}]})

    _serve(monkeypatch, handler)
    with pytest.raises(errors.GatewayError) as exc:
        await _call()
    assert type(exc.value) is errors.GatewayError
    assert "RESUME TEXT" not in str(exc.value)
    assert "RESUME TEXT" in exc.value.detail
