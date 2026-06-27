# CLI audit

Date: 2026-06-25

## Scope

This audit covers the current Typer command surface, help output, flag
classification, CLI test coverage, README command flow, and agent workflow
references.

The first help capture attempt used `uv run jobhunt ...`. It failed because
the sandbox could not write to `~/.cache/uv`. The user asked to continue, so
the audit used the installed local entrypoint at `.venv/bin/jobhunt`. The
rendered help output comes from the same project environment.

## Commands

Visible top-level commands:

| Command | Role | Assessment |
|---|---|---|
| `setup` | First-run wizard | Clean. |
| `convert-resume` | Baseline resume parse | Clean. |
| `scan` | Ingest plus score | Operational flags are valid, but `--refresh` and `--no-discover` help is long. |
| `apply` | Tailor, cover, browser autofill, lifecycle updates | Overloaded. This is the main help bloat source. |
| `add` | Add ATS slug from URL | Clean. |
| `answer` | Draft application-form answers | Clean enough. `--recall` is a mode flag, but the help is still readable. |
| `interview-prep` | Draft prep docs | Useful surface, but manual-intake help is long. |
| `list` | Target list plus pipeline views | Manageable. Status shortcuts are redundant with `--status`, but useful. |
| `analyze` | Deterministic analysis group | Clean group. Two subcommands have unnecessary mode flags. |
| `discover` | Legacy slug discovery | Acceptable as a visible legacy group. |

Hidden groups:

| Group | Role | Assessment |
|---|---|---|
| `config` | Setup and maintenance helpers | Correctly hidden from top-level help. README can mention it as hidden. |
| `db` | Database lifecycle helpers | Correctly hidden from top-level help. `reset` is destructive and should stay out of primary flow. |

Analyze subcommands:

| Command | Assessment |
|---|---|
| `analyze certs` | Useful defaults. Help is long but explains real options. |
| `analyze skills` | Requires `--gaps` even though gaps is the only supported mode. This should default to gaps. |
| `analyze employers` | Requires `--hiring-velocity` even though hiring velocity is the only supported mode. This should default to hiring velocity. |
| `analyze response-rate` | Clean. |
| `analyze validators` | Clean. |

## Help findings

- Top-level `jobhunt --help` is clean. The visible command count is reasonable.
- `apply --help` is the main problem. It mixes job selection, generation, browser execution, manual URL intake, and lifecycle tracking in one option list.
- `apply --help` truncates `--description-from-stdin` to `--description-from-st...`.
- `apply --mark-response` help says to combine with `--recruiter`, but the actual flag is `--recruiter-type`.
- `apply --recruiter-type` help is too long for a help table.
- `interview-prep --help` is usable, but `--description-from-stdin` is long and repeats the same manual-intake concept as `apply`.
- `scan --help`, `discover slugs --help`, and `analyze certs --help` are long, but their flags are not redundant.
- `list --help` is acceptable. The status shortcut flags should remain because they are easier than spelling `--status`.

## Flag classification

Primary workflow flags:

- `scan`: `--limit`, `--max-age-days`, `--no-discover`
- `list`: `--min-score`, `--week`, `--status`, `--applied`, `--drafted`, `--withdrawn`, `--no-reply`, `--older-than`, `--limit`
- `apply`: `<job-id>`, `--top`, `--best`, `--min-score`, `--no-browser`, `--url`
- `answer`: `--job`, `--max-words`, `--no-save`, `--recall`
- `interview-prep`: `<job-id>`, `--stage`, `--research`, `--recruiter-type`
- `analyze`: `certs`, `skills`, `employers`, `response-rate`, `validators`

Compatibility flags after cleanup:

- `analyze skills --gaps`
- `analyze employers --hiring-velocity`
- `apply --description-from-stdin`
- `interview-prep --description-from-stdin`

Advanced or maintenance flags:

- `scan --skip-score`, `scan --skip-ingest`, `scan --refresh`
- `apply --no-score`, `apply --force-robots`, `apply --include-borderline`
- `interview-prep --force-robots`, `interview-prep --refresh-research`, `interview-prep --no-llm`
- `add --skip-probe`
- `discover slugs --apply`, `--ats`, `--include-cached`
- Hidden `config` and `db` groups

Do not remove in this pass:

- No existing flag should be removed. Compatibility is a project requirement.

## Test map

Existing coverage found:

- `tests/test_analyze_expansion.py` covers `analyze skills --gaps`, the current failure for `analyze skills`, `analyze employers --hiring-velocity`, the current failure for `analyze employers`, and `analyze response-rate`.
- `tests/test_analyze_validators.py` covers `analyze validators`.
- `tests/test_add_cmd.py` covers `add`.
- `tests/test_config_seed.py` and `tests/test_config_reprobe.py` cover hidden config commands.
- `tests/test_setup_wizard.py` covers `setup`.
- `tests/test_manual_intake.py` covers shared manual-intake helpers and `interview-prep` validation branches.
- `tests/test_interview_prep.py` covers `_run_lifecycle` status nudges, but not the `apply` command lifecycle flags through Typer.
- `tests/test_db_response_tracking.py` covers response, interview, and outcome DB helpers.
- `tests/test_list_filters.py` covers list filters around response tracking.

Gaps to fill in later phases:

- Update `tests/test_analyze_expansion.py` so `analyze skills` and `analyze employers` succeed by default while old flagged forms still work.
- Add command-level tests for `--stdin` aliases on `apply` and `interview-prep`.
- If a future phase adds a lifecycle command, add Typer-level tests that prove it matches `_run_lifecycle` behavior.

## README findings

- README is a complete manual rather than a skimmable entry point.
- The workflow section says every flag is documented in README and that users should not need `--help`. That conflicts with the desired skimmable README direction.
- The daily flow is clear, but it uses verbose lifecycle commands like `jobhunt apply --set-status applied <job-id>`.
- The analyze examples still require `--gaps` and `--hiring-velocity`.

Recommendation:

- Phase 5 should keep install, first run, daily workflow, weekly workflow, data layout, and maintainer checks.
- Command details should become concise examples and tables.
- Full flag detail should live in `--help`, not README.

## Lifecycle workflow recommendation

Do not add a new top-level `track` command in this pass.

Reasons:

- Top-level `jobhunt --help` is already clean.
- The user explicitly wants to avoid app bloat.
- Adding `track` would introduce a new public surface that needs command-level tests.
- Existing lifecycle behavior is centralized in `apply_cmd._run_lifecycle`, so a future `track` command can reuse it if lifecycle updates remain awkward after help and README cleanup.

Current recommendation:

- Keep existing `apply` lifecycle flags for compatibility.
- Shorten their help text in Phase 3 only if it stays within the planned edit surface.
- Revisit `track` after README cleanup if the daily flow still feels too noisy.

## Phase recommendations

- Phase 2 should proceed. `analyze skills` and `analyze employers` should default to their only supported modes.
- Phase 3 should proceed. Add `--stdin` aliases and shorten the manual-intake help text.
- Phase 5 should proceed. Make README a skimmable developer guide.
- No extra phase is recommended for `track` during this pass.
