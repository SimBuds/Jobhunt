"""Refuse to run while personal data sits inside the repository checkout.

Personal data (the profile, the database, rendered documents, the browser
profile, the resume) lives in the XDG data directory. A path that resolves
into the checkout, a leftover `kb/profile/` or `data/` from before the
2026-11 migration, or a resume file in the repo root would put it one
`git add` away from a public repository, so the CLI stops and says why
instead of writing anything.
"""

from __future__ import annotations

from pathlib import Path

from jobhunt.config import Config
from jobhunt.errors import PrivacyError
from jobhunt.resume.locate import _candidates

# Invocations that only report and never touch private data, so a refused
# user can still inspect the settings that cause the refusal.
_EXEMPT = (("config", "show"), ("config", "path"))


def _find_repo_root(start: Path) -> Path | None:
    """The checkout holding `start`: the nearest ancestor with a `.git`.
    None for an installed copy, which has no checkout to protect."""
    for parent in start.resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def repo_root() -> Path | None:
    return _find_repo_root(Path(__file__))


def is_exempt(argv: list[str]) -> bool:
    return "--help" in argv or tuple(argv[:2]) in _EXEMPT


def check_private_paths(cfg: Config, root: Path | None) -> None:
    """Raise `PrivacyError` naming every piece of private data in `root`."""
    if root is None:
        return
    root = root.resolve()
    problems: list[str] = []
    configured = {
        "data_dir": cfg.paths.data_dir,
        "db_path": cfg.paths.db_path,
        "profile_dir": cfg.paths.profile_dir,
        "user_data_dir": cfg.browser.user_data_dir,
    }
    for name, path in configured.items():
        if path.resolve().is_relative_to(root):
            problems.append(f"{name} = {path} is inside the repo")
    for leftover in (root / "kb" / "profile", root / "data"):
        if leftover.exists():
            problems.append(f"{leftover} still exists in the repo")
    for resume in _candidates(root):
        problems.append(
            f"{resume} is a resume in the repo root: move it to "
            f"{cfg.paths.profile_dir} and pass --docx if it lives elsewhere"
        )
    if problems:
        raise PrivacyError(
            f"personal data inside the repository checkout {root}:\n  - "
            + "\n  - ".join(problems)
            + "\nMove it to the private data directory and drop any [paths] "
            'override that points into the repo (README: "Private data").'
        )
