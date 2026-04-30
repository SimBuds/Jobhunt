-- 0002_apply_tracking.sql — weekly tracking + decline reason on jobs.
-- `applied_week` is an ISO week label like "2026-W18" so weekly rollups are trivial.

ALTER TABLE jobs ADD COLUMN decline_reason TEXT;
ALTER TABLE applications ADD COLUMN applied_week TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_decline_reason ON jobs(decline_reason);
CREATE INDEX IF NOT EXISTS idx_applications_applied_week ON applications(applied_week);
