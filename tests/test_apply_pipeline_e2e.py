"""End-to-end test for `_apply_one` — drives the apply pipeline with mocked
gateway/Playwright/render and asserts that audit verdicts steer the right
side effects.

No network, no model server, no browser. Pure structure-of-flow check.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from jobhunt.commands import apply_cmd
from jobhunt.config import Config
from jobhunt.db import migrate
from jobhunt.models import Job
from jobhunt.pipeline.audit import AuditResult
from jobhunt.pipeline.cover import CoverLetter
from jobhunt.pipeline.tailor import TailoredCategory, TailoredResume


def _fake_tailored() -> TailoredResume:
    return TailoredResume(
        summary="Senior engineer with Python + Postgres experience.",
        skills_categories=[TailoredCategory(name="Backend", items=["Python", "Postgres"])],
        roles=[],
        certifications=[],
        education=[],
        coursework=[],
        model="test-model",
    )


def _fake_cover() -> CoverLetter:
    return CoverLetter(
        salutation="Dear Hiring Manager,",
        body=["I am applying to ACME for the engineer role."],
        sign_off="Sincerely, Casey",
        model="test-model",
    )


def _make_audit(verdict: str) -> AuditResult:
    return AuditResult(
        keyword_coverage_pct=85,
        matched_keywords=["python"],
        missing_must_haves=[],
        fabrication_flags=[],
        cover_letter_violations=[],
        alignment_flags=[],
        verdict=verdict,
    )


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Config rooted at tmp_path with a real (migrated) sqlite DB."""
    repo_root = Path(__file__).resolve().parents[1]
    cfg = Config()
    cfg.paths.data_dir = tmp_path
    cfg.paths.db_path = tmp_path / "jobhunt.db"
    cfg.paths.migrations_dir = repo_root / "migrations"
    cfg.paths.kb_dir = tmp_path / "kb"
    cfg.browser.user_data_dir = tmp_path / "browser-profile"
    # Initialise the DB so upsert_application works.
    conn = sqlite3.connect(cfg.paths.db_path)
    conn.row_factory = sqlite3.Row
    try:
        migrate(conn, cfg.paths.migrations_dir)
        # Pre-seed the job row so the FK on applications doesn't fail.
        with conn:
            conn.execute(
                "INSERT INTO jobs (id, source, external_id, company, title) VALUES (?, ?, ?, ?, ?)",
                ("test:1", "lever", "1", "ACME", "Engineer"),
            )
    finally:
        conn.close()
    return cfg


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, *, audit_verdict: str) -> dict[str, Any]:
    """Stub out tailor/cover/audit/render/autofill. Returns a dict the test can
    inspect to verify which side effects fired."""
    calls: dict[str, Any] = {"render": 0, "render_cover": 0, "autofill": 0, "prompt": 0}

    async def fake_tailor_retry(*args: Any, **kwargs: Any) -> tuple[TailoredResume, list[Any], int]:
        # Matches the (tailored, violations, attempts) tuple shape of
        # `tailor_resume_with_retry`. Tests don't exercise the retry path,
        # so always return a clean first-attempt result.
        return _fake_tailored(), [], 1

    async def fake_cover_retry(*args: Any, **kwargs: Any) -> tuple[CoverLetter, list[str], int]:
        return _fake_cover(), [], 1

    def fake_audit(**kwargs: Any) -> AuditResult:
        return _make_audit(audit_verdict)

    def fake_render(*args: Any, **kwargs: Any) -> None:
        calls["render"] += 1
        kwargs["out_path"].write_bytes(b"fake-docx")

    def fake_render_cover(*args: Any, **kwargs: Any) -> None:
        calls["render_cover"] += 1
        kwargs["out_path"].write_bytes(b"fake-docx")

    async def fake_autofill(**kwargs: Any) -> Path:
        calls["autofill"] += 1
        plan = kwargs["out_dir"] / "fill-plan.json"
        plan.write_text("{}", encoding="utf-8")
        return plan

    def fake_input(*args: Any, **kwargs: Any) -> str:
        calls["prompt"] += 1
        return "n"  # not submitted

    monkeypatch.setattr(apply_cmd, "tailor_resume_with_retry", fake_tailor_retry)
    monkeypatch.setattr(apply_cmd, "write_cover_with_retry", fake_cover_retry)
    monkeypatch.setattr(apply_cmd, "audit", fake_audit)
    monkeypatch.setattr(apply_cmd, "render", fake_render)
    monkeypatch.setattr(apply_cmd, "render_cover", fake_render_cover)
    monkeypatch.setattr(apply_cmd, "autofill", fake_autofill)
    monkeypatch.setattr("builtins.input", fake_input)

    return calls


def _job() -> Job:
    return Job(
        id="test:1",
        source="lever",
        external_id="1",
        company="ACME",
        title="Engineer",
        description="Need Python and Postgres.",
        url="https://jobs.lever.co/acme/1/apply",
    )


def _count_apps(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    finally:
        conn.close()


def test_apply_one_ship_renders_and_records(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pipeline(monkeypatch, audit_verdict="ship")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=False))

    assert calls["render"] == 1
    assert calls["render_cover"] == 1
    assert calls["autofill"] == 1
    out = cfg.paths.data_dir / "applications" / "test_1"
    assert (out / "audit.json").is_file()
    assert (out / "Resume.docx").exists()
    assert (out / "Cover_Letter.docx").exists()
    assert _count_apps(cfg.paths.db_path) == 1


def test_apply_one_revise_still_renders(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pipeline(monkeypatch, audit_verdict="revise")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))

    # revise verdict still renders docs (per audit rules §4) but skips browser.
    assert calls["render"] == 1
    assert calls["render_cover"] == 1
    assert calls["autofill"] == 0
    assert _count_apps(cfg.paths.db_path) == 1


def test_apply_one_block_skips_render_and_db(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pipeline(monkeypatch, audit_verdict="block")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=False))

    assert calls["render"] == 0
    assert calls["render_cover"] == 0
    assert calls["autofill"] == 0
    out = cfg.paths.data_dir / "applications" / "test_1"
    assert (out / "audit.json").is_file()  # audit.json IS written before the block
    assert _count_apps(cfg.paths.db_path) == 0  # no application row recorded


def test_apply_one_no_browser_skips_autofill(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_pipeline(monkeypatch, audit_verdict="ship")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))
    assert calls["autofill"] == 0
    assert calls["prompt"] == 0  # no plan_path → no submission prompt


def _app_rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM applications"))
    finally:
        conn.close()


def test_apply_one_records_drafted_before_browser_crash(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash in the browser step must not lose the tailoring run.

    Regression for the 11 orphan artifact dirs found 2026-07-24: the
    application row used to be written only after the browser step and the
    submit prompt, so an uncaught failure in either left finished .docx files
    on disk with nothing in `applications` pointing at them. `BrowserError` is
    handled by `_run_browser_step`'s retry loop; this simulates the unhandled
    class (a hard Playwright fault, or a Ctrl-C at the prompt).
    """
    _patch_pipeline(monkeypatch, audit_verdict="ship")

    async def exploding_autofill(**kwargs: Any) -> Path:
        raise RuntimeError("playwright died")

    monkeypatch.setattr(apply_cmd, "autofill", exploding_autofill)

    with pytest.raises(RuntimeError):
        asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=False))

    rows = _app_rows(cfg.paths.db_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "drafted"
    assert rows[0]["job_id"] == "test:1"
    # Artifacts the run produced are now reachable from the row.
    assert rows[0]["resume_path"].endswith("Resume.docx")


def test_apply_one_confirmed_status_overwrites_early_draft(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The post-prompt write wins: one row, final status, fill-plan attached."""
    _patch_pipeline(monkeypatch, audit_verdict="ship")
    # `_confirm_submission_status` short-circuits to 'drafted' off a tty, which
    # would pass this test for the wrong reason — force the interactive path.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=False))

    rows = _app_rows(cfg.paths.db_path)
    assert len(rows) == 1  # upsert on job_id, not a second row
    assert rows[0]["status"] == "applied"
    assert rows[0]["applied_at"] is not None
    assert rows[0]["fill_plan_path"].endswith("fill-plan.json")


# --- Phase 21b (2026-11-03): a redraft never resets a recorded status --------
#
# `apply` writes 'drafted' before the submission prompt, and again after it
# when there is no browser step or no tty. The upsert sets `status =
# excluded.status` on conflict, so redrafting a job already marked applied
# (to re-tailor for an interview, say) reset it to 'drafted', put it back in
# the `--top` queue, and overwrote the week it was applied in.


def _seed_application(cfg: Config, status: str) -> None:
    conn = sqlite3.connect(cfg.paths.db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO applications (id, job_id, status, resume_path, "
                "applied_at, applied_week, outcome_at) VALUES "
                "('app-1', 'test:1', ?, '/old/Resume.docx', "
                "'2026-10-01 09:00:00', '2026-W40', '2026-10-15 09:00:00')",
                (status,),
            )
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["applied", "interviewing", "offer", "rejected", "withdrawn"])
def test_redraft_keeps_a_recorded_status_and_its_dates(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    _seed_application(cfg, status)
    _patch_pipeline(monkeypatch, audit_verdict="ship")

    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))

    [row] = _app_rows(cfg.paths.db_path)
    assert row["status"] == status
    assert (row["applied_at"], row["applied_week"], row["outcome_at"]) == (
        "2026-10-01 09:00:00",
        "2026-W40",
        "2026-10-15 09:00:00",
    )
    # The documents are still regenerated and the row points at the new ones.
    assert row["resume_path"] != "/old/Resume.docx"
    assert row["resume_path"].endswith("Resume.docx")


def test_redraft_of_a_drafted_job_stays_drafted(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_application(cfg, "drafted")
    _patch_pipeline(monkeypatch, audit_verdict="ship")

    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))

    [row] = _app_rows(cfg.paths.db_path)
    assert row["status"] == "drafted"
    assert row["resume_path"] != "/old/Resume.docx"


def test_set_status_drafted_still_resets_on_purpose(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard lives in apply's draft path, not in the upsert, so an explicit
    `apply --set-status drafted` (undoing a mistaken 'applied') still works."""
    _seed_application(cfg, "applied")
    monkeypatch.setattr(apply_cmd, "load_config", lambda: cfg)

    apply_cmd._run_lifecycle(
        "test:1",
        set_status="drafted",
        mark_response=None,
        mark_interview=None,
        set_outcome=None,
        recruiter_type=None,
    )

    [row] = _app_rows(cfg.paths.db_path)
    assert row["status"] == "drafted"


def test_prompt_answer_still_moves_a_recorded_status(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a 'drafted' write is held back. A real answer at the submission
    prompt (here 'withdrawn' on a job already marked applied) is recorded."""
    _seed_application(cfg, "applied")
    _patch_pipeline(monkeypatch, audit_verdict="ship")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "w")

    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=False))

    [row] = _app_rows(cfg.paths.db_path)
    assert row["status"] == "withdrawn"


# --- Phase 22a (2026-11-03): the automated caller is recorded and fenced ----


def test_hub_draft_records_the_actor(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, audit_verdict="ship")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True, drafted_by="hub"))
    [row] = _app_rows(cfg.paths.db_path)
    assert (row["status"], row["drafted_by"]) == ("drafted", "hub")


def test_human_draft_records_null(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, audit_verdict="ship")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))
    [row] = _app_rows(cfg.paths.db_path)
    assert row["drafted_by"] is None


def test_hub_run_leaves_a_job_past_drafted_untouched(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selection already excludes these. This is the write-time backstop for a
    race (Casey marks it applied mid-run): the row is not written at all."""
    _seed_application(cfg, "applied")
    _patch_pipeline(monkeypatch, audit_verdict="ship")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True, drafted_by="hub"))
    [row] = _app_rows(cfg.paths.db_path)
    assert (row["status"], row["resume_path"], row["drafted_by"]) == (
        "applied",
        "/old/Resume.docx",
        None,
    )


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["apply", "test:1", "--drafted-by", "hub"], "automated runs"),
        (["apply", "test:1", "--set-status", "applied", "--drafted-by", "hub"], "automated runs"),
        (["apply", "--top", "1", "--drafted-by", "bot"], "Invalid value"),
        (["apply", "--top", "1", "--drafted-by", "hub"], "automated runs"),
        (["apply", "--best", "--no-browser", "--drafted-by", "hub"], "automated runs"),
        (
            ["apply", "--url", "https://example.com/job", "--no-browser", "--drafted-by", "hub"],
            "automated runs",
        ),
    ],
    ids=["explicit-id", "lifecycle-flag", "unknown-actor", "browser", "best", "url"],
)
def test_drafted_by_usage_errors_exit_2(argv: list[str], reason: str) -> None:
    """Exit 2 alone would also pass on HEAD, where --drafted-by is simply an
    unknown option, so each case also checks why it was refused."""
    from typer.testing import CliRunner

    from jobhunt.cli import app

    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 2, result.output
    assert reason in result.output


# --- Phase 6a.2 (2026-11-10): resume header from verified.json -------------
#
# The resume renders verified.json's contact_line (headline, city, links); the
# cover letter keeps the config.toml line (AGENTS.md "Resume render vs design").

VERIFIED_CONTACT = "Shopify and WordPress Developer  Toronto, ON | 416 | x@y.com"


def _capture_contacts(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    seen: dict[str, str] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        seen["resume"] = kwargs["contact_line"]
        kwargs["out_path"].write_bytes(b"fake-docx")

    def fake_render_cover(*args: Any, **kwargs: Any) -> None:
        seen["cover"] = kwargs["contact_line"]
        kwargs["out_path"].write_bytes(b"fake-docx")

    monkeypatch.setattr(apply_cmd, "render", fake_render)
    monkeypatch.setattr(apply_cmd, "render_cover", fake_render_cover)
    return seen


def _profile_with(cfg: Config, tmp_path: Path, payload: dict[str, Any]) -> None:
    import json

    cfg.paths.profile_dir = tmp_path / "profile"
    cfg.paths.profile_dir.mkdir()
    (cfg.paths.profile_dir / "verified.json").write_text(json.dumps(payload))
    cfg.applicant.email = "x@y.com"


def test_resume_uses_verified_contact_and_cover_keeps_config(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch, audit_verdict="ship")
    seen = _capture_contacts(monkeypatch)
    _profile_with(cfg, tmp_path, {"contact_line": VERIFIED_CONTACT})
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))
    assert seen["resume"] == VERIFIED_CONTACT
    assert seen["cover"].startswith("x@y.com")


def test_resume_falls_back_to_config_contact(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch, audit_verdict="ship")
    seen = _capture_contacts(monkeypatch)
    _profile_with(cfg, tmp_path, {"name": "Casey Hsu"})
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))
    assert seen["resume"] == seen["cover"]
    assert seen["resume"].startswith("x@y.com")
