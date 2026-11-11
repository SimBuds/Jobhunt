from __future__ import annotations

import sys

from jobhunt.commands import (
    add_cmd,
    analyze_cmd,
    apply_cmd,
    config_cmd,
    convert_resume_cmd,
    db_cmd,
    discover_cmd,
    list_cmd,
    scan_cmd,
    setup_cmd,
)
from jobhunt.config import Config
from jobhunt.errors import (
    ConfigError,
    GatewayUnavailableError,
    InputError,
    JobHuntError,
    NeedsPersonError,
)

# A config/input fault (10) or an unreachable router (13) fails the next job
# exactly as it failed this one, so a batch stops on it. A stage failure (12)
# is about one posting and stays a per-job skip.
_RUN_WIDE_EXIT_CODES = frozenset({ConfigError.exit_code, GatewayUnavailableError.exit_code})


def stops_batch(e: JobHuntError) -> bool:
    """True when `e` would fail every remaining job in a batch the same way."""
    return e.exit_code in _RUN_WIDE_EXIT_CODES


def require_tty(reason: str) -> None:
    """Exit 11 at once when no person can answer a prompt.

    Without a terminal a prompt either aborts (stdin at /dev/null) or waits
    forever (an open pipe nobody writes to), so an automated caller would
    read a bug or hang. `reason` says what needs a person and how to skip it.
    """
    if sys.stdin is None or not sys.stdin.isatty():
        raise NeedsPersonError(f"{reason}, and there is no terminal to ask on.")


def ensure_profile(cfg: Config) -> None:
    """Bail with a friendly message if `convert-resume` hasn't been run yet.

    `<profile_dir>/verified.json` is the source-of-truth that scoring, tailoring,
    and listing all depend on. Running scan/list/apply without it produces
    confusing downstream errors, so guard at the command entry point.
    """
    verified = cfg.paths.profile_dir / "verified.json"
    if not verified.is_file():
        raise InputError(
            f"missing {verified}. Run `jobhunt convert-resume` first to parse your baseline resume."
        )


__all__ = [
    "add_cmd",
    "analyze_cmd",
    "apply_cmd",
    "config_cmd",
    "convert_resume_cmd",
    "db_cmd",
    "discover_cmd",
    "ensure_profile",
    "list_cmd",
    "scan_cmd",
    "setup_cmd",
    "require_tty",
    "stops_batch",
]
