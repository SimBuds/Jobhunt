-- 0008_answer_index.sql — index of `jobhunt answer` artifacts.
--
-- Each call to `jobhunt answer "<question>"` writes a markdown file under
-- `data/answers/<sha1>.md` (standalone) or
-- `data/applications/<job-id>/answers/<sha1>.md` (job-scoped). Without an
-- index, recall ("what did I say about Vue last time?") requires scanning
-- the filesystem. This table mirrors the file artifacts so `--recall` can
-- LIKE-match the question text in SQL.
--
-- `sha1` is the 12-char hash of the question text — same value used in the
-- filename. Composite PK is (sha1, job_id); standalone answers use the
-- empty string '' for job_id (SQLite doesn't permit expressions in PK
-- constraints, and NULL values are treated as distinct by SQLite under
-- PRIMARY KEY which would let duplicate standalone rows accumulate).
--
-- The next `db migrate` also runs `pipeline._answer_index.backfill` which
-- walks both directories and populates this table from disk so any
-- pre-existing artifacts are queryable.

CREATE TABLE IF NOT EXISTS answers (
    sha1 TEXT NOT NULL,
    job_id TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sha1, job_id)
);

CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question);
CREATE INDEX IF NOT EXISTS idx_answers_job_id ON answers(job_id);
