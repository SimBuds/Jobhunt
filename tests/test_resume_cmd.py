"""`jobhunt resume` — lane-brief loading + base-resume rendering.

The LLM is never called: rendering tests monkeypatch
`tailor_resume_with_retry` to return a fixed `TailoredResume` (pattern from
tests/test_tailor_retry.py). Brief-parsing tests run against the real
kb/lanes files so a malformed brief fails CI, not a live run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobhunt.cli import app
from jobhunt.commands import resume_cmd
from jobhunt.commands.resume_cmd import discover_lanes, lane_job, load_lane_brief
from jobhunt.config import PipelineConfig
from jobhunt.pipeline.tailor import TailoredCategory, TailoredResume, TailoredRole

REPO_KB = Path(__file__).resolve().parent.parent / "kb"
REPO_LANES = discover_lanes(REPO_KB)


# --- lane briefs (real kb files) --------------------------------------------


@pytest.mark.parametrize("focus", sorted(REPO_LANES))
def test_lane_brief_parses(focus: str) -> None:
    brief = load_lane_brief(REPO_KB, focus)
    assert brief.title
    assert brief.company
    # Stay above PipelineConfig.thin_jd_chars so these briefs are never
    # treated as signal-poor JDs if reused elsewhere.
    assert len(brief.description) > PipelineConfig().thin_jd_chars


@pytest.mark.parametrize("focus", sorted(REPO_LANES))
def test_lane_job_is_valid(focus: str) -> None:
    job = lane_job(load_lane_brief(REPO_KB, focus))
    assert job.id == f"lane-{REPO_LANES[focus].slug}"
    assert job.source == "lane"
    assert job.description


def test_unknown_lane_raises_and_lists_what_exists(tmp_path: Path) -> None:
    """With lanes discovered from disk, an unknown focus reports the real set.

    The old hardcoded LANES dict could only ever fail with "missing lane
    brief"; now the failure names what the user actually has, which is the
    useful message when their lanes aren't the author's two.
    """
    from jobhunt.errors import PipelineError

    with pytest.raises(PipelineError, match="unknown lane 'ai' — found: none"):
        load_lane_brief(tmp_path, "ai")

    lanes = tmp_path / "lanes"
    lanes.mkdir()
    (lanes / "data-engineering.md").write_text(
        "---\ntitle: Data Engineer\n---\n\nPipelines.\n", encoding="utf-8"
    )
    with pytest.raises(PipelineError, match="found: data"):
        load_lane_brief(tmp_path, "ai")


def test_lanes_are_discovered_from_disk(tmp_path: Path) -> None:
    """Any candidate's lane slugs work, not just `ai` and `cms`."""
    from jobhunt.commands.resume_cmd import discover_lanes

    lanes = tmp_path / "lanes"
    lanes.mkdir()
    for slug in ("data-engineering", "platform-sre", "seo-technical"):
        (lanes / f"{slug}.md").write_text(
            f"---\ntitle: {slug}\n---\n\nBody.\n", encoding="utf-8"
        )

    got = discover_lanes(tmp_path)

    assert set(got) == {"data", "platform", "seo"}
    assert got["data"].slug == "data-engineering"
    # Known acronyms upper-case; other segments title-case.
    assert got["seo"].label == "SEO_Technical"
    assert got["platform"].label == "Platform_SRE"
    assert got["data"].label == "Data_Engineering"


def test_colliding_slug_heads_fall_back_to_the_full_slug(tmp_path: Path) -> None:
    """Two lanes sharing a first segment must both stay reachable."""
    from jobhunt.commands.resume_cmd import discover_lanes

    lanes = tmp_path / "lanes"
    lanes.mkdir()
    for slug in ("ai-automation", "ai-research", "cms-ecommerce"):
        (lanes / f"{slug}.md").write_text(
            f"---\ntitle: {slug}\n---\n\nBody.\n", encoding="utf-8"
        )

    got = discover_lanes(tmp_path)

    assert set(got) == {"ai-automation", "ai-research", "cms"}


def test_brief_without_frontmatter_raises(tmp_path: Path) -> None:
    from jobhunt.errors import PipelineError

    lanes = tmp_path / "lanes"
    lanes.mkdir()
    (lanes / "ai-automation.md").write_text("no frontmatter here")
    with pytest.raises(PipelineError, match="no frontmatter"):
        load_lane_brief(tmp_path, "ai")


# --- command ----------------------------------------------------------------


def test_unknown_focus_exits_2() -> None:
    result = CliRunner().invoke(app, ["resume", "--focus", "bogus"])
    assert result.exit_code == 2
    assert "unknown focus" in result.output


def _fixed_tailored() -> TailoredResume:
    return TailoredResume(
        summary="Full-stack JavaScript/TypeScript developer.",
        skills_categories=[TailoredCategory(name="Core", items=["TypeScript", "React"])],
        roles=[
            TailoredRole(
                title="CMS / E-commerce Developer",
                employer="Atelier Dacko, Custom Jewelry Brand",
                dates="(Apr 2023 – Present)",
                bullets=["Built a 16+ page Shopify storefront."],
            )
        ],
        certifications=[],
        education=[],
        coursework=[],
        model="stub",
    )


def test_renders_docx_for_one_lane(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_config_dir / "data"
    kb_dir = tmp_config_dir / "kb"
    jh = tmp_config_dir / "jobhunt"
    jh.mkdir()
    (jh / "config.toml").write_text(
        "[paths]\n"
        f'data_dir = "{data_dir}"\n'
        f'kb_dir = "{kb_dir}"\n'
        "[applicant]\n"
        'full_name = "Casey Hsu"\n'
        'email = "x@y.com"\n'
    )
    (kb_dir / "profile").mkdir(parents=True)
    (kb_dir / "profile" / "verified.json").write_text(
        json.dumps({"name": "Casey Hsu", "contact_line": "Toronto | x@y.com"})
    )
    lanes = kb_dir / "lanes"
    lanes.mkdir()
    (lanes / "ai-automation.md").write_text(
        "---\ntitle: AI Automation Developer\ncompany: Base\n---\n" + "x" * 900
    )

    async def fake_retry(*_: object, **__: object) -> tuple[TailoredResume, list[str], int]:
        return _fixed_tailored(), [], 1

    monkeypatch.setattr(resume_cmd, "tailor_resume_with_retry", fake_retry)

    result = CliRunner().invoke(app, ["resume", "--focus", "ai"])
    assert result.exit_code == 0, result.output

    out_dir = data_dir / "resumes"
    assert (out_dir / "Casey_Hsu_Resume_AI_Automation.docx").is_file()
    tailored_json = json.loads((out_dir / "tailored-ai-automation.json").read_text())
    assert tailored_json["summary"].startswith("Full-stack")
