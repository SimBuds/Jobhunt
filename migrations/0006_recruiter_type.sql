-- 0006_recruiter_type.sql — rename recruiter_handle → recruiter_type.
--
-- Phase 1 shipped this column as a free-text handle. Use-case refined:
-- the field is more valuable as a *type* enum that lets `interview-prep`
-- (Phase 13) tailor likely questions by recruiter category. Recruiting
-- agencies skew toward personal/soft-skill questions; hiring managers
-- skew toward deep technical + team-fit; internal recruiters skew toward
-- behavioral + comp.
--
-- Valid values (documented; no CHECK so we can extend cheaply later):
--   'internal_recruiter'  — company HR / talent acquisition team.
--   'hiring_manager'      — the team's lead/director who owns the seat.
--   'external_agency'     — third-party recruiter (Robert Half, etc.).
--   'unknown'             — default fallback when type isn't yet known.

ALTER TABLE applications RENAME COLUMN recruiter_handle TO recruiter_type;
