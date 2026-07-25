"""Locate the baseline resume without demanding one exact filename.

The path used to be hard-coded to `Baseline_Resume.docx`. Renaming the file —
an ordinary thing to do while rewriting a resume — broke `convert-resume` and
`setup` with a file-not-found. This module matches any root-level `.docx` /
`.pdf` whose name contains "resume" instead.

The search is deliberately **non-recursive**. `data/resumes/` holds the
generated lane resumes and `data/applications/<id>/` holds tailored per-job
copies, all of which have "Resume" in the name. Recursing would let a
generated artifact become the source of truth for regenerating itself.
"""

from __future__ import annotations

from pathlib import Path

from jobhunt.errors import PipelineError

RESUME_SUFFIXES = (".docx", ".pdf")


def _candidates(root: Path) -> list[Path]:
    hits = [
        p
        for p in root.iterdir()
        if p.is_file()
        and p.suffix.lower() in RESUME_SUFFIXES
        and "resume" in p.name.lower()
        # Word writes `~$Name.docx` lock files alongside an open document.
        and not p.name.startswith("~$")
    ]
    return sorted(hits, key=_rank)


def _rank(path: Path) -> tuple[int, int, float, str]:
    """Sort key: lower is better.

    1. a name containing "baseline" wins — it is the documented convention and
       the only signal the user has to express intent;
    2. `.docx` beats `.pdf`, since only .docx can actually be parsed today;
    3. most-recently-modified beats older, so a fresh rewrite is picked up;
    4. name, purely to make ties deterministic across filesystems.
    """
    return (
        0 if "baseline" in path.name.lower() else 1,
        RESUME_SUFFIXES.index(path.suffix.lower()),
        -path.stat().st_mtime,
        path.name,
    )


def find_baseline_resume(
    root: Path | None = None, *, explicit: Path | None = None
) -> Path:
    """Return the resume to parse.

    An `explicit` path always wins and is returned even if it does not match
    the naming pattern — the user asked for that file by name.
    """
    if explicit is not None:
        if not explicit.is_file():
            raise PipelineError(f"resume not found: {explicit}")
        return explicit

    root = root or Path()
    if not root.is_dir():
        raise PipelineError(f"not a directory: {root}")

    hits = _candidates(root)
    if not hits:
        raise PipelineError(
            f"no resume found in {root.resolve()}. Expected a .docx or .pdf "
            "with 'resume' in the filename (e.g. Baseline_Resume.docx), "
            "or pass --docx <path>."
        )
    return hits[0]


def describe_choice(chosen: Path, root: Path | None = None) -> str:
    """One line naming the pick, and the alternatives it beat.

    Silently choosing among several resumes is worse than choosing loudly:
    the whole verified-facts pipeline downstream treats this file as truth.
    """
    others = [p.name for p in _candidates(root or Path()) if p != chosen]
    if not others:
        return f"resume: {chosen}"
    return f"resume: {chosen}  (also found: {', '.join(others)})"
