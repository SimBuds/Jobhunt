"""Probe each candidate seed slug once and print the verified subset.

Use this before committing changes to kb/seeds/gta-employers.toml. Unverified
seeds become every new user's first impression of broken slugs; verifying
once at curation time prevents that.

Usage:
    uv run python scripts/verify_seeds.py

Edit the CANDIDATES dict below to add new candidate slugs to vet, then run
the script. Output: a TOML block ready to paste into the seed file.

Workday is excluded — it needs the tenant:host:site triple, which is easier
to gather manually (you need to find the URL anyway). Add Workday seeds by
hand after running `jobhunt add <workday-url>` once.
"""

from __future__ import annotations

import asyncio

import httpx

from jobhunt.discover.probe import _probe_one
from jobhunt.http import DEFAULT_UA, RateLimiter

# Candidate slugs sourced from public knowledge of GTA tech employers.
# These are NOT yet verified — running this script narrows to the live subset.
CANDIDATES: dict[str, list[str]] = {
    "greenhouse": [
        "shopify",
        "1password",
        "wealthsimple",
        "faire",
        "konradgroup",
        "hootsuite",
        "lightspeedhq",
        "vidyard",
        "tophat",
        "getjobber",
        "ada",
        "klue",
        "thinkific",
        "ritual",
        "ecobee",
        "wattpad",
        "loopio",
        "achievers",
        "trulioo",
        "kira",
        "freshbooks",
        "bench",
        "drop",
        "wave",
        "fiix",
        "league",
        "shakepay",
        "borrowell",
    ],
    "lever": [
        "benchsci",
        "fellow",
        "kovrr",
        "voiceflow",
        "deeplearningai",
    ],
    "ashby": [
        "cohere",
        "harvey",
        "mercor",
        "sentry",
        "klue",
    ],
    "smartrecruiters": [
        # Hand-add after `jobhunt discover slugs` confirms — SmartRecruiters
        # slugs are case-sensitive and tend to be inconsistent.
    ],
}


async def main() -> None:
    limiter = RateLimiter(rate_per_sec=1.0)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        verified: dict[str, list[tuple[str, int]]] = {}
        for ats, slugs in CANDIDATES.items():
            print(f"\n=== {ats} ({len(slugs)} candidates) ===")
            verified[ats] = []
            for slug in slugs:
                outcome = await _probe_one(client, limiter, slug, ats, slug)
                marker = (
                    "ok  " if outcome.status == 200
                    else "404 " if outcome.status == 404
                    else "err "
                )
                count = outcome.job_count if outcome.job_count is not None else "-"
                print(f"  {marker} {slug:<25} jobs={count}")
                if outcome.status == 200:
                    verified[ats].append((slug, outcome.job_count or 0))

    print("\n\n# ===== verified TOML block (paste into kb/seeds/gta-employers.toml) =====")
    for ats, hits in verified.items():
        if not hits:
            print(f"{ats} = []")
            continue
        # Sort by job count desc — bigger boards first for first-run signal.
        hits.sort(key=lambda h: -h[1])
        joined = ", ".join(f'"{slug}"' for slug, _ in hits)
        print(f"{ats} = [{joined}]")


if __name__ == "__main__":
    asyncio.run(main())
