"""Readable per-job lines (Phase O1, 2026-11-10).

A scan printed one line per job led by the internal id
(`lever:acme:230b8d92-...`, up to 57 characters), so the title ran off the
screen. Standard output now leads with the score and `Title · Company`; the id
and decline reason come back under `--verbose`. `--json` stays unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from jobhunt import errors
from jobhunt.commands._job_line import job_line

# Reuse the scan harness from the --json tests (mocked ingest and scoring).
_spec = importlib.util.spec_from_file_location(
    "_json_output_harness", Path(__file__).with_name("test_json_output.py")
)
assert _spec and _spec.loader
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)
_invoke, _scan_setup, _scored = _harness._invoke, _harness._scan_setup, _harness._scored


def test_scored_line_leads_with_score_title_and_company() -> None:
    assert job_line(title="Shopify Developer", company="Acme", score=72) == (
        " 72  Shopify Developer · Acme"
    )


def test_declined_line_hides_score_and_reason() -> None:
    line = job_line(title="Team Lead", company="Acme", score=30, declined=True)
    assert line == " --  Team Lead · Acme  declined"


def test_missing_company_or_title_leaves_no_dangling_separator() -> None:
    assert job_line(title="Developer", company=None, score=60) == " 60  Developer"
    assert job_line(title=None, company="Acme", score=60) == " 60  Acme"


def test_long_line_is_cut_to_the_width_with_an_ellipsis() -> None:
    line = job_line(title="Senior " * 30, company="Acme", score=45, width=60)
    assert len(line) <= 60
    assert line.endswith("…")


def test_declined_marker_survives_the_cut() -> None:
    line = job_line(title="Lead " * 30, company="Acme", score=30, declined=True, width=60)
    assert len(line) <= 60
    assert line.endswith("…  declined")


# --- scan, end to end through the CLI ----------------------------------------


def _scan(migrations_dir: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], outcomes):
    monkeypatch.setenv("COLUMNS", "100")
    _scan_setup(migrations_dir, monkeypatch, len(outcomes), outcomes)
    return _invoke(args)


def test_scan_standard_lines_have_no_job_id(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _scan(
        migrations_dir,
        monkeypatch,
        ["scan", "--no-discover"],
        {
            "greenhouse:acme:1": _scored(72),
            "greenhouse:acme:2": _scored(30, decline="Manager title"),
        },
    )
    assert result.exit_code == 0, result.output
    assert "  [1/2]  72  Developer 1 · Acme" in result.stdout
    assert "  [2/2]  --  Developer 2 · Acme  declined" in result.stdout
    assert "greenhouse:acme:" not in result.stdout
    assert "Manager title" not in result.stdout


def test_scan_verbose_keeps_id_and_decline_reason(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _scan(
        migrations_dir,
        monkeypatch,
        ["-v", "scan", "--no-discover"],
        {
            "greenhouse:acme:1": _scored(72),
            "greenhouse:acme:2": _scored(30, decline="Manager title"),
        },
    )
    assert result.exit_code == 0, result.output
    assert "  [1/2] greenhouse:acme:1 [score=72] Developer 1" in result.stdout
    assert "  [2/2] greenhouse:acme:2 [DECLINE: Manager title] Developer 2" in result.stdout


def test_scan_error_line_leads_with_title_and_company(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = {
        "greenhouse:acme:1": _scored(72),
        "greenhouse:acme:2": errors.GatewayError("HTTP 400 exceed_context_size_error"),
    }
    result = _scan(migrations_dir, monkeypatch, ["scan", "--no-discover"], outcomes)
    assert "  ! Developer 2 · Acme: HTTP 400" in result.stderr
    assert "greenhouse:acme:2" not in result.stderr


def test_scan_verbose_error_line_keeps_the_id(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = {
        "greenhouse:acme:1": _scored(72),
        "greenhouse:acme:2": errors.GatewayError("HTTP 400 exceed_context_size_error"),
    }
    result = _scan(migrations_dir, monkeypatch, ["-v", "scan", "--no-discover"], outcomes)
    assert "greenhouse:acme:2" in result.stderr


def test_scan_json_stdout_is_unchanged(
    migrations_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _scan(
        migrations_dir,
        monkeypatch,
        ["scan", "--no-discover", "--json"],
        {
            "greenhouse:acme:1": _scored(72),
        },
    )
    out = json.loads(result.stdout)
    assert out["jobs"] == [
        {
            "id": "greenhouse:acme:1",
            "title": "Developer 1",
            "company": "Acme",
            "score": 72,
            "declined": False,
        },
    ]


# --- Phase O2: location, tags, and `list` -------------------------------------


def test_location_joins_the_label() -> None:
    line = job_line(title="Developer", company="Acme", score=80, location="Toronto, ON")
    assert line == " 80  Developer · Acme · Toronto, ON"


def test_tags_follow_the_label_and_survive_the_cut() -> None:
    line = job_line(
        title="Senior " * 30, company="Acme", score=62, tags="drafted · revise", width=70
    )
    assert len(line) <= 70
    assert line.endswith("…  drafted · revise")


def test_list_standard_row_is_one_line_without_id_or_url(migrations_dir: Path) -> None:
    _harness._seed(migrations_dir)
    out = _invoke(["list"]).stdout
    assert "   80  Developer 1 · Acme · Toronto, ON  ship" in out
    assert "greenhouse:acme:1" not in out
    assert "https://example.com/1" not in out


def test_list_verbose_adds_id_and_url_line(migrations_dir: Path) -> None:
    _harness._seed(migrations_dir)
    lines = _invoke(["-v", "list"]).stdout.splitlines()
    i = lines.index("   80  Developer 1 · Acme · Toronto, ON  ship")
    assert lines[i + 1] == "       greenhouse:acme:1  https://example.com/1"


def test_list_applied_row_shows_its_status(migrations_dir: Path) -> None:
    _harness._seed(migrations_dir)
    out = _invoke(["list", "--applied"]).stdout
    assert "   65  Developer 2 · Acme · Toronto, ON  applied" in out


def test_list_footer_is_plain_words_without_zero_counts(migrations_dir: Path) -> None:
    _harness._seed(migrations_dir)
    footer = _invoke(["list"]).stdout.splitlines()[-1]
    assert re.fullmatch(r"This week \(\d{4}-W\d\d\): 2 scanned, 0 declined\.", footer)
    week = _invoke(["list", "--week", "1"]).stdout.splitlines()[-1]
    assert re.fullmatch(r"Week \d{4}-W\d\d: 0 scanned, 0 declined, 1 applied\.", week)


# --- Phase O3: `apply` picker, selection, header, audit line ------------------


def _picker_conn(migrations_dir: Path):
    from jobhunt.config import Config
    from jobhunt.db import connect, upsert_job, write_score
    from jobhunt.models import Job

    _harness._seed(migrations_dir)
    conn = connect(Config().paths.db_path)
    with conn:
        upsert_job(
            conn,
            Job(
                id="lever:globex:9",
                source="lever",
                external_id="9",
                company="Globex",
                title="Web Developer",
                location="Remote, Canada",
                description="x",
                url="https://example.com/9",
            ),
        )
        write_score(
            conn,
            job_id="lever:globex:9",
            score=50,
            reasons=[],
            red_flags=[],
            must_clarify=[],
            model="t",
            prompt_hash="t",
        )
    return conn


def _pick(conn, monkeypatch, capsys, *, verbose: bool) -> list[str]:
    import jobhunt.commands as commands
    from jobhunt.commands import apply_cmd

    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setattr(commands, "require_tty", lambda *_: None)
    monkeypatch.setattr(apply_cmd.typer, "prompt", lambda *a, **k: "")
    apply_cmd._resolve_interactive(conn, min_score=55, include_borderline=True, verbose=verbose)
    return capsys.readouterr().out.splitlines()


def test_picker_rows_are_one_line_without_id(migrations_dir: Path, monkeypatch, capsys) -> None:
    lines = _pick(_picker_conn(migrations_dir), monkeypatch, capsys, verbose=False)
    assert "  [ 1]  80  Developer 1 · Acme · Toronto, ON" in lines
    assert "  [ 2]  50  Web Developer · Globex · Remote, Canada  stretch" in lines
    assert not any("greenhouse:acme:1" in line or "lever:globex:9" in line for line in lines)


def test_picker_verbose_adds_the_id_line(migrations_dir: Path, monkeypatch, capsys) -> None:
    lines = _pick(_picker_conn(migrations_dir), monkeypatch, capsys, verbose=True)
    i = lines.index("  [ 1]  80  Developer 1 · Acme · Toronto, ON")
    assert lines[i + 1] == "        greenhouse:acme:1"


def test_selected_list_is_one_line_per_job(capsys, monkeypatch) -> None:
    from jobhunt.commands.apply_cmd import _print_selected

    monkeypatch.setenv("COLUMNS", "100")
    jobs = [
        {
            "id": "greenhouse:acme:1",
            "source": "greenhouse",
            "score": 80,
            "title": "Developer 1",
            "company": "Acme",
            "location": "Toronto, ON",
        }
    ]
    _print_selected(jobs, verbose=False)
    _print_selected(jobs, verbose=True)
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "",
        "selected 1 job(s):",
        "  •  80  Developer 1 · Acme · Toronto, ON",
        "",
        "selected 1 job(s):",
        "  •  80  Developer 1 · Acme · Toronto, ON",
        "        greenhouse:acme:1",
    ]


def test_job_header_drops_the_id() -> None:
    """The id is not repeated in each job's header; `-v` shows it in the
    selected list printed just before the run."""
    from jobhunt.commands.apply_cmd import _job_header
    from jobhunt.models import Job

    job = Job(
        id="lever:acme:230b8d92",
        source="lever",
        external_id="1",
        title="Shopify Developer",
        company="Acme",
    )
    assert _job_header(job) == "\n=== Shopify Developer · Acme ==="


def _audit(**kw):
    from jobhunt.pipeline.audit import AuditResult

    base = dict(
        keyword_coverage_pct=72,
        matched_keywords=[],
        missing_must_haves=[],
        fabrication_flags=[],
        cover_letter_violations=[],
        alignment_flags=[],
        verdict="revise",
    )
    base.update(kw)
    return AuditResult(**base)


def test_audit_sentence_names_only_nonzero_counts() -> None:
    from jobhunt.commands.apply_cmd import _audit_sentence

    full = _audit(
        missing_must_haves=["a", "b", "c"],
        cover_letter_violations=["x"],
        alignment_flags=["y"],
        specificity_flags=["z", "w"],
    )
    assert _audit_sentence(full) == (
        "    audit: revise · 72% keywords · 3 missing keywords · 1 cover-letter issue"
        " · 1 alignment flag · 2 specificity flags"
    )
    assert _audit_sentence(_audit(verdict="ship", keyword_coverage_pct=100)) == (
        "    audit: ship · 100% keywords"
    )
    assert _audit_sentence(_audit(keyword_coverage_pct=None)) == (
        "    audit: revise · coverage n/a"
    )


def test_warning_summary_reads_as_words() -> None:
    from collections import Counter

    from jobhunt.commands.apply_cmd import _warning_summary

    assert _warning_summary(Counter({"coverage": 2, "specificity": 1})) == (
        "most common warnings: coverage (2), specificity (1)"
    )


# --- Phase O6: printed URLs drop utm_* ----------------------------------------
#
# Adzuna's redirect_url carries utm_source=<API app id>. It stays stored as
# sent (Adzuna attributes clicks with it); only printed copies are cleaned.


def test_display_url_drops_utm_and_keeps_other_params() -> None:
    from jobhunt.commands._job_line import display_url

    assert (
        display_url("https://www.adzuna.ca/details/5955916591?utm_medium=api&utm_source=06287e36")
        == "https://www.adzuna.ca/details/5955916591"
    )
    assert display_url("https://x.com/job?gh_jid=12&utm_source=a&ref=b") == (
        "https://x.com/job?gh_jid=12&ref=b"
    )


def test_display_url_passes_through_plain_and_empty_values() -> None:
    from jobhunt.commands._job_line import display_url

    assert display_url("https://example.com/1") == "https://example.com/1"
    assert display_url("") == ""
    assert display_url(None) == ""


def test_list_verbose_prints_clean_url_and_keeps_the_stored_one(migrations_dir: Path) -> None:
    from jobhunt.config import Config
    from jobhunt.db import connect

    _harness._seed(migrations_dir)
    conn = connect(Config().paths.db_path)
    tracked = "https://www.adzuna.ca/details/1?utm_medium=api&utm_source=06287e36"
    with conn:
        conn.execute("UPDATE jobs SET url = ? WHERE id = 'greenhouse:acme:1'", (tracked,))
    out = _invoke(["-v", "list"]).stdout
    assert "https://www.adzuna.ca/details/1" in out
    assert "06287e36" not in out
    assert (
        conn.execute("SELECT url FROM jobs WHERE id = 'greenhouse:acme:1'").fetchone()[0] == tracked
    )
    conn.close()
