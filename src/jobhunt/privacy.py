"""Keep personal files in the app folder out of git (2026-11-17).

Config, secrets, the database, the profile, resumes and tailored documents
live inside the app folder (`config.app_root()`), so the app works the same
for anyone who downloads it, on Linux or Windows. Privacy means they can never
be committed: on every run the app makes sure `.gitignore` covers them, and it
refuses to run only when git already tracks one, which an ignore rule cannot
undo. A folder without git (a zip download) just gets the `.gitignore`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from jobhunt.errors import PrivacyError

# Invocations that only report and never touch private data, so a refused
# user can still inspect the settings that cause the refusal.
_EXEMPT = (("config", "show"), ("config", "path"))

# Personal files, as .gitignore patterns. The .bak and .tmp siblings come
# from `_config_write`, which snapshots config.toml before every write.
IGNORED = (
    "config.toml",
    "config.toml.bak",
    "config.toml.tmp",
    "secrets.toml",
    "data/",
    "*.docx",
    "*.pdf",
    "*.db",
)
_BLOCK_HEADER = "# jobhunt: personal files, added automatically"


def is_exempt(argv: list[str]) -> bool:
    return "--help" in argv or tuple(argv[:2]) in _EXEMPT


def ensure_gitignore(root: Path) -> list[str]:
    """Append every missing `IGNORED` entry to `root/.gitignore`, creating the
    file when absent. Lines the user wrote are never changed. Returns the
    entries added, so a second run returns []."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = {line.strip() for line in text.splitlines()}
    missing = [entry for entry in IGNORED if entry not in present]
    if not missing:
        return []
    if text and not text.endswith("\n"):
        text += "\n"
    block = "\n".join([_BLOCK_HEADER, *missing]) + "\n"
    path.write_text(text + ("\n" if text else "") + block, encoding="utf-8")
    return missing


def _tracked(root: Path) -> list[str]:
    """Private files git already tracks in `root`. Read-only `git ls-files`.
    Empty when `root` is not a checkout or git is not installed."""
    git = shutil.which("git")
    if git is None or not (root / ".git").exists():
        return []
    specs = [f":(glob){e}**" if e.endswith("/") else f":(glob)**/{e}" for e in IGNORED]
    proc = subprocess.run(
        [git, "-C", str(root), "ls-files", "-z", "--", *specs],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PrivacyError(f"could not check which files git tracks in {root}: {proc.stderr}")
    return [name for name in proc.stdout.split("\0") if name]


def check_tracked(root: Path) -> None:
    """Raise `PrivacyError` naming every private file git tracks in `root`."""
    tracked = _tracked(root)
    if tracked:
        names = "\n  - ".join(tracked)
        raise PrivacyError(
            f"git tracks personal files in {root}:\n  - {names}\n"
            f"Stop tracking them from {root} (the files stay on disk), then commit:\n"
            "  git rm --cached -- " + " ".join(f'"{n}"' for n in tracked)
        )
