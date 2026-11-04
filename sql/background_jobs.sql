-- Synthetic standalone job tracking, independent of company-search tables.
-- Can be safely applied repeatedly to the disposable local PostgreSQL service.
CREATE SCHEMA IF NOT EXISTS osint_demo_jobs;

CREATE TABLE IF NOT EXISTS osint_demo_jobs.runs (
    run_id UUID PRIMARY KEY,
    company_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

-- Defends against duplicate active runs even if a caller forgets the
-- advisory transaction lock. Terminal runs do not block later refreshes.
CREATE UNIQUE INDEX IF NOT EXISTS demo_one_active_run_per_company
ON osint_demo_jobs.runs (company_number)
WHERE status IN ('PENDING','RUNNING');

CREATE TABLE IF NOT EXISTS osint_demo_jobs.tasks (
    run_id UUID NOT NULL REFERENCES osint_demo_jobs.runs(run_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','COMPLETED','FAILED')),
    finished_at TIMESTAMPTZ,
    PRIMARY KEY (run_id, ordinal),
    UNIQUE (run_id, stage)
);
