-- 0001_init.sql — initial schema.
-- jobs, companies, scores, applications. Migration tracking lives in `migrations`
-- (auto-created by db.migrate before this file runs).

CREATE TABLE IF NOT EXISTS companies (
    name TEXT PRIMARY KEY,
    homepage TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    company TEXT,
    title TEXT,
    location TEXT,
    remote_type TEXT,
    description TEXT,
    url TEXT,
    posted_at TIMESTAMP,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    UNIQUE(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);

CREATE TABLE IF NOT EXISTS scores (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    score INTEGER NOT NULL,
    reasons TEXT,
    red_flags TEXT,
    must_clarify TEXT,
    model TEXT,
    prompt_hash TEXT,
    scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scores_score ON scores(score);

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'drafted',
    resume_path TEXT,
    cover_path TEXT,
    fill_plan_path TEXT,
    applied_at TIMESTAMP,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
