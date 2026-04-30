"""Cover letter pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from jobhunt.config import Config
from jobhunt.errors import PipelineError
from jobhunt.gateway import complete_json, load_prompt
from jobhunt.models import Job
from jobhunt.pipeline.score import MAX_DESC_CHARS, truncate


@dataclass
class CoverLetter:
    salutation: str
    body: list[str]
    sign_off: str
    model: str

    def to_markdown(self) -> str:
        parts = [self.salutation, ""]
        parts.extend(p.rstrip() + "\n" for p in self.body)
        parts.append(self.sign_off)
        return "\n".join(parts).strip() + "\n"


async def write_cover(cfg: Config, job: Job) -> CoverLetter:
    if not job.description:
        raise PipelineError(f"job {job.id} has no description")
    verified_path = cfg.paths.kb_dir / "profile" / "verified.json"
    if not verified_path.is_file():
        raise PipelineError(f"missing {verified_path} — run `job-seeker convert-resume`")

    prompt = load_prompt(cfg.paths.kb_dir, "cover")
    user = prompt.render_user(
        verified_facts=verified_path.read_text(encoding="utf-8"),
        title=job.title or "(unknown)",
        company=job.company or "(unknown)",
        location=job.location or "(unknown)",
        description=truncate(job.description, MAX_DESC_CHARS),
    )
    model = cfg.gateway.tasks.get(prompt.task) or cfg.gateway.tasks["cover"]
    raw = await complete_json(
        base_url=cfg.gateway.base_url,
        model=model,
        system=prompt.system,
        user=user,
        schema=prompt.schema,
        temperature=prompt.temperature,
    )
    body = raw["body"]
    if isinstance(body, str):
        body = [body]
    return CoverLetter(
        salutation=str(raw.get("salutation") or "Dear Hiring Team,"),
        body=[str(p).strip() for p in body],
        sign_off=str(raw.get("sign_off") or "Best,\nCasey Hsu"),
        model=model,
    )
