"""Index of `jobhunt answer` artifacts.

Mirrors the on-disk markdown files in the `answers` table so `--recall`
can do a LIKE-match in SQL. Pure stdlib parsing of the artifact format
written by `commands.answer_cmd._save_answer`:

    # Question

    <question text>

    # Answer

    <answer text>
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path


_QUESTION_BLOCK_RE = re.compile(
    r"^# Question\s*\n+(?P<question>.+?)\n+# Answer", re.DOTALL
)


def question_sha1(question: str) -> str:
    """Same 12-char digest used in the on-disk filename."""
    return hashlib.sha1(question.encode("utf-8")).hexdigest()[:12]


def index_answer(
    conn: sqlite3.Connection,
    *,
    sha1: str,
    question: str,
    job_id: str | None,
    path: Path,
) -> None:
    """Upsert one row. Re-running the same question overwrites the path +
    refreshes created_at — matches the file's overwrite semantics.

    Standalone answers (`job_id=None`) are stored with `job_id=''` so the
    composite PK (sha1, job_id) works under SQLite's "expressions not
    allowed in PK constraints" rule.
    """
    conn.execute(
        """
        INSERT INTO answers (sha1, job_id, question, path, created_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(sha1, job_id) DO UPDATE SET
            question = excluded.question,
            path = excluded.path,
            created_at = CURRENT_TIMESTAMP
        """,
        (sha1, job_id or "", question, str(path)),
    )


def _parse_question_from_file(path: Path) -> str | None:
    """Return the question text inside an answer artifact, or None if the
    file doesn't match the expected format."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _QUESTION_BLOCK_RE.search(text)
    if m is None:
        return None
    return m.group("question").strip()


def backfill_existing(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Walk `data/answers/` and `data/applications/*/answers/` to populate
    the index from any artifacts written before the index existed. Returns
    the number of rows inserted/updated. Safe to re-run."""
    candidates: list[tuple[Path, str | None]] = []
    standalone = data_dir / "answers"
    if standalone.is_dir():
        for p in standalone.glob("*.md"):
            candidates.append((p, None))
    apps_dir = data_dir / "applications"
    if apps_dir.is_dir():
        for ans_dir in apps_dir.glob("*/answers"):
            # The parent dir name is the safe-id of the job. We don't have a
            # reverse mapping from safe-id back to job.id, so store the
            # safe-id as the job_id. List queries can resolve both.
            job_safe_id = ans_dir.parent.name
            for p in ans_dir.glob("*.md"):
                candidates.append((p, job_safe_id))

    updated = 0
    with conn:
        for path, job_id in candidates:
            question = _parse_question_from_file(path)
            if question is None:
                continue
            index_answer(
                conn,
                sha1=question_sha1(question),
                question=question,
                job_id=job_id,
                path=path,
            )
            updated += 1
    return updated


def recall(
    conn: sqlite3.Connection, phrase: str, *, limit: int = 10
) -> list[sqlite3.Row]:
    """LIKE-match the question column. Case-insensitive, partial substring.
    Sorted newest-first so re-asking a question surfaces the most-recent
    draft before older variants. `job_id=''` (standalone) is normalised
    back to None for readability in the calling layer."""
    pattern = f"%{phrase}%"
    return list(
        conn.execute(
            """
            SELECT sha1,
                   question,
                   CASE WHEN job_id = '' THEN NULL ELSE job_id END AS job_id,
                   path,
                   created_at
            FROM answers
            WHERE question LIKE ? COLLATE NOCASE
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (pattern, limit),
        )
    )
