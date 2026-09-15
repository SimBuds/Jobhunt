"""Did the tailoring pass keep the numbers?

A tailored resume can satisfy every honesty invariant and still come out worse
than the baseline, by rewriting "cut page load time 30%" into "improved site
performance". Nothing is fabricated, keyword coverage may even rise, and the
document gets measurably weaker: the LLM ranking layer rewards quantified,
specific claims, and the human penalty for prose that reads as AI-written is
aimed squarely at generic phrasing.

The existing guards cannot see this. `_enforce_no_fabrication` only asks
whether a claim is *permitted*; keyword coverage only asks whether a term is
*present*. Neither notices a metric going missing, because a dropped number
violates no rule.

This module measures retention: of the figures the verified profile makes
available for a role, how many survived into the tailored bullets. Deterministic
regex + counters, matching `pipeline.audit`. No LLM call belongs here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from jobhunt.pipeline.tailor import TailoredResume

# Below this share of available figures retained, the verdict drops to
# `revise`. Not 100: the tailor legitimately selects a subset of bullets per
# role, so some figures are expected to fall away with the bullets that carried
# them. This threshold asks that the rewrite kept the resume's quantified
# character, not every individual number.
MIN_METRIC_RETENTION_PCT = 40

# A role that had several figures to work with and shipped none of them is the
# specific failure this module exists to catch, so it flags on its own
# regardless of the overall percentage.
_BARE_ROLE_THRESHOLD = 2

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _is_year(value: str) -> bool:
    """Four-digit values in a plausible calendar range are dates, not
    achievements. Counting them would let a role score full retention on the
    strength of its own date line."""
    if len(value) != 4 or "." in value:
        return False
    return 1900 <= int(value) <= 2100


def metrics(text: str) -> set[str]:
    """Quantified figures in a bullet, normalized so "1,100" and "1100" are one
    figure. Units are deliberately not captured: "30%" and "30 percent" are the
    same claim surviving, and the point is whether the number came through."""
    out: set[str] = set()
    for raw in _NUM_RE.findall(text):
        value = raw.replace(",", "").rstrip(".")
        if not value or _is_year(value):
            continue
        out.add(value)
    return out


@dataclass(frozen=True)
class SpecificityReport:
    available: int
    retained: int
    pct: int | None  # None when the profile offered no figures to retain
    bare_roles: list[str]  # roles with figures available that kept none

    @property
    def flags(self) -> list[str]:
        out: list[str] = []
        for employer in self.bare_roles:
            out.append(
                f"tailored bullets for {employer} dropped every quantified "
                "figure the profile had for that role"
            )
        if self.pct is not None and self.pct < MIN_METRIC_RETENTION_PCT:
            out.append(
                f"quantified-figure retention {self.pct}% is below "
                f"{MIN_METRIC_RETENTION_PCT}% — the rewrite generalized away "
                "specifics the ranking layer rewards"
            )
        return out


def specificity_report(tailored: TailoredResume, verified: dict[str, Any]) -> SpecificityReport:
    """Compare figures available per verified role against those that survived.

    Roles are paired on the exact `(employer, dates)` tuple, the same key
    `_enforce_no_fabrication` validates, so by the time this runs every tailored
    role is known to have a verified counterpart.
    """
    by_key: dict[tuple[str, str], list[str]] = {
        (r["employer"], r["dates"]): list(r.get("bullets", []))
        for r in verified.get("work_history", [])
    }

    available = 0
    retained = 0
    bare_roles: list[str] = []

    for role in tailored.roles:
        source = by_key.get((role.employer, role.dates))
        if source is None:
            continue  # fabrication guard owns this failure; don't double-report
        source_metrics: set[str] = set()
        for bullet in source:
            source_metrics |= metrics(bullet)
        if not source_metrics:
            continue
        shipped: set[str] = set()
        for bullet in role.bullets:
            shipped |= metrics(bullet)
        kept = source_metrics & shipped
        available += len(source_metrics)
        retained += len(kept)
        if not kept and len(source_metrics) >= _BARE_ROLE_THRESHOLD:
            bare_roles.append(role.employer)

    pct = round(100 * retained / available) if available else None
    return SpecificityReport(available=available, retained=retained, pct=pct, bare_roles=bare_roles)
