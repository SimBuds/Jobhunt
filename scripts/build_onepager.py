"""Generate a one-page, ATS-clean manual-apply resume from `verified.json`.

The comprehensive `Baseline_Resume.docx` is the 2-page fact source (it feeds the
pipeline). This script produces a separate ONE-page document for manual
applications, reusing the pipeline's own one-page shrink ladder and ATS renderer
so it stays in sync with the verified facts. It does NOT modify the baseline.

One-pager composition choices (vs the comprehensive baseline):
- Each dev role keeps up to 3 work points (>= 3 per role); Sous Chef keeps 1.
- The PROJECTS section is dropped to make room for >=3 bullets/role on one page
  (the baseline keeps both projects; project tech still shows in the skills row).
- 4 consolidated skill categories (Project Stack merged into AI & Tooling).
- The optional "Familiar" honesty-signal row is dropped (it stays on the baseline).
- Dean's List is folded onto the diploma line; the coursework list is dropped
  (the pipeline's per-JD tailored output surfaces relevant courses instead).
- Contact URLs render as bare domains so the contact line fits one line.

Run:  uv run python scripts/build_onepager.py
Out:  Casey_Hsu_Resume_OnePage.docx  (repo root)

For an actual application, prefer a JD-tailored one-pager:
`jobhunt apply --url <posting-url>` — it beats any generic master.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jobhunt.pipeline.tailor import (
    TailoredCategory,
    TailoredProject,
    TailoredResume,
    TailoredRole,
)
from jobhunt.resume.render_docx import estimate_lines, fits_one_page, render

REPO = Path(__file__).resolve().parent.parent
VERIFIED = REPO / "kb" / "profile" / "verified.json"
OUT = REPO / "Casey_Hsu_Resume_OnePage.docx"


def build() -> Path:
    v = json.loads(VERIFIED.read_text())

    cats = [
        TailoredCategory("Core", list(v.get("skills_core") or [])),
        TailoredCategory("CMS & E-Commerce", list(v.get("skills_cms") or [])),
        TailoredCategory("Data & DevOps", list(v.get("skills_data_devops") or [])),
        TailoredCategory(
            "AI & Tooling",
            list(v.get("skills_ai") or []) + list(v.get("skills_projects") or []),
        ),
    ]
    cats = [c for c in cats if c.items]

    # Keep >= 3 work points per dev role (cap at 3 for one-page density); the
    # culinary role keeps 1 (least relevant to dev applications).
    roles = []
    for r in v["work_history"]:
        keep = 1 if r["title"].lower().startswith("sous chef") else 3
        roles.append(
            TailoredRole(r["title"], r["employer"], r["dates"], list(r["bullets"])[:keep])
        )
    # PROJECTS dropped on the one-pager so >= 3 bullets/role fits one page; the
    # comprehensive baseline keeps them, and the merged AI & Tooling skill row
    # still carries the project tech (FastAPI, Redis, Claude API, ...).
    projects: list[TailoredProject] = []
    # verified.json education is one combined string (diploma + Dean's List +
    # coursework). Keep the diploma line and fold Dean's List inline.
    education = []
    for line in v.get("education") or []:
        diploma = line.split("\n")[0].strip()
        if not diploma.endswith("."):
            diploma += "."
        education.append(diploma + " Dean's List (all terms).")

    tr = TailoredResume(
        summary=v["summary"],
        skills_categories=cats,
        roles=roles,
        certifications=list(v.get("certifications") or []),
        education=education,
        coursework=[],
        model="onepager",
        projects=projects,
    )
    # NOTE: intentionally NOT calling _shrink_to_one_page — it trims to 1
    # bullet/role, the opposite of the >= 3-per-role goal. The bullet caps above
    # plus dropping PROJECTS are what keep this to one page.
    if not fits_one_page(tr):
        print(f"warning: estimate {estimate_lines(tr)} lines may exceed one page")

    contact = re.sub(r"https?://", "", v["contact_line"])  # bare domains
    return render(tr, contact, v["name"], OUT)


if __name__ == "__main__":
    print("wrote", build())
