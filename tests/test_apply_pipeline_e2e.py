"""End-to-end test for `_apply_one` — drives the apply pipeline with mocked
gateway/Playwright/render and asserts that audit verdicts steer the right
side effects.

No network, no Ollama, no browser. Pure structure-of-flow check.
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
                "INSERT INTO jobs (id, source, external_id, company, title) "
                "VALUES (?, ?, ?, ?, ?)",
                ("test:1", "lever", "1", "ACME", "Engineer"),
            )
    finally:
        conn.close()
    return cfg


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, *, audit_verdict: str) -> dict[str, Any]:
    """Stub out tailor/cover/audit/render/autofill. Returns a dict the test can
    inspect to verify which side effects fired."""
    calls: dict[str, Any] = {"render": 0, "render_cover": 0, "autofill": 0, "prompt": 0}

    async def fake_tailor(cfg: Config, job: Job) -> TailoredResume:
        return _fake_tailored()

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

    def fake_prompt(*args: Any, **kwargs: Any) -> str:
        calls["prompt"] += 1
        return "n"  # not submitted

    monkeypatch.setattr(apply_cmd, "tailor_resume", fake_tailor)
    monkeypatch.setattr(apply_cmd, "write_cover_with_retry", fake_cover_retry)
    monkeypatch.setattr(apply_cmd, "audit", fake_audit)
    monkeypatch.setattr(apply_cmd, "render", fake_render)
    monkeypatch.setattr(apply_cmd, "render_cover", fake_render_cover)
    monkeypatch.setattr(apply_cmd, "autofill", fake_autofill)
    monkeypatch.setattr(apply_cmd.typer, "prompt", fake_prompt)

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


def test_apply_one_ship_renders_and_records(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_pipeline(monkeypatch, audit_verdict="ship")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=False))

    assert calls["render"] == 1
    assert calls["render_cover"] == 1
    assert calls["autofill"] == 1
    out = cfg.paths.data_dir / "applications" / "test_1"
    assert (out / "audit.json").is_file()
    assert any(out.glob("Casey_Hsu_Resume_*.docx"))
    assert _count_apps(cfg.paths.db_path) == 1


def test_apply_one_revise_still_renders(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_pipeline(monkeypatch, audit_verdict="revise")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))

    # revise verdict still renders docs (per audit rules §4) but skips browser.
    assert calls["render"] == 1
    assert calls["render_cover"] == 1
    assert calls["autofill"] == 0
    assert _count_apps(cfg.paths.db_path) == 1


def test_apply_one_block_skips_render_and_db(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_pipeline(monkeypatch, audit_verdict="block")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=False))

    assert calls["render"] == 0
    assert calls["render_cover"] == 0
    assert calls["autofill"] == 0
    out = cfg.paths.data_dir / "applications" / "test_1"
    assert (out / "audit.json").is_file()  # audit.json IS written before the block
    assert _count_apps(cfg.paths.db_path) == 0  # no application row recorded


def test_apply_one_no_browser_skips_autofill(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_pipeline(monkeypatch, audit_verdict="ship")
    asyncio.run(apply_cmd._apply_one(cfg, _job(), verified={}, no_browser=True))
    assert calls["autofill"] == 0
    assert calls["prompt"] == 0  # no plan_path → no submission prompt
