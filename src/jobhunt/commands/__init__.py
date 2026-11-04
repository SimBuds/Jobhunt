from __future__ import annotations

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
from jobhunt.errors import ConfigError, GatewayUnavailableError, InputError, JobHuntError

# A config/input fault (10) or an unreachable router (13) fails the next job
# exactly as it failed this one, so a batch stops on it. A stage failure (12)
# is about one posting and stays a per-job skip.
_RUN_WIDE_EXIT_CODES = frozenset({ConfigError.exit_code, GatewayUnavailableError.exit_code})


def stops_batch(e: JobHuntError) -> bool:
    """True when `e` would fail every remaining job in a batch the same way."""
    return e.exit_code in _RUN_WIDE_EXIT_CODES


def ensure_profile(cfg: Config) -> None:
    """Bail with a friendly message if `convert-resume` hasn't been run yet.

    `<profile_dir>/verified.json` is the source-of-truth that scoring, tailoring,
    and listing all depend on. Running scan/list/apply without it produces
    confusing downstream errors, so guard at the command entry point.
    """
    verified = cfg.paths.profile_dir / "verified.json"
    if not verified.is_file():
        raise InputError(
            f"missing {verified}. Run `jobhunt convert-resume` first to parse "
            "your baseline resume."
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
    "stops_batch",
]
