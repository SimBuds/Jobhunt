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
        [str(script), "list"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
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
        base_url="http://localhost:8080/v1",
        model="lite",
        system="s",
        user="u",
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
            _status(
                503,
                {"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}},
            ),
            "GatewayUnavailableError",
        ),
        (
            _status(
                500,
                {
                    "error": {
                        "code": 500,
                        "message": "model name=lite failed to load",
                        "type": "server_error",
                    }
                },
            ),
            "GatewayUnavailableError",
        ),
        (_status(500, "proxy error: Failed to read connection"), "GatewayUnavailableError"),
        (
            _status(500, {"error": {"code": 500, "message": "unexpected", "type": "server_error"}}),
            "GatewayError",
        ),
        (
            _status(
                400,
                {
                    "error": {
                        "code": 400,
                        "message": "model 'lite' not found",
                        "type": "invalid_request_error",
                    }
                },
            ),
            "ConfigError",
        ),
        (
            _status(
                400,
                {
                    "error": {
                        "code": 400,
                        "message": "prompt too long",
                        "type": "exceed_context_size_error",
                    }
                },
            ),
            "GatewayError",
        ),
        (_raise(httpx.ReadTimeout), "GatewayError"),
    ],
    ids=[
        "connect-refused",
        "connect-timeout",
        "503-loading",
        "500-failed-to-load",
        "500-proxy-died",
        "500-other",
        "400-unknown-model",
        "400-context",
        "read-timeout",
    ],
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
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "length",
                        "message": {"role": "assistant", "content": '{"summary": "RESUME TEXT'},
                    }
                ]
            },
        )

    _serve(monkeypatch, handler)
    with pytest.raises(errors.GatewayError) as exc:
        await _call()
    assert type(exc.value) is errors.GatewayError
    assert "RESUME TEXT" not in str(exc.value)
    assert "RESUME TEXT" in exc.value.detail


# --- batches stop on a run-wide failure (Phase 22c) --------------------------
#
# A 10 (config or input) or a 13 (router unavailable) fails every job the same
# way, so a batch stops at the first one and exits with its code. Before this,
# `scan` caught each job's error and exited 0, so with the router down every
# job failed and the hub read success. A 12 stays a per-job skip: one
# pathological posting must not halt every scan at the same job forever.

from types import SimpleNamespace  # noqa: E402

import typer  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from jobhunt.commands import apply_cmd, scan_cmd  # noqa: E402
from jobhunt.commands._refs import resolve_job_ref  # noqa: E402
from jobhunt.config import Config  # noqa: E402
from jobhunt.db import connect, migrate, upsert_job  # noqa: E402
from jobhunt.models import Job  # noqa: E402


def _job(n: str) -> Job:
    return Job(
        id=f"greenhouse:acme:{n}",
        source="greenhouse",
        external_id=n,
        company="Acme",
        title=f"Developer {n}",
        location="Toronto, ON",
        description="Need Python.",
        url=f"https://example.com/{n}",
    )


def _profiled_cfg(migrations_dir: Path, *job_ids: str) -> Config:
    """Default config (isolated by conftest) with a stub profile and a
    migrated database holding `job_ids`."""
    cfg = Config()
    cfg.paths.profile_dir.mkdir(parents=True)
    (cfg.paths.profile_dir / "verified.json").write_text("{}")
    conn = connect(cfg.paths.db_path)
    migrate(conn, migrations_dir)
    with conn:
        for n in job_ids:
            upsert_job(conn, _job(n))
    conn.close()
    return cfg


_SCORED = SimpleNamespace(
    score=60, matched_must_haves=[], decline_reason=None, gaps=[], model="t", breakdown=None
)


def _scorer(fail: dict[str, Exception], calls: list[str]):
    async def score_job(cfg: Config, job: Job) -> Any:
        calls.append(job.id)
        if job.id in fail:
            raise fail[job.id]
        return _SCORED

    return score_job


async def _scan(cfg: Config) -> None:
    await scan_cmd._run(
        cfg,
        skip_score=False,
        skip_ingest=True,
        limit=None,
        max_age_days=0,
        no_discover=True,
    )


@pytest.mark.parametrize(
    "exc",
    [errors.GatewayUnavailableError("router down"), errors.ConfigError("model 'x' not found")],
    ids=["router-down-13", "unknown-model-10"],
)
async def test_scan_stops_at_a_run_wide_failure(
    exc: Exception,
    migrations_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = _profiled_cfg(migrations_dir, "1", "2")
    calls: list[str] = []
    monkeypatch.setattr(
        scan_cmd, "score_job", _scorer({"greenhouse:acme:1": exc, "greenhouse:acme:2": exc}, calls)
    )
    with pytest.raises(type(exc)):
        await _scan(cfg)
    assert len(calls) == 1, "the scan kept scoring after a run-wide failure"
    assert "score: 0/2 scored" in capsys.readouterr().out


async def test_scan_skips_a_per_job_failure_and_scores_the_rest(
    migrations_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = _profiled_cfg(migrations_dir, "1", "2")
    calls: list[str] = []
    fail = {
        "greenhouse:acme:1": errors.PipelineError("bad JD"),
        "greenhouse:acme:2": errors.PipelineError("bad JD"),
    }
    first = sorted(fail)[0]
    monkeypatch.setattr(scan_cmd, "score_job", _scorer({first: fail[first]}, calls))
    await _scan(cfg)
    assert len(calls) == 2
    assert "score: 1/2 scored" in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["tailor", "cover"])
@pytest.mark.parametrize(
    ("exc", "stops"),
    [
        (errors.GatewayUnavailableError("router down"), True),
        (errors.ConfigError("model 'x' not found"), True),
        (errors.PipelineError("fabrication"), False),
    ],
    ids=["13", "10", "12"],
)
async def test_apply_llm_phase_raises_only_run_wide_failures(
    stage: str,
    exc: Exception,
    stops: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def deepen(cfg: Any, job: Job, *, echo: Any) -> Job:
        return job

    async def tailor(*a: Any, **kw: Any) -> Any:
        if stage == "tailor":
            raise exc
        return object(), [], 1

    async def cover(*a: Any, **kw: Any) -> Any:
        raise exc

    monkeypatch.setattr(apply_cmd, "_deepen_thin_adzuna", deepen)
    monkeypatch.setattr(apply_cmd, "tailor_resume_with_retry", tailor)
    monkeypatch.setattr(apply_cmd, "write_cover_with_retry", cover)
    # Since 2026-11-12 the score is read before tailoring (its gaps guard the
    # summary). This test has no database, so stub it like the others.
    monkeypatch.setattr(apply_cmd, "_load_score", lambda cfg, job_id: None)
    cfg = Config()
    if stops:
        with pytest.raises(type(exc)):
            await apply_cmd._apply_llm_phase(cfg, _job("1"), verified={})
    else:
        assert await apply_cmd._apply_llm_phase(cfg, _job("1"), verified={}) == apply_cmd._Skipped(
            "PipelineError"
        )


async def test_apply_each_stops_after_the_current_job_and_shows_progress(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Job B's LLM phase is prefetched during job A's IO phase. When B finds
    the router down, A still finishes, B's buffered header is shown, and C
    never starts."""
    events: list[str] = []

    async def llm(cfg: Any, job: Job, *, verified: Any, echo: Any = apply_cmd._default_echo):
        events.append(f"llm:{job.id}")
        echo(f"=== {job.id} ===")
        if job.id == "B":
            raise errors.GatewayUnavailableError("router down")
        return SimpleNamespace(early_exit=False, topics=[])

    async def io(
        cfg: Any, job: Job, phase: Any, *, no_browser: bool, drafted_by: str | None = None
    ) -> Any:
        events.append(f"io:{job.id}")
        return "ship", []

    monkeypatch.setattr(apply_cmd, "_apply_llm_phase", llm)
    monkeypatch.setattr(apply_cmd, "_apply_io_phase", io)
    monkeypatch.setattr(apply_cmd, "_row_to_job", lambda r: r)
    monkeypatch.setattr(apply_cmd.sys.stdin, "isatty", lambda: False)
    rows = [SimpleNamespace(id=i) for i in ("A", "B", "C")]
    with pytest.raises(errors.GatewayUnavailableError):
        await apply_cmd._apply_each(Config(), rows, no_browser=True)  # type: ignore[arg-type]
    assert events == ["llm:A", "io:A", "llm:B"]
    out = capsys.readouterr().out
    assert "=== B ===" in out
    assert "stopped: 2 job(s) not processed" in out


async def test_apply_url_scoring_stops_on_a_run_wide_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`apply --url` used to print the scoring failure and go on to tailor."""

    async def synth(cfg: Any, **kw: Any) -> Job:
        return _job("1")

    async def score_job(cfg: Any, job: Job) -> Any:
        raise errors.GatewayUnavailableError("router down")

    monkeypatch.setattr(apply_cmd, "synth_manual_job", synth)
    monkeypatch.setattr(apply_cmd, "score_job", score_job)
    with pytest.raises(errors.GatewayUnavailableError):
        await apply_cmd._resolve_manual(
            Config(),
            url="https://example.com/1",
            title=None,
            company=None,
            no_score=False,
            force_robots=False,
        )


def test_nothing_to_apply_exits_0(migrations_dir: Path) -> None:
    _profiled_cfg(migrations_dir)
    result = CliRunner().invoke(cli.app, ["apply", "--top", "1", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "nothing to apply to." in result.output


def test_missing_profile_exits_10_in_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["jobhunt", "list"])
    with pytest.raises(SystemExit) as exc:
        cli._run()
    assert exc.value.code == 10
    err = capsys.readouterr().err
    assert err.startswith("error: missing ")
    assert "jobhunt convert-resume" in err
    assert err.count("\n") == 1


def test_unknown_reference_exits_10(migrations_dir: Path, tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    migrate(conn, migrations_dir)
    try:
        with pytest.raises(typer.Exit) as exc:
            resolve_job_ref(conn, "nonexistent", scope="jobs")
    finally:
        conn.close()
    assert exc.value.exit_code == 10


# --- wrong working directory (Phase 22e) -------------------------------------
#
# `kb_dir` and `migrations_dir` resolve against the working directory, so run
# from anywhere else jobhunt used to fail late, on a missing prompt or
# migration. The callback now checks both up front and exits 10.


def test_wrong_working_dir_exits_10_naming_both_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["jobhunt", "list"])
    with pytest.raises(SystemExit) as exc:
        cli._run()
    assert exc.value.code == 10
    err = capsys.readouterr().err
    assert err.count("\n") == 1, err
    assert "migrations" in err and "prompts" in err
    assert "run from the repo root" in err


def test_repo_root_passes_the_working_dir_check() -> None:
    cli._check_working_dir(Config())


@pytest.mark.parametrize("args", [["config", "show"], ["config", "path"], ["scan", "--help"]])
def test_exempt_commands_run_outside_the_repo(
    args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output


def test_a_set_path_is_not_checked_as_a_working_dir_symptom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q (2026-11-04): only defaulted paths depend on the working directory.
    A kb_dir set in config is the user's choice, even one without prompts/."""
    monkeypatch.setenv("JOBHUNT_PATHS__KB_DIR", str(tmp_path / "elsewhere"))
    from jobhunt.config import load_config

    cli._check_working_dir(load_config())


def test_a_first_run_from_the_wrong_dir_does_not_pin_its_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Phase 22j: with no config.toml, the first run writes one. It used to
    pin the wrong directory's kb and migrations paths, so the second run
    skipped the check and failed late. Both runs must exit 10 here, and a run
    from the repo root afterwards must pass the check."""
    from jobhunt.config import config_path, load_config

    assert not config_path().exists()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["jobhunt", "list"])
    for _ in range(2):
        with pytest.raises(SystemExit) as exc:
            cli._run()
        assert exc.value.code == 10
        assert "run from the repo root" in capsys.readouterr().err
    assert config_path().exists()
    monkeypatch.chdir(REPO)
    cli._check_working_dir(load_config())
