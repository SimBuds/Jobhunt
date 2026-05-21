-- 0005_response_tracking.sql — response/interview/outcome tracking.
--
-- `applied_at` already records when an application was submitted. The fields
-- added here track the lifecycle after that point so per-score-band analyses
-- in `analyze response-rate` (Phase 14) can answer "did this score actually
-- convert to a recruiter reply?".
--
-- Valid `outcome` values (documented; no CHECK constraint so this stays
-- cheap and matches the existing `status` column style): 'offer',
-- 'rejected', 'withdrawn', 'ghosted'. `status` continues to drive
-- pipeline state ('drafted' / 'applied' / 'interviewing' / 'offer' /
-- 'rejected' / 'withdrawn'); `outcome` is the *final* disposition,
-- distinct from the in-flight `status`.

ALTER TABLE applications ADD COLUMN response_received_at TIMESTAMP;
ALTER TABLE applications ADD COLUMN interview_at TIMESTAMP;
ALTER TABLE applications ADD COLUMN outcome TEXT;
-- recruiter_handle is renamed to recruiter_type in migration 0006.
-- Left under the original name here so already-migrated databases stay
-- consistent with their `migrations` ledger.
ALTER TABLE applications ADD COLUMN recruiter_handle TEXT;

CREATE INDEX IF NOT EXISTS idx_applications_response_received_at
    ON applications(response_received_at);
CREATE INDEX IF NOT EXISTS idx_applications_outcome ON applications(outcome);
