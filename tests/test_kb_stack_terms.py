"""The public knowledge base must not claim a stack the profile dropped.

Phase 16b (2026-11-02): `kb/policies/tailoring-rules.md`, injected into every
tailor and cover call, told the model to surface "Ollama" and "GPU-tuning"
after the profile moved to llama.cpp and dropped both. The model then reached
for claims the fabrication guards reject, or, in covers, never checked.

Ollama may appear in exactly one place: `kb/prompts/score.md`'s "AI SDKs /
hosts" peer line, because postings still ask for it and that line lets an
Ollama ask credit llama.cpp work. `kb/profile/` is personal and gitignored,
so it is not scanned.
"""

from __future__ import annotations

from pathlib import Path

KB = Path(__file__).resolve().parent.parent / "kb"
SCORE_PROMPT = KB / "prompts" / "score.md"


def _public_kb_files() -> list[Path]:
    return sorted(
        p for p in KB.rglob("*") if p.is_file() and "profile" not in p.relative_to(KB).parts
    )


def test_ollama_appears_only_on_the_score_peer_line() -> None:
    hits = [
        f"{p.relative_to(KB)}:{n}"
        for p in _public_kb_files()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "ollama" in line.lower() and not (p == SCORE_PROMPT and "AI SDKs / hosts" in line)
    ]
    assert hits == []


def test_score_peer_line_credits_llama_cpp_for_ollama() -> None:
    [line] = [
        line
        for line in SCORE_PROMPT.read_text(encoding="utf-8").splitlines()
        if "AI SDKs / hosts" in line
    ]
    assert "Ollama" in line
    assert "llama.cpp" in line


def test_tailoring_policy_drops_gpu_tuning() -> None:
    policy = (KB / "policies" / "tailoring-rules.md").read_text(encoding="utf-8")
    assert "gpu-tuning" not in policy.lower()
