"""Every real prompt in kb/prompts/ renders through its real caller.

Phase V1 (2026-11-12). Unit-test fixtures write their own small prompts, and
those drifted from the real files: from 2026-07-26 the real interview-prep
prompt used {candidate_name} in its USER section, the caller never passed it,
and every live run failed while the suite stayed green. These cases point
each pipeline at the repo's real kb/ and stop at the model call, so a
template variable no caller supplies fails here first.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from jobhunt.config import Config, GatewayConfig, PathsConfig
from jobhunt.models import Job
from jobhunt.pipeline import answer, cover, interview_prep, score, tailor
from jobhunt.pipeline.interview_prep import PrepContext

REPO = Path(__file__).resolve().parents[1]
REAL_KB = REPO / "kb"
FIXTURE_PROFILE = REPO / "tests" / "fixtures" / "profile" / "verified.json"
# A rendered prompt must not keep a bare {name}. Escaped JSON in the
# templates renders as {"...": ...}, which this does not match.
LEFTOVER = re.compile(r"\{[a-z_]+\}")


class _ReachedModelError(Exception):
    """Raised by the stub: the prompt rendered and reached the model call."""


def _cfg(tmp_path: Path) -> Config:
    profile = tmp_path / "profile"
    profile.mkdir()
    shutil.copy(FIXTURE_PROFILE, profile / "verified.json")
    tasks = {t: "test" for t in ("score", "tailor", "cover", "answer")}
    return Config(
        paths=PathsConfig(kb_dir=REAL_KB, profile_dir=profile),
        gateway=GatewayConfig(tasks=tasks),
    )


def _job() -> Job:
    return Job(
        id="t:1",
        source="t",
        external_id="1",
        company="Acme",
        title="Web Developer",
        location="Toronto, ON",
        description="We build Shopify and WordPress sites. " * 30,
    )


async def _score(cfg: Config) -> None:
    await score.score_job(cfg, _job())


async def _tailor(cfg: Config) -> None:
    await tailor._tailor_once(cfg, _job(), revisions="")


async def _cover(cfg: Config) -> None:
    await cover.write_cover(cfg, _job())


async def _answer(cfg: Config) -> None:
    await answer.write_answer(
        cfg, question="Why do you want this role?", jd_context="Shopify work.", max_words=150
    )


async def _interview_prep(cfg: Config) -> None:
    ctx = PrepContext(
        job_id="t:1",
        job_title="Web Developer",
        job_company="Acme",
        job_description="We build Shopify and WordPress sites. " * 30,
        job_url="https://example.com/jobs/1",
        stage="hiring_manager",
    )
    await interview_prep.draft_prep_sections(cfg, ctx=ctx)


CASES = {
    "score": (score, _score),
    "tailor": (tailor, _tailor),
    "cover": (cover, _cover),
    "answer": (answer, _answer),
    "interview-prep": (interview_prep, _interview_prep),
}


def test_every_real_prompt_has_a_case() -> None:
    """A new prompt file must get a case here, or it is not covered."""
    assert {p.stem for p in (REAL_KB / "prompts").glob("*.md")} == set(CASES)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(CASES))
async def test_real_prompt_renders(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, run = CASES[name]
    seen: dict[str, Any] = {}

    async def stub(**kw: Any) -> dict[str, Any]:
        seen.update(kw)
        raise _ReachedModelError

    monkeypatch.setattr(module, "complete_json", stub)
    with pytest.raises(_ReachedModelError):
        await run(_cfg(tmp_path))
    for part in ("system", "user"):
        leftover = LEFTOVER.findall(seen[part])
        assert not leftover, f"{name} {part} prompt kept {leftover}"
