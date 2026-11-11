"""One JSON envelope on stdout for automated callers (Phase 22f, 2026-11-04).

Q-K: `{"schema": 1, "command", "ok", "exit_code", "error": null | {"class"},
...payload}`. Under `--json` stdout carries that one object and nothing
else, and every human line goes to stderr, so a caller can parse stdout
whole. The error names only its class: a message can carry paths or model
output, and the hub may store what it reads.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from jobhunt import errors
from jobhunt.cli import app
from jobhunt.commands._json_out import json_output
from jobhunt.commands.apply_cmd import _safe_id
from jobhunt.config import Config
from jobhunt.db import connect, migrate, upsert_application, upsert_job, write_score
from jobhunt.models import Job

SENTINEL = "SENTINEL-PROFILE-TEXT"


def _seed(migrations_dir: Path, *, jobs: bool = True) -> Config:
    cfg = Config()
    cfg.paths.profile_dir.mkdir(parents=True)
    (cfg.paths.profile_dir / "verified.json").write_text(
        json.dumps({"summary": SENTINEL, "work_history": [{"bullets": [SENTINEL]}]})
    )
    conn = connect(cfg.paths.db_path)
    migrate(conn, migrations_dir)
    if jobs:
        with conn:
            for n, score in (("1", 80), ("2", 65)):
                upsert_job(conn, Job(
                    id=f"greenhouse:acme:{n}", source="greenhouse", external_id=n,
                    company="Acme", title=f"Developer {n}", location="Toronto, ON",
                    description=SENTINEL, url=f"https://example.com/{n}",
                ))
                write_score(conn, job_id=f"greenhouse:acme:{n}", score=score, reasons=[],
                            red_flags=[], must_clarify=[], model="t", prompt_hash="t")
            upsert_application(
                conn, application_id="a2", job_id="greenhouse:acme:2", status="applied",
                resume_path=None, cover_path=None, fill_plan_path=None,
                applied_week="2026-W45",
            )
        audit = cfg.paths.data_dir / "applications" / _safe_id("greenhouse:acme:1")
        audit.mkdir(parents=True)
        (audit / "audit.json").write_text(json.dumps({"verdict": "ship"}))
    conn.close()
    return cfg


def _invoke(args: list[str]) -> Any:
    return CliRunner().invoke(app, args)


def test_list_json_stdout_is_one_envelope(migrations_dir: Path) -> None:
    _seed(migrations_dir)
    result = _invoke(["list", "--json"])
    assert result.exit_code == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["schema"] == 1
    assert out["command"] == "list"
    assert out["ok"] is True
    assert out["exit_code"] == 0
    assert out["error"] is None
    assert out["count"] == 1
    assert out["jobs"] == [{
        "id": "greenhouse:acme:1", "title": "Developer 1", "company": "Acme",
        "score": 80, "status": None, "verdict": "ship",
    }]


def test_list_json_sends_human_lines_to_stderr(migrations_dir: Path) -> None:
    _seed(migrations_dir)
    result = _invoke(["list", "--json"])
    assert "showing 1 job(s)" in result.stderr
    assert "ready to apply" in result.stderr
    assert "showing" not in result.stdout


def test_list_json_with_a_filter_carries_the_application_status(migrations_dir: Path) -> None:
    _seed(migrations_dir)
    out = json.loads(_invoke(["list", "--applied", "--json"]).stdout)
    assert [(j["id"], j["status"], j["verdict"]) for j in out["jobs"]] == [
        ("greenhouse:acme:2", "applied", None)
    ]


def test_list_json_zero_rows(migrations_dir: Path) -> None:
    _seed(migrations_dir, jobs=False)
    result = _invoke(["list", "--json"])
    assert result.exit_code == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["count"] == 0
    assert out["jobs"] == []


def test_list_json_never_carries_profile_or_description_text(migrations_dir: Path) -> None:
    _seed(migrations_dir)
    for args in (["list", "--json"], ["list", "--applied", "--json"]):
        assert SENTINEL not in _invoke(args).stdout


def test_list_json_error_still_prints_one_envelope(migrations_dir: Path) -> None:
    """A missing profile (InputError, 10) after the command starts."""
    cfg = Config()
    conn = connect(cfg.paths.db_path)
    migrate(conn, migrations_dir)
    conn.close()
    result = _invoke(["list", "--json"])
    out = json.loads(result.stdout)
    assert out["ok"] is False
    assert out["exit_code"] == 10
    assert out["error"] == {"class": "InputError"}
    assert "jobs" not in out


def test_list_json_usage_exit_inside_the_command(migrations_dir: Path) -> None:
    _seed(migrations_dir)
    result = _invoke(["list", "--verdict", "bad", "--json"])
    assert result.exit_code == 2
    out = json.loads(result.stdout)
    assert out["ok"] is False
    assert out["exit_code"] == 2
    assert "verdict must be one of" in result.stderr


def test_list_human_output_is_pinned(migrations_dir: Path) -> None:
    """The whole human output on the fixture, pinned exactly. Rewritten
    2026-11-10 (Phase O2) for the one-line-per-job format; `--json` is
    pinned separately and did not change."""
    _seed(migrations_dir)
    result = _invoke(["list"])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[:5] == [
        "ready to apply: 1  |  drafted, not submitted: 0  |  no reply >14d: 0",
        "",
        "showing 1 job(s)",
        "   80  Developer 1 · Acme · Toronto, ON  ship",
        "",
    ]
    assert re.fullmatch(r"This week \(\d{4}-W\d\d\): 2 scanned, 0 declined\.", lines[5])
    assert len(lines) == 6
    assert result.stderr == ""


# --- the helper on its own ---------------------------------------------------


def _envelope(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    out = capsys.readouterr().out
    assert out.count("\n") == 1, out
    return json.loads(out)


def test_helper_routes_print_and_echo_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    before = sys.stdout
    with json_output("demo") as payload:
        print("human line")
        typer.echo("echoed line")
        payload["n"] = 1
    captured = capsys.readouterr()
    assert "human line" in captured.err and "echoed line" in captured.err
    assert json.loads(captured.out) == {
        "schema": 1, "command": "demo", "ok": True, "exit_code": 0, "error": None, "n": 1,
    }
    assert sys.stdout is before


@pytest.mark.parametrize(
    ("exc", "code", "cls"),
    [(errors.GatewayUnavailableError("down"), 13, "GatewayUnavailableError"),
     (errors.PipelineError("x"), 12, "PipelineError"),
     (typer.Exit(code=2), 2, "Exit"),
     (RuntimeError("bug"), 1, "RuntimeError")],
)
def test_helper_reports_an_escaping_exception_and_reraises(
    exc: BaseException, code: int, cls: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(type(exc)), json_output("demo") as payload:
        payload["partial"] = True
        raise exc
    out = _envelope(capsys)
    assert out["ok"] is False
    assert out["exit_code"] == code
    assert out["error"] == {"class": cls}
    assert "down" not in json.dumps(out)


def test_helper_treats_exit_0_as_success(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit), json_output("demo"):
        raise typer.Exit(code=0)
    out = _envelope(capsys)
    assert out["ok"] is True and out["error"] is None


def test_helper_refuses_a_payload_key_that_shadows_the_envelope() -> None:
    with pytest.raises(ValueError, match="ok"), json_output("demo") as payload:
        payload["ok"] = "spoofed"


# --- scan --json (Phase 22g) -------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from jobhunt.commands import scan_cmd  # noqa: E402

_FILTERED = {"mgmt": 1, "stale": 2, "research": 0, "non_eng": 0, "senior": 0, "dup": 0}


def _fake_ingest(n_jobs: int):
    """Stand-in for `_ingest_all`: inserts `n_jobs` and reports one failed source
    whose error text (a URL, a body) must not reach the envelope."""
    async def ingest(cfg: Config, conn: Any, *, max_age_days: int) -> Any:
        with conn:
            for n in range(1, n_jobs + 1):
                upsert_job(conn, Job(
                    id=f"greenhouse:acme:{n}", source="greenhouse", external_id=str(n),
                    company="Acme", title=f"Developer {n}", location="Toronto, ON",
                    description=SENTINEL, url=f"https://example.com/{n}",
                ))
        per_source = [("greenhouse", "acme", n_jobs, None),
                      ("lever", "globex", 0, f"HTTP 500 {SENTINEL}")]
        return n_jobs, per_source, dict(_FILTERED)

    return ingest


def _fake_score(outcomes: dict[str, Any]):
    async def score_job(cfg: Config, job: Job) -> Any:
        outcome = outcomes[job.id]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return score_job


def _scored(score: int, decline: str | None = None) -> Any:
    return SimpleNamespace(score=score, matched_must_haves=[], decline_reason=decline,
                           gaps=[SENTINEL], model="t", breakdown=None)


def _scan_setup(migrations_dir: Path, monkeypatch: pytest.MonkeyPatch, n_jobs: int,
                outcomes: dict[str, Any]) -> None:
    _seed(migrations_dir, jobs=False)
    monkeypatch.setattr(scan_cmd, "_ingest_all", _fake_ingest(n_jobs))
    monkeypatch.setattr(scan_cmd, "score_job", _fake_score(outcomes))


def test_scan_json_reports_ingest_and_scores(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_setup(migrations_dir, monkeypatch, 2, {
        "greenhouse:acme:1": _scored(72),
        "greenhouse:acme:2": _scored(30, decline="Manager title"),
    })
    result = _invoke(["scan", "--no-discover", "--json"])
    assert result.exit_code == 0, result.stderr
    out = json.loads(result.stdout)
    assert (out["command"], out["ok"], out["error"]) == ("scan", True, None)
    assert out["ingest"] == {
        "inserted": 2, "filtered": _FILTERED,
        "source_errors": [{"source": "lever", "label": "globex"}],
    }
    assert out["score"] == {"scored": 2, "failed": 0, "declined": 1}
    assert out["jobs"] == [
        {"id": "greenhouse:acme:1", "title": "Developer 1", "company": "Acme",
         "score": 72, "declined": False},
        {"id": "greenhouse:acme:2", "title": "Developer 2", "company": "Acme",
         "score": 30, "declined": True},
    ]
    assert out["failures"] == []
    assert "ingest summary" in result.stderr


def test_scan_json_router_down_gives_a_partial_payload(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_setup(migrations_dir, monkeypatch, 2, {
        "greenhouse:acme:1": _scored(72),
        "greenhouse:acme:2": errors.GatewayUnavailableError(f"down {SENTINEL}"),
    })
    result = _invoke(["scan", "--no-discover", "--json"])
    # CliRunner runs `app`, not `cli._run`, so the error surfaces as the exception.
    assert isinstance(result.exception, errors.GatewayUnavailableError)
    out = json.loads(result.stdout)
    assert out["ok"] is False
    assert out["exit_code"] == 13
    assert out["error"] == {"class": "GatewayUnavailableError"}
    assert [j["id"] for j in out["jobs"]] == ["greenhouse:acme:1"]
    assert out["score"]["scored"] == 1


def test_scan_json_counts_a_per_job_failure_and_stays_ok(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_setup(migrations_dir, monkeypatch, 2, {
        "greenhouse:acme:1": errors.PipelineError(f"bad JD {SENTINEL}"),
        "greenhouse:acme:2": _scored(60),
    })
    result = _invoke(["scan", "--no-discover", "--json"])
    assert result.exit_code == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["ok"] is True
    assert out["score"] == {"scored": 1, "failed": 1, "declined": 0}
    assert out["failures"] == [
        {"id": "greenhouse:acme:1", "error": {"class": "PipelineError"}}
    ]


def test_scan_json_with_nothing_new(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_setup(migrations_dir, monkeypatch, 0, {})
    result = _invoke(["scan", "--no-discover", "--json"])
    assert result.exit_code == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["ingest"]["inserted"] == 0
    assert out["score"] == {"scored": 0, "failed": 0, "declined": 0}
    assert out["jobs"] == [] and out["failures"] == []


def test_scan_json_skip_ingest_reports_no_ingest(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_setup(migrations_dir, monkeypatch, 0, {})
    out = json.loads(_invoke(["scan", "--skip-ingest", "--json"]).stdout)
    assert out["ingest"] is None


def test_scan_json_never_carries_description_gaps_or_error_text(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_setup(migrations_dir, monkeypatch, 2, {
        "greenhouse:acme:1": errors.PipelineError(f"bad JD {SENTINEL}"),
        "greenhouse:acme:2": _scored(60),
    })
    assert SENTINEL not in _invoke(["scan", "--no-discover", "--json"]).stdout


# --- apply --top N --no-browser --json (Phase 22h) ---------------------------

from jobhunt.commands import apply_cmd  # noqa: E402
from jobhunt.pipeline.audit import AuditResult  # noqa: E402


def _audit(verdict: str, *, fabrication: list[str] | None = None) -> AuditResult:
    return AuditResult(
        keyword_coverage_pct=90, matched_keywords=[SENTINEL], missing_must_haves=[],
        fabrication_flags=fabrication or [], cover_letter_violations=[],
        alignment_flags=[], verdict=verdict,
    )


def _apply_setup(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, Any]
) -> Config:
    """Three queued jobs (scores 80, 70, 60). The LLM phase is faked per job id;
    the IO phase is real except the .docx render, which writes the sentinel."""
    cfg = _seed(migrations_dir, jobs=False)
    conn = connect(cfg.paths.db_path)
    with conn:
        for n, score in (("1", 80), ("2", 70), ("3", 60)):
            upsert_job(conn, Job(
                id=f"greenhouse:acme:{n}", source="greenhouse", external_id=n,
                company="Acme", title=f"Developer {n}", location="Toronto, ON",
                description=SENTINEL, url="",
            ))
            write_score(conn, job_id=f"greenhouse:acme:{n}", score=score, reasons=[],
                        red_flags=[], must_clarify=[], model="t", prompt_hash="t")
    conn.close()

    async def llm(cfg: Config, job: Job, *, verified: Any, echo: Any = None) -> Any:
        outcome = outcomes[job.id]
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome, AuditResult):
            return outcome
        out_dir = cfg.paths.data_dir / "applications" / _safe_id(job.id)
        out_dir.mkdir(parents=True, exist_ok=True)
        audit_path = out_dir / "audit.json"
        audit_path.write_text(json.dumps({"verdict": outcome.verdict, "text": SENTINEL}))
        return apply_cmd._LLMPhaseResult(
            out_dir=out_dir, tailored=SENTINEL, cover=SENTINEL,  # type: ignore[arg-type]
            audit_result=outcome, audit_path=audit_path,
            topics=apply_cmd._audit_topics(outcome),
            early_exit=outcome.verdict == "block",
        )

    def render(cfg: Config, job: Job, tailored: Any, cover: Any, out_dir: Path) -> Any:
        resume, cover_path = out_dir / "resume.docx", out_dir / "cover.docx"
        resume.write_text(SENTINEL)
        cover_path.write_text(SENTINEL)
        return resume, cover_path

    monkeypatch.setattr(apply_cmd, "_apply_llm_phase", llm)
    monkeypatch.setattr(apply_cmd, "_render_artifacts", render)
    return cfg


_APPLY = ["apply", "--top", "3", "--no-browser", "--drafted-by", "hub", "--json"]


def test_apply_json_reports_drafted_blocked_and_skipped(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply_setup(migrations_dir, monkeypatch, {
        "greenhouse:acme:1": _audit("ship"),
        "greenhouse:acme:2": _audit("block", fabrication=[f"invented {SENTINEL}"]),
        "greenhouse:acme:3": apply_cmd._Skipped("FabricationError"),
    })
    result = _invoke(_APPLY)
    assert result.exit_code == 0, (result.stderr, result.exception)
    out = json.loads(result.stdout)
    assert (out["command"], out["ok"], out["error"]) == ("apply", True, None)
    assert out["counts"] == {"drafted": 1, "blocked": 1, "skipped": 1}
    [drafted] = out["drafted"]
    assert {k: drafted[k] for k in ("id", "title", "company", "score", "status",
                                    "verdict", "drafted_by")} == {
        "id": "greenhouse:acme:1", "title": "Developer 1", "company": "Acme",
        "score": 80, "status": "drafted", "verdict": "ship", "drafted_by": "hub",
    }
    for key in ("resume_path", "cover_path", "audit_path"):
        assert Path(drafted[key]).is_file(), key
    assert out["blocked"] == [{"id": "greenhouse:acme:2", "rules": ["fabrication"]}]
    assert out["skipped"] == [
        {"id": "greenhouse:acme:3", "error": {"class": "FabricationError"}}
    ]


def test_apply_json_never_carries_document_or_flag_text(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply_setup(migrations_dir, monkeypatch, {
        "greenhouse:acme:1": _audit("ship"),
        "greenhouse:acme:2": _audit("block", fabrication=[f"invented {SENTINEL}"]),
        "greenhouse:acme:3": apply_cmd._Skipped("FabricationError"),
    })
    assert SENTINEL not in _invoke(_APPLY).stdout


def test_apply_json_router_down_gives_a_partial_payload(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply_setup(migrations_dir, monkeypatch, {
        "greenhouse:acme:1": _audit("ship"),
        "greenhouse:acme:2": errors.GatewayUnavailableError(f"down {SENTINEL}"),
        "greenhouse:acme:3": _audit("ship"),
    })
    result = _invoke(_APPLY)
    assert isinstance(result.exception, errors.GatewayUnavailableError)
    out = json.loads(result.stdout)
    assert (out["ok"], out["exit_code"]) == (False, 13)
    assert out["error"] == {"class": "GatewayUnavailableError"}
    assert [d["id"] for d in out["drafted"]] == ["greenhouse:acme:1"]
    assert out["counts"] == {"drafted": 1, "blocked": 0, "skipped": 0}


def test_apply_json_nothing_to_apply(migrations_dir: Path) -> None:
    _seed(migrations_dir, jobs=False)
    result = _invoke(["apply", "--top", "1", "--no-browser", "--json"])
    assert result.exit_code == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["ok"] is True
    assert out["drafted"] == [] and out["blocked"] == [] and out["skipped"] == []
    assert out["counts"] == {"drafted": 0, "blocked": 0, "skipped": 0}


@pytest.mark.parametrize(
    "args",
    [["apply", "greenhouse:acme:1", "--no-browser", "--json"],
     ["apply", "--top", "1", "--json"],
     ["apply", "--best", "--no-browser", "--json"]],
    ids=["job-id", "browser", "best"],
)
def test_apply_json_outside_the_automated_shape_is_a_usage_error(
    args: list[str], migrations_dir: Path
) -> None:
    _seed(migrations_dir, jobs=False)
    result = _invoke(args)
    assert result.exit_code == 2
    assert "--json is for automated runs" in result.stderr
    assert result.stdout == ""
