"""Phase 11 tests — apply --top N overlapped pipeline.

Asserts the next job's LLM phase starts BEFORE the current job's IO phase
finishes. Uses sleep-instrumented stubs so timing is deterministic and we
can observe ordering via a shared event list.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


@dataclass
class _Recorder:
    events: list[str] = field(default_factory=list)

    def note(self, label: str) -> None:
        self.events.append(label)


@pytest.mark.asyncio
async def test_next_llm_phase_starts_before_current_io_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the prefetch: when IO for job N is in flight, LLM for job N+1
    has already begun. Without the prefetch (sequential per-job loop), the
    sequence would be llm0 → io0 → llm1 → io1. With prefetch it should be
    llm0 → (llm1 starts) → io0 → (llm1 done) → io1.
    """
    from jobhunt.commands import apply_cmd

    rec = _Recorder()

    async def fake_llm_phase(cfg: Any, job: Any, *, verified: Any, echo: Any = None) -> Any:
        rec.note(f"llm-start:{job.id}")
        await asyncio.sleep(0.05)
        rec.note(f"llm-end:{job.id}")
        # Minimal _LLMPhaseResult-shaped stand-in.
        return _StubPhase(job.id)

    async def fake_io_phase(cfg: Any, job: Any, phase: Any, *, no_browser: bool) -> Any:
        rec.note(f"io-start:{job.id}")
        await asyncio.sleep(0.1)  # IO is the slow side (mimics user-review time)
        rec.note(f"io-end:{job.id}")
        return ("ship", [])

    async def fake_warm(cfg: Any, *, task: str = "score") -> None:
        return None

    monkeypatch.setattr(apply_cmd, "_apply_llm_phase", fake_llm_phase)
    monkeypatch.setattr(apply_cmd, "_apply_io_phase", fake_io_phase)
    monkeypatch.setattr("jobhunt.gateway.warm.warm_model", fake_warm)
    # Stub _row_to_job to just unwrap a fake row.
    monkeypatch.setattr(apply_cmd, "_row_to_job", lambda r: r)

    # Stub the verified.json load by skipping path access — give it a config
    # whose kb_dir resolves nowhere and let the fall-through use empty dict.
    class _FakeCfg:
        class _Paths:
            kb_dir = Path("/nonexistent")

        paths = _Paths

    monkeypatch.setattr(apply_cmd, "load_config", lambda: _FakeCfg)  # unused here

    rows = [_StubJob("job-A"), _StubJob("job-B"), _StubJob("job-C")]

    cfg = _FakeCfg()
    await apply_cmd._apply_each(cfg, rows, no_browser=True)

    # Expected interleaving (timing-based; asyncio scheduling kicks the
    # next-LLM task only at the first await, which is _apply_io_phase):
    #   llm-start:A
    #   llm-end:A
    #   io-start:A           <- the prefetched llm-B task is scheduled here
    #   llm-start:B          <- runs concurrently with io-A's sleep
    #   llm-end:B            <- LLM finishes before IO (LLM=50ms, IO=100ms)
    #   io-end:A
    #   io-start:B
    #   ...
    # The overlap is proven by: B's LLM completes BEFORE A's IO completes.
    idx_llm_end_b = rec.events.index("llm-end:job-B")
    idx_io_end_a = rec.events.index("io-end:job-A")
    assert idx_llm_end_b < idx_io_end_a, (
        f"job-B's LLM phase should finish during job-A's IO phase "
        f"(got: {rec.events})"
    )
    # Sequential (no overlap) would have looked like:
    #   llm-A, llm-A end, io-A, io-A end, llm-B, llm-B end, io-B, io-B end
    # — which we should NOT see.
    sequential_pattern = ["llm-start:job-A", "llm-end:job-A",
                          "io-start:job-A", "io-end:job-A",
                          "llm-start:job-B"]
    sequential_indices = [
        rec.events.index(e) for e in sequential_pattern
    ]
    assert sequential_indices != sorted(sequential_indices), (
        f"pipeline ran sequentially — no overlap detected: {rec.events}"
    )
    # And the final order should still complete all three jobs.
    assert "io-end:job-C" in rec.events


@pytest.mark.asyncio
async def test_continue_prompt_no_stops_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the between-jobs prompt returns 'n', the loop must break and
    the prefetched LLM task must be cancelled — otherwise we'd silently
    burn a tailor+cover on a job the user already opted out of."""
    from jobhunt.commands import apply_cmd

    rec = _Recorder()

    async def fake_llm_phase(cfg: Any, job: Any, *, verified: Any, echo: Any = None) -> Any:
        rec.note(f"llm-start:{job.id}")
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            rec.note(f"llm-cancel:{job.id}")
            raise
        rec.note(f"llm-end:{job.id}")
        return _StubPhase(job.id)

    async def fake_io_phase(cfg: Any, job: Any, phase: Any, *, no_browser: bool) -> Any:
        rec.note(f"io:{job.id}")
        # Tiny yield so the prefetched LLM task for the *next* job gets a
        # chance to start. In production _apply_io_phase has many real
        # awaits (render, browser, input prompt); this mimics that.
        await asyncio.sleep(0.05)
        return ("ship", [])

    async def fake_warm(cfg: Any, *, task: str = "score") -> None:
        return None

    async def fake_prompt(next_job: Any) -> bool:
        rec.note(f"prompt:{next_job.id}")
        return False  # user says "no, stop"

    monkeypatch.setattr(apply_cmd, "_apply_llm_phase", fake_llm_phase)
    monkeypatch.setattr(apply_cmd, "_apply_io_phase", fake_io_phase)
    monkeypatch.setattr("jobhunt.gateway.warm.warm_model", fake_warm)
    monkeypatch.setattr(apply_cmd, "_row_to_job", lambda r: r)
    monkeypatch.setattr(apply_cmd, "_prompt_continue", fake_prompt)
    # Force TTY check to pass so the prompt actually fires.
    monkeypatch.setattr(apply_cmd.sys.stdin, "isatty", lambda: True)

    class _FakeCfg:
        class _Paths:
            kb_dir = Path("/nonexistent")

        paths = _Paths

    cfg = _FakeCfg()
    rows = [_StubJob("A"), _StubJob("B"), _StubJob("C")]
    await apply_cmd._apply_each(cfg, rows, no_browser=True)

    # job A processed (llm + io), prompt asked about B → user said no →
    # B's prefetched LLM cancelled, C never started.
    assert "io:A" in rec.events
    assert "prompt:B" in rec.events
    assert "llm-cancel:B" in rec.events
    assert "io:B" not in rec.events
    assert "llm-start:C" not in rec.events


@dataclass
class _StubJob:
    id: str
    title: str = "Stub"
    company: str = "Stub Co"
    description: str = ""
    url: str = ""


@dataclass
class _StubPhase:
    job_id: str
    early_exit: bool = False

    @property
    def topics(self) -> list[str]:
        return []

    @property
    def audit_result(self) -> Any:
        class _R:
            verdict = "ship"
        return _R()
