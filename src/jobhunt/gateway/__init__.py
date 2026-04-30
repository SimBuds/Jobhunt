"""Local LLM gateway. All structured calls go through `complete_json`."""

from jobhunt.gateway.client import complete_json
from jobhunt.gateway.prompts import Prompt, load_prompt

__all__ = ["complete_json", "Prompt", "load_prompt"]
