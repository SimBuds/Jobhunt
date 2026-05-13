CREATE TABLE slug_probes (
    company TEXT NOT NULL,
    ats TEXT NOT NULL,
    slug TEXT NOT NULL,
    status INTEGER NOT NULL,
    job_count INTEGER,
    probed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company, ats, slug)
);

CREATE INDEX idx_slug_probes_hit ON slug_probes(ats, status) WHERE status = 200;
