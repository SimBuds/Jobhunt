"""Deterministic certification frequency analysis over scanned job descriptions."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping

# ---------------------------------------------------------------------------
# Known-cert registry
# Each entry: (canonical display name, compiled case-insensitive pattern).
# Patterns use \b word-boundaries. Specific variants (e.g. "– Associate")
# must appear BEFORE their base pattern (e.g. bare "Solutions Architect") so
# the overlap-filter below keeps the longer match.
# ---------------------------------------------------------------------------

_I = re.IGNORECASE

# Helper to build a pattern that ends at a word or non-word boundary
# (useful for certs ending in non-word chars like +).
def _pat(r: str, flags: int = _I) -> re.Pattern[str]:
    return re.compile(r, flags)


_KNOWN: list[tuple[str, re.Pattern[str]]] = [
    # --- Cloud: AWS (specific first, then base) ---
    ("AWS Certified Solutions Architect – Professional", _pat(
        r"\bAWS\s+Certified\s+Solutions\s+Architect[\s–-]+Professional\b"
    )),
    ("AWS Certified Solutions Architect – Associate", _pat(
        r"\bAWS\s+Certified\s+Solutions\s+Architect[\s–-]+Associate\b"
    )),
    ("AWS Certified Solutions Architect", _pat(
        r"\bAWS\s+Certified\s+Solutions\s+Architect\b"
    )),
    ("AWS Certified Developer", _pat(r"\bAWS\s+Certified\s+Developer\b")),
    ("AWS Certified DevOps Engineer", _pat(r"\bAWS\s+Certified\s+DevOps\s+Engineer\b")),
    ("AWS Certified SysOps Administrator", _pat(r"\bAWS\s+Certified\s+SysOps\s+Administrator\b")),
    ("AWS Cloud Practitioner", _pat(r"\bAWS\s+Cloud\s+Practitioner\b")),
    # --- Cloud: GCP ---
    ("GCP Professional Cloud Architect", _pat(
        r"\bGCP\s+Professional\s+Cloud\s+Architect\b|\bGoogle\s+Professional\s+Cloud\s+Architect\b"
    )),
    ("GCP Professional Data Engineer", _pat(
        r"\bGCP\s+Professional\s+Data\s+Engineer\b|\bGoogle\s+Professional\s+Data\s+Engineer\b"
    )),
    ("GCP Associate Cloud Engineer", _pat(
        r"\bGCP\s+Associate\s+Cloud\s+Engineer\b"
        r"|\bGoogle\s+Associate\s+Cloud\s+Engineer\b"
        r"|\bGCP\s+ACE\b"
    )),
    # --- Cloud: Azure ---
    ("Azure AZ-900", _pat(r"\bAZ-?900\b")),
    ("Azure AZ-104", _pat(r"\bAZ-?104\b")),
    ("Azure AZ-204", _pat(r"\bAZ-?204\b")),
    ("Azure AZ-305", _pat(r"\bAZ-?305\b")),
    ("Azure AZ-400", _pat(r"\bAZ-?400\b")),
    ("Azure Fundamentals", _pat(r"\bAzure\s+Fundamentals\b")),
    # --- Security ---
    ("CISSP", _pat(r"\bCISSP\b")),
    ("CISM", _pat(r"\bCISM\b")),
    ("CISA", _pat(r"\bCISA\b")),
    ("CEH", _pat(r"\bCEH\b")),
    ("OSCP", _pat(r"\bOSCP\b")),
    ("GIAC GSEC", _pat(r"\bGSEC\b|\bGIAC\s+GSEC\b")),
    # Security+ ends in '+' (non-word char) so \b won't work after it; use lookahead.
    ("Security+", _pat(
        r"\bSecurity\+(?=\s|$|[,;.()\[\]])"
        r"|CompTIA\s+Security\+?(?=\s|$|[,;.()\[\]])"
        r"|\bSec\+(?=\s|$|[,;.()\[\]])"
    )),
    # --- PM / Agile ---
    ("PMP", _pat(r"\bPMP\b")),
    ("CAPM", _pat(r"\bCAPM\b")),
    ("PRINCE2", _pat(r"\bPRINCE\s*2\b")),
    ("CSM", _pat(r"\bCSM\b")),
    ("PSM", _pat(r"\bPSM\b")),
    ("SAFe", _pat(r"\bSAFe\b|\bScaled\s+Agile\b")),
    ("ITIL", _pat(r"\bITIL\b")),
    # --- Data / ML ---
    ("Databricks Certified", _pat(r"\bDatabricks\s+Certified\b")),
    ("Snowflake SnowPro", _pat(r"\bSnowPro\b|\bSnowflake\s+SnowPro\b")),
    ("Tableau Desktop Specialist", _pat(r"\bTableau\s+Desktop\s+Specialist\b")),
    ("Cloudera CCA", _pat(r"\bCloudera\s+CCA\b|\bCCA\b")),
    ("TensorFlow Developer Certificate", _pat(r"\bTensorFlow\s+Developer\s+Cert(?:ificate)?\b")),
    # --- Networking / sysadmin ---
    ("CCNP", _pat(r"\bCCNP\b")),
    ("CCNA", _pat(r"\bCCNA\b")),
    ("RHCE", _pat(r"\bRHCE\b")),
    ("RHCSA", _pat(r"\bRHCSA\b")),
    ("LPIC", _pat(r"\bLPIC\b")),
    # Network+/Linux+ end in '+' — same lookahead trick.
    ("CompTIA Network+", _pat(
        r"\bNetwork\+(?=\s|$|[,;.()\[\]])"
        r"|CompTIA\s+Network\+?(?=\s|$|[,;.()\[\]])"
    )),
    ("CompTIA A+", _pat(r"\bCompTIA\s+A\+(?=\s|$|[,;.()\[\]])")),
    ("CompTIA Linux+", _pat(
        r"\bLinux\+(?=\s|$|[,;.()\[\]])"
        r"|CompTIA\s+Linux\+?(?=\s|$|[,;.()\[\]])"
    )),
    # --- Finance / business ---
    ("CFA", _pat(r"\bCFA\b")),
    ("CPA", _pat(r"\bCPA\b")),
    ("FRM", _pat(r"\bFRM\b")),
    ("CBAP", _pat(r"\bCBAP\b")),
]

# Generic patterns: capture what follows "Certified" or precedes "certification".
_GENERIC_CERTIFIED = re.compile(
    r"\bCertified\s+([A-Z][A-Za-z+./-]*(?:\s+[A-Z][A-Za-z+./-]*){0,4})\b"
)
_GENERIC_CERTIFICATION = re.compile(
    r"\b([A-Z][A-Za-z+./-]*(?:\s+[A-Z][A-Za-z+./-]*){0,4})\s+[Cc]ertification\b"
)

_GENERIC_STOPWORDS = frozenset({"The", "A", "An", "Our", "Your", "This", "We", "You"})


def extract_certs(text: str) -> list[str]:
    """Return a de-duplicated list of cert names found in *text*, in text order.

    Known certs take priority over generic patterns. When two known patterns
    overlap (e.g. "AWS Certified Solutions Architect" inside "AWS Certified
    Solutions Architect – Associate"), only the longest (outermost) match is
    kept. Generic patterns only run on text not consumed by known-cert matches.
    """
    # Collect all known-cert matches as (start, end, name).
    raw_matches: list[tuple[int, int, str]] = []
    for name, pat in _KNOWN:
        for m in pat.finditer(text):
            raw_matches.append((m.start(), m.end(), name))

    # Sort by start position; ties broken by length descending (longest wins).
    raw_matches.sort(key=lambda t: (t[0], -(t[1] - t[0])))

    # Remove matches whose span is fully contained within an already-accepted match.
    accepted: list[tuple[int, int, str]] = []
    covered: list[tuple[int, int]] = []
    for start, end, name in raw_matches:
        if any(cs <= start and end <= ce for cs, ce in covered):
            continue
        accepted.append((start, end, name))
        covered.append((start, end))

    known_names = [name for _, _, name in accepted]
    known_spans = [(s, e) for s, e, _ in accepted]

    # Build masked text for generic pattern pass.
    masked = list(text)
    for s, e in known_spans:
        for i in range(s, e):
            masked[i] = " "
    scrubbed = "".join(masked)

    generic_matches: list[tuple[int, str]] = []
    for pat in (_GENERIC_CERTIFIED, _GENERIC_CERTIFICATION):
        for m in pat.finditer(scrubbed):
            phrase = " ".join(m.group(1).strip().split())
            if phrase not in _GENERIC_STOPWORDS:
                generic_matches.append((m.start(), phrase))

    # De-duplicate generics (same phrase can appear multiple times).
    seen_generic: set[str] = set()
    generic_names: list[str] = []
    for _, phrase in sorted(generic_matches):
        if phrase not in seen_generic:
            seen_generic.add(phrase)
            generic_names.append(phrase)

    # De-duplicate known names (same cert caught by multiple patterns).
    seen: set[str] = set()
    result: list[str] = []
    for name in known_names + generic_names:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def tally(rows: Iterable[Mapping[str, object]]) -> Counter[str]:
    """Count how many *distinct* jobs mention each cert.

    Each row must support ``row["title"]`` and ``row["description"]`` access.
    A cert found multiple times in the same job is counted only once.
    """
    counts: Counter[str] = Counter()
    for row in rows:
        title = row["title"] or ""
        desc = row["description"] or ""
        combined = f"{title}\n{desc}"
        for cert in extract_certs(combined):
            counts[cert] += 1
    return counts
