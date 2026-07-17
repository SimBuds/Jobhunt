-- Application channel attribution (July 2026). Tracks WHERE an application
-- was submitted — the jobhunt pipeline vs. manual channels (LinkedIn Easy
-- Apply, Indeed, referrals, recruiter outreach, employer site). Lives on
-- `applications`, not `jobs`: a scanned Greenhouse job can still be applied
-- to via LinkedIn. Drives `analyze response-rate --by channel` and
-- `analyze funnel`.
ALTER TABLE applications ADD COLUMN channel TEXT NOT NULL DEFAULT 'pipeline';
CREATE INDEX idx_applications_channel ON applications(channel);
