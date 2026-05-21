"""Probe a MaRS / Toronto-AI-startup candidate list and emit the verified subset.

Companion to `verify_seeds.py`. Where that script targets established GTA tech
employers (Shopify, Wealthsimple, Faire, etc.), this one targets the Toronto
AI / LLM / agentic / ML-startup space — MaRS Discovery District tenants,
OneEleven alumni, Vector Institute-adjacent shops, and BetaKit-tracked
generative-AI startups. This is Casey's strongest fit (the AI/LLM
differentiator) and these companies skew toward modern ATSes (Ashby in
particular).

Usage:
    uv run python scripts/seed_mars.py

Output: a TOML fragment to paste into `kb/seeds/gta-employers.toml` under
the existing buckets. Re-run periodically — many of these are young companies
that move ATS providers more often than the established players.

Workday is excluded — Casey's bank/enterprise targets (RBC, TD, BMO,
Manulife) belong in a separate Workday seed flow (`jobhunt add <url>`).
"""

from __future__ import annotations

import asyncio

import httpx

from jobhunt.discover.probe import _probe_one
from jobhunt.http import DEFAULT_UA, RateLimiter

# Curated candidate slugs sourced from May 2026 public knowledge of the
# Toronto AI / ML startup space. Each candidate's plausible slug is the
# canonical lowercased company-name fragment. Probes will narrow to the
# subset that's actually serving postings on each ATS today.
CANDIDATES: dict[str, list[str]] = {
    "greenhouse": [
        # Toronto AI/LLM-adjacent
        "cohere",                  # already on ashby — try greenhouse too
        "verloop",
        "ada",                     # conversational AI
        "voiceflow",               # also on lever — try greenhouse
        "tealbook",                # AI procurement, Toronto HQ
        "integrate-ai",
        "borealis",                # RBC AI subsidiary
        # Generative AI tooling / dev tools
        "tenstorrent",             # AI accelerator hardware, Toronto
        "kira",                    # AI contract review
        # Health / bio AI
        "deepgenomics",
        "proteinqure",
        "bluedot",
        # Toronto fintech AI
        "wealthsimple",            # known live
        "drop",
        "neo",
        "borrowell",
        # Broader Toronto startup tech (AI-adjacent culture)
        "applyboard",
        "ecobee",
        "vidyard",
        "tophat",
        "loopio",
        "achievers",
        "wave",
        "league",
        "nuvei",
        "properly",
        "q4inc",
        "venasolutions",
        "validere",
        "north",                   # north.io
        "coconutsoftware",
        # AI agency / consultancy
        "altaml",
        "stradigi",
        "faculty",
    ],
    "lever": [
        "voiceflow",
        "fellow",
        "kovrr",
        "deeplearningai",
        "consensus",
        "openstore",
        "synthesia",
    ],
    "ashby": [
        # Ashby is *the* AI-startup ATS in 2026 — broader candidate list here.
        "harvey",                  # legal AI
        "perplexity",              # AI search
        "anthropic",               # likely 404 (greenhouse/workday) but cheap to probe
        "openai",                  # same — cheap to confirm
        "lovable",
        "cursor",
        "anysphere",               # cursor's parent
        "magic",
        "browserbase",
        "elevenlabs",
        "rerun",
        "replicate",
        "modal",
        "writer",
        "exa",
        "decagon",
        "mercor",                  # already in seed; idempotent
        "vapi",
        "retell",
        "11x",
        "clay",
        "humanloop",
        "langfuse",
        "lindy",
        "trellis",
        "weights-biases",
        "wandb",
    ],
    "smartrecruiters": [
        # SmartRecruiters slugs are case-sensitive. These are unverified —
        # script will surface the live subset.
        "Bell",
        "BMOFinancialGroup",
        "TELUS",
        "Sobeys",
        "Loblaw",
        "Manulife",                # likely Workday in reality
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

    print("\n\n# ===== verified MaRS/AI seed block (merge into kb/seeds/gta-employers.toml) =====")
    print("# NOTE: keys overlap with the existing seed file. Manually merge —")
    print("# duplicates are harmless (config seed dedupes), but a single source")
    print("# of truth keeps the seed file readable.\n")
    for ats, hits in verified.items():
        if not hits:
            print(f"{ats} = []  # nothing live")
            continue
        # Sort by job count desc — bigger boards first.
        hits.sort(key=lambda h: -h[1])
        joined = ", ".join(f'"{slug}"' for slug, _ in hits)
        print(f"{ats} = [{joined}]")


if __name__ == "__main__":
    asyncio.run(main())
