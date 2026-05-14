"""Deterministic certification frequency analysis over scanned job descriptions."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping

# Specific variants (e.g. "– Associate") must appear BEFORE their base
# pattern (e.g. bare "Solutions Architect") so the overlap-filter in
# extract_certs keeps the longer match.

_I = re.IGNORECASE


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
    ("Azure AZ-500", _pat(r"\bAZ-?500\b")),
    ("Azure AZ-700", _pat(r"\bAZ-?700\b")),
    ("Azure AZ-800", _pat(r"\bAZ-?800\b")),
    ("Azure AZ-801", _pat(r"\bAZ-?801\b")),
    ("Azure DP-900", _pat(r"\bDP-?900\b")),
    ("Azure DP-100", _pat(r"\bDP-?100\b")),
    ("Azure AI-900", _pat(r"\bAI-?900\b")),
    ("Azure Fundamentals", _pat(r"\bAzure\s+Fundamentals\b")),
    # --- Cloud: Microsoft legacy ---
    ("MCSE", _pat(r"\bMCSE\b")),
    ("MCSA", _pat(r"\bMCSA\b")),
    # --- Kubernetes / Cloud-native ---
    ("CKS", _pat(r"\bCKS\b")),           # specific before CKA/CKAD
    ("CKAD", _pat(r"\bCKAD\b")),
    ("CKA", _pat(r"\bCKA\b")),
    # --- HashiCorp / Infrastructure ---
    ("Terraform Associate", _pat(
        r"\bHashiCorp\s+Certified\s*:\s*Terraform\s+Associate\b"
        r"|\bTerraform\s+Associate\b"
    )),
    ("Vault Associate", _pat(
        r"\bHashiCorp\s+Certified\s*:\s*Vault\s+Associate\b"
        r"|\bVault\s+Associate\b"
    )),
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
    ("PMI-ACP", _pat(r"\bPMI-ACP\b")),
    ("PMI-RMP", _pat(r"\bPMI-RMP\b")),
    ("PMI-PBA", _pat(r"\bPMI-PBA\b")),
    ("PRINCE2", _pat(r"\bPRINCE\s*2\b")),
    ("CSPO", _pat(r"\bCSPO\b")),
    ("CSM", _pat(r"\bCSM\b")),
    ("PSM", _pat(r"\bPSM\b")),
    # `SAFe` is mixed-case by design; case-insensitive matching would collide with the
    # English word "safe" (job descriptions are full of "safe place", "safe transport").
    # Inline (?-i:...) keeps the bare-acronym alternative case-sensitive while letting
    # the spelled-out "Scaled Agile" stay case-insensitive.
    ("SAFe", _pat(r"(?-i:\bSAFe\b)|\bScaled\s+Agile\b")),
    ("ITIL", _pat(r"\bITIL\b")),
    # --- Architecture ---
    ("TOGAF", _pat(r"\bTOGAF\b")),
    # --- Oracle ---
    ("OCP", _pat(r"\bOCP\b")),
    ("OCA", _pat(r"\bOCA\b")),
    # --- Salesforce (specific before generic anchor) ---
    ("Salesforce Certified Administrator", _pat(
        r"\bSalesforce\s+Certified\s+Administrator\b"
    )),
    ("Salesforce Certified Developer", _pat(
        r"\bSalesforce\s+Certified\s+(?:Platform\s+)?Developer\b"
    )),
    ("Salesforce Certified", _pat(r"\bSalesforce\s+Certified\b")),
    # --- Six Sigma ---
    ("Six Sigma Black Belt", _pat(
        r"\bSix\s+Sigma\s+Black\s+Belt\b|\bCSSBB\b"
    )),
    ("Six Sigma Green Belt", _pat(
        r"\bSix\s+Sigma\s+Green\s+Belt\b|\bCSSGB\b"
    )),
    ("Six Sigma Yellow Belt", _pat(r"\bSix\s+Sigma\s+Yellow\s+Belt\b")),
    # --- Google Marketing ---
    ("Google Analytics Certification", _pat(r"\bGoogle\s+Analytics\s+Cert(?:ification)?\b")),
    ("Google Ads Certification", _pat(r"\bGoogle\s+Ads\s+Cert(?:ification)?\b")),
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
# Slashes are excluded from the char class so "Java/Spring/..." can't become a single
# token and produce false positives. Require at least 2 words ({1,2} extra) to avoid
# single-word matches from slash-delimited skill lists landing on the last capitalized
# token before "certification". Multi-word unknown certs (e.g. "Azure Security Engineer
# Certification") are the real target here.
_GENERIC_CERTIFIED = re.compile(
    r"\bCertified\s+([A-Z][A-Za-z+.]*(?:\s+[A-Z][A-Za-z+.]*){1,2})\b"
)
_GENERIC_CERTIFICATION = re.compile(
    r"\b([A-Z][A-Za-z+.]*(?:\s+[A-Z][A-Za-z+.]*){1,2})\s+[Cc]ertification\b"
)

# Reject any generic match whose phrase contains one of these words (word-level check,
# not phrase-level) — they indicate job-requirement boilerplate, not cert names.
_GENERIC_STOPWORDS = frozenset({
    "The", "A", "An", "Our", "Your", "This", "We", "You",
    "Developer", "Engineer", "Job", "Description", "Required", "Preferred",
    "Desired", "Requirement", "Experience", "Skills", "Knowledge", "Role",
    "Position", "Management", "Development", "Engineering",
})


def extract_certs(text: str) -> list[str]:
    """Return a de-duplicated list of cert names found in *text*, in text order.

    Known certs take priority over generic patterns. When two known patterns
    overlap (e.g. "AWS Certified Solutions Architect" inside "AWS Certified
    Solutions Architect – Associate"), only the longest (outermost) match is
    kept. Generic patterns only run on text not consumed by known-cert matches.
    """
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

    masked = list(text)
    for s, e in known_spans:
        for i in range(s, e):
            masked[i] = " "
    scrubbed = "".join(masked)

    generic_matches: list[tuple[int, str]] = []
    for pat in (_GENERIC_CERTIFIED, _GENERIC_CERTIFICATION):
        for m in pat.finditer(scrubbed):
            phrase = " ".join(m.group(1).strip().split())
            if not any(w in _GENERIC_STOPWORDS for w in phrase.split()):
                generic_matches.append((m.start(), phrase))

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


def extract_certs_split(text: str) -> tuple[list[str], list[str]]:
    """Like `extract_certs` but returns (known, generic) separately so callers
    can surface generic-regex hits as a review list for promotion to `_KNOWN`.

    Order within each list matches text-occurrence order. Generic phrases that
    happen to share a name with a known cert are dropped from the generic list
    (the known curation wins on collision)."""
    raw_matches: list[tuple[int, int, str]] = []
    for name, pat in _KNOWN:
        for m in pat.finditer(text):
            raw_matches.append((m.start(), m.end(), name))
    raw_matches.sort(key=lambda t: (t[0], -(t[1] - t[0])))

    accepted: list[tuple[int, int, str]] = []
    covered: list[tuple[int, int]] = []
    for start, end, name in raw_matches:
        if any(cs <= start and end <= ce for cs, ce in covered):
            continue
        accepted.append((start, end, name))
        covered.append((start, end))

    known_seen: set[str] = set()
    known_names: list[str] = []
    for _, _, name in accepted:
        if name not in known_seen:
            known_seen.add(name)
            known_names.append(name)
    known_spans = [(s, e) for s, e, _ in accepted]

    masked = list(text)
    for s, e in known_spans:
        for i in range(s, e):
            masked[i] = " "
    scrubbed = "".join(masked)

    generic_matches: list[tuple[int, str]] = []
    for pat in (_GENERIC_CERTIFIED, _GENERIC_CERTIFICATION):
        for m in pat.finditer(scrubbed):
            phrase = " ".join(m.group(1).strip().split())
            if not any(w in _GENERIC_STOPWORDS for w in phrase.split()):
                generic_matches.append((m.start(), phrase))

    seen_generic: set[str] = set()
    generic_names: list[str] = []
    for _, phrase in sorted(generic_matches):
        if phrase in known_seen:
            continue
        if phrase in seen_generic:
            continue
        seen_generic.add(phrase)
        generic_names.append(phrase)
    return known_names, generic_names


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


def tally_split(rows: Iterable[Mapping[str, object]]) -> tuple[Counter[str], Counter[str]]:
    """Like `tally` but returns (known, generic) counters separately."""
    known: Counter[str] = Counter()
    generic: Counter[str] = Counter()
    for row in rows:
        title = row["title"] or ""
        desc = row["description"] or ""
        combined = f"{title}\n{desc}"
        k_names, g_names = extract_certs_split(combined)
        for name in k_names:
            known[name] += 1
        for name in g_names:
            generic[name] += 1
    return known, generic
