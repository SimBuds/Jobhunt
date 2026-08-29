"""Applicant-identity helpers shared by the prompt-building pipelines.

Prompts name the applicant rather than saying "the candidate". That is not a
stylistic preference: replacing the name with an abstract noun across the
prompt library was measured on the golden set and cost 17 points of keyword
coverage, because the model binds instructions to a concrete referent more
reliably (IMPLEMENT.md Phase A13). Injecting the *configured* name keeps that
grounding while letting the library work for whoever's profile is loaded.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FALLBACK_NAME = "the candidate"


def first_name(full_name: str) -> str:
    """First name, in prose case, for interpolation into prompt text.

    Resume headers are routinely all-caps ("JANE DEV"), which reads as
    shouting inside an instruction, and the prompts were written around a bare
    first name. Reproducing that surface form exactly is the point: the
    rendered prompt must stay byte-identical to the hard-coded version it
    replaces, so the golden-set numbers cannot move.
    """
    raw = (full_name or "").strip()
    if not raw:
        return FALLBACK_NAME
    first = raw.split()[0]
    return first.capitalize() if first.isupper() else first


def candidate_name(verified: Mapping[str, Any]) -> str:
    """`first_name` sourced from a parsed verified-profile mapping."""
    return first_name(str(verified.get("name") or ""))


def display_name(verified: Mapping[str, Any]) -> str:
    """Full name in prose case, for the few prompt lines that use both names.

    Resume headers are usually all-caps ("JANE DEV"); only those are
    re-cased, so a name that is already mixed-case ("Jane McDonald") is left
    exactly as the profile spells it.
    """
    raw = str(verified.get("name") or "").strip()
    if not raw:
        return FALLBACK_NAME
    return " ".join(t.capitalize() if t.isupper() else t for t in raw.split())


def render_policy(policy: str, *, name: str) -> str:
    """Substitute the applicant name into injected policy text.

    `kb/policies/tailoring-rules.md` reaches the model as a *value* passed to
    `render_user`, not as part of the prompt template, so `str.format` on the
    template never expands placeholders inside it — the policy has to be
    rendered separately.

    The file carries no other braces, so a plain format is safe; an unexpected
    brace raises loudly here rather than silently mangling the honesty rules.
    """
    if "{" not in policy:
        return policy
    return policy.format(candidate_name=name)
