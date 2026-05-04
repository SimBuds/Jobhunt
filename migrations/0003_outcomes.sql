-- 0003_outcomes.sql — outcome tracking + audit_json column.
-- outcome_at records when a final outcome (offer/rejected/withdrawn) was set,
-- enabling per-score-band interview-rate analysis via `job-seeker config calibrate`.

ALTER TABLE applications ADD COLUMN outcome_at TIMESTAMP;
ALTER TABLE applications ADD COLUMN audit_json TEXT;

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_outcome_at ON applications(outcome_at);
