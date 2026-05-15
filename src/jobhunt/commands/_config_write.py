"""Atomic config.toml write helper shared by `discover slugs --apply`,
`jobhunt add`, and `config seed --apply`.

Writes are not comment-preserving: tomli_w drops any user-added comments on
the file. Callers should document this near their own UX. The .bak snapshot
is overwritten on each call — a second writer in the same session loses the
prior .bak. Single-user CLI, so this is an accepted tradeoff."""

from __future__ import annotations

import os

import tomli_w

from jobhunt.config import Config, _to_toml_dict, config_path
from jobhunt.errors import JobHuntError


def write_config_atomically(cfg: Config) -> None:
    """Serialize `cfg` to the user's config.toml. Creates a .bak snapshot of
    the prior file, writes to a .tmp sibling, then atomically renames over."""
    path = config_path()
    if not path.exists():
        raise JobHuntError(f"config.toml not found at {path}")

    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_bytes(path.read_bytes())

    serialized = tomli_w.dumps(_to_toml_dict(cfg.model_dump(mode="json")))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(serialized)
    os.replace(tmp, path)


__all__ = ["write_config_atomically"]
