"""Guided first-run wizard.

Walks the user through DB init, resume conversion, applicant defaults, and
the curated seed import. Safe to re-run — each step detects existing state
and offers keep/redo. The applicant-prompt step is the canonical place to
update years_experience / include_senior_roles / salary / work arrangements
without hand-editing config.toml."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import typer

from jobhunt.commands import config_cmd, convert_resume_cmd
from jobhunt.commands._config_write import write_config_atomically
from jobhunt.commands.convert_resume_cmd import _REQUIRED_FIELDS
from jobhunt.config import EmploymentType, WorkArrangement, config_path, load_config
from jobhunt.db import connect, migrate

# Mirrors the Literal[...] options in ApplicantProfile. Hardcoded here so the
# wizard doesn't reach into Pydantic internals; if these expand in config.py,
# update both sites.
_WORK_ARRANGEMENTS: tuple[WorkArrangement, ...] = ("onsite", "hybrid", "remote")
_EMPLOYMENT_TYPES: tuple[EmploymentType, ...] = (
    "full_time",
    "part_time",
    "contract",
    "internship",
    "temporary",
)

app = typer.Typer(
    help="Guided first-run setup: DB init, resume parse, applicant defaults, seed import.",
    no_args_is_help=False,
)


def _header(text: str) -> None:
    typer.echo(f"\n=== {text} ===")


def _step_db_init() -> None:
    _header("Step 1/6: database")
    cfg = load_config()
    conn = connect(cfg.paths.db_path)
    try:
        migrate(conn, cfg.paths.migrations_dir)
    finally:
        conn.close()
    typer.echo(f"DB ready at {cfg.paths.db_path}")


def _step_resume_present() -> Path | None:
    from jobhunt.errors import PipelineError
    from jobhunt.resume.locate import describe_choice, find_baseline_resume

    _header("Step 2/6: resume file")
    try:
        docx = find_baseline_resume()
    except PipelineError:
        typer.echo(
            "no resume found here. Drop a .docx with 'resume' in the filename "
            "(e.g. Baseline_Resume.docx) into this directory."
        )
        if not typer.confirm("place your baseline resume there now and continue?", default=False):
            typer.echo("setup paused — re-run `jobhunt setup` once the resume is added.")
            return None
        try:
            docx = find_baseline_resume()
        except PipelineError:
            typer.echo("still no resume found. aborting.")
            return None
    typer.echo(describe_choice(docx))
    return docx


def _step_convert_resume(docx: Path) -> bool:
    """Parse the resume into kb/profile/. Returns True if `[applicant]` is incomplete."""
    _header("Step 3/6: parse resume")
    cfg = load_config()
    verified = cfg.paths.kb_dir / "profile" / "verified.json"
    if (
        verified.is_file()
        and verified.stat().st_mtime > docx.stat().st_mtime
        and not typer.confirm(
            f"{verified.name} is newer than {docx.name} — re-parse anyway?",
            default=False,
        )
    ):
        typer.echo("skipped (existing profile kept).")
        return False
    try:
        convert_resume_cmd.run(docx=docx)
    except SystemExit as e:
        # `convert-resume` exits 1 when a required `[applicant]` field could not
        # be read off the contact line (e.g. a resume with no GitHub link). That
        # is the right exit code standalone, but inside the wizard it aborted
        # steps 4-6, so the user lost applicant defaults and the seed list over
        # one missing URL. kb/profile/ is already written by this point; carry
        # on and re-raise the warning at the end where it can be acted on.
        if not e.code:
            raise
        return True
    return False


def _prompt_int(label: str, current: int | None) -> int:
    default = str(current) if current is not None else None
    while True:
        raw = typer.prompt(label, default=default, show_default=current is not None)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            typer.echo("  please enter an integer.")
            continue
        if value < 0:
            typer.echo("  must be >= 0.")
            continue
        return value


def _prompt_str(label: str, current: str) -> str:
    return str(typer.prompt(label, default=current, show_default=bool(current)))


def _prompt_multi[Choice: str](
    label: str, current: Sequence[Choice], allowed: tuple[Choice, ...]
) -> list[Choice]:
    typer.echo(f"{label} (comma-separated; allowed: {', '.join(allowed)})")
    raw = str(
        typer.prompt("  >", default=",".join(current), show_default=bool(current))
    )
    picked = [t.strip() for t in raw.split(",") if t.strip()]
    allowed_by_value = {value: value for value in allowed}
    invalid = [t for t in picked if t not in allowed_by_value]
    if invalid:
        typer.echo(f"  ignoring unknown: {', '.join(invalid)}")
    return [allowed_by_value[t] for t in picked if t in allowed_by_value]


def _step_applicant() -> None:
    _header("Step 4/6: applicant defaults")
    cfg = load_config()
    a = cfg.applicant

    a.years_experience = _prompt_int("years of professional dev experience", a.years_experience)
    a.include_senior_roles = typer.confirm(
        "include Senior / Lead / Staff / Principal titles in scan results?",
        default=a.include_senior_roles,
    )
    a.salary_expectation_cad = _prompt_str(
        "salary expectation (CAD, free text)", a.salary_expectation_cad
    )

    a.work_arrangements = _prompt_multi(
        "work arrangements", a.work_arrangements, _WORK_ARRANGEMENTS
    )
    a.employment_types = _prompt_multi("employment types", a.employment_types, _EMPLOYMENT_TYPES)

    write_config_atomically(cfg)
    typer.echo(f"\nwrote {config_path()} (inline comments dropped; .bak snapshot saved).")


def _step_config_show() -> None:
    _header("Step 5/6: resolved config")
    config_cmd.show()


def _step_seed() -> None:
    _header("Step 6/6: seed list")
    try:
        config_cmd.seed(preview=True, apply=False)
    except SystemExit:
        return
    if typer.confirm("\napply these to config.toml?", default=True):
        config_cmd.seed(preview=False, apply=True)


def _footer(applicant_incomplete: bool) -> None:
    if applicant_incomplete:
        missing = [f for f in _REQUIRED_FIELDS if not getattr(load_config().applicant, f)]
        typer.echo("\n=== Setup complete, with one thing left ===")
        typer.echo(
            f"[applicant] is still missing: {', '.join(missing)}.\n"
            f"Add them to {config_path()} — rendered resumes use them for the "
            "header, so `scan` and `apply` will produce an incomplete document "
            "until they are set.",
            err=True,
        )
        return
    typer.echo("\n=== Setup complete — happy hunting! ===")
    typer.echo("run `jobhunt --help` to see what's available, or `jobhunt scan` to start.")


@app.callback(invoke_without_command=True)
def run() -> None:
    """Guided first-run setup. Safe to re-run for updating applicant defaults."""
    _step_db_init()
    docx = _step_resume_present()
    if docx is None:
        raise typer.Exit(code=0)
    applicant_incomplete = _step_convert_resume(docx)
    _step_applicant()
    _step_config_show()
    _step_seed()
    _footer(applicant_incomplete)
