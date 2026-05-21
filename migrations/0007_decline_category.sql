-- 0007_decline_category.sql — categorize free-text decline reasons.
--
-- `jobs.decline_reason` is human-readable prose written by the scorer.
-- Aggregating it across a quarter (Phase 14 `analyze declines`) needs an
-- enum. The classifier in `jobhunt.pipeline._decline_classify` runs at
-- score time going forward, and the next `db migrate` backfills any
-- existing rows that already have a decline_reason set.
--
-- Valid values (documented; no CHECK so we can extend cheaply):
--   'years_gap'           — JD requires 5+ / 7+ years Casey doesn't have.
--   'people_management'   — Manager/Director/VP/4+ direct reports asks.
--   'wrong_domain'        — domain Casey hasn't worked in.
--   'wrong_stack'         — non-overlapping required stack (Go/Rust/etc.).
--   'familiar_only'       — only Familiar-bucket skills match.
--   'regulated_domain'    — clinical / securities / medical-device.
--   'location_mismatch'   — non-Toronto, non-remote-Canada.
--   'other'               — fell through every pattern (review for new patterns).

ALTER TABLE jobs ADD COLUMN decline_category TEXT;
CREATE INDEX IF NOT EXISTS idx_jobs_decline_category ON jobs(decline_category);
