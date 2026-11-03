-- Who produced an application's first draft. NULL means Casey, by hand.
-- An automated caller (the hub) records its name, e.g. 'hub'. Written on
-- insert only, so a later human re-tailor never erases the original drafter.
ALTER TABLE applications ADD COLUMN drafted_by TEXT;
