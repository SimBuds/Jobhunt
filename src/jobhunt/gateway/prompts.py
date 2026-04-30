"""Prompt loader. Reads markdown files from kb/prompts/ with TOML/YAML-ish frontmatter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter  # type: ignore[import-untyped]

from jobhunt.errors import PipelineError


@dataclass
class Prompt:
    name: str
    task: str  # logical task name -> resolves to a model via gateway.tasks
    temperature: float
    system: str
    user_template: str
    schema: dict[str, Any]

    def render_user(self, **vars: Any) -> str:
        try:
            return self.user_template.format(**vars)
        except KeyError as e:
            raise PipelineError(f"prompt {self.name!r} missing variable: {e}") from e


def load_prompt(kb_dir: Path, name: str) -> Prompt:
    path = kb_dir / "prompts" / f"{name}.md"
    if not path.is_file():
        raise PipelineError(f"prompt not found: {path}")
    post = frontmatter.load(path)
    fm = post.metadata
    body = post.content

    task = fm.get("task", name)
    temperature = float(fm.get("temperature", 0.0))
    schema = fm.get("schema")
    if not isinstance(schema, dict):
        raise PipelineError(f"prompt {name!r} missing 'schema' frontmatter")

    if "## SYSTEM" not in body or "## USER" not in body:
        raise PipelineError(f"prompt {name!r} must have ## SYSTEM and ## USER sections")
    _, after_system = body.split("## SYSTEM", 1)
    system_part, user_part = after_system.split("## USER", 1)
    return Prompt(
        name=name,
        task=task,
        temperature=temperature,
        system=system_part.strip(),
        user_template=user_part.strip(),
        schema=schema,
    )
