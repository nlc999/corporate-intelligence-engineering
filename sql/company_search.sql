-- Self-contained PostgreSQL 15+ demonstration schema.
-- Adapted from an existing indexed corporate-register discovery schema.
-- ONLY synthetic registrations belong in the accompanying seed fixture.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS osint;

-- Source register membership is intentionally not evidence of ingestion.
CREATE TABLE IF NOT EXISTS osint.ingested_company (
    company_number TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS osint.company_catalogue (
    company_number TEXT NOT NULL,
    company_name TEXT NOT NULL,
    company_status TEXT NOT NULL,
    locality TEXT,
    previous_names TEXT[] NOT NULL DEFAULT '{}',
    snapshot_date DATE NOT NULL,
    company_name_normalised TEXT GENERATED ALWAYS AS (
        ' ' || btrim(regexp_replace(lower(company_name), '[^a-z0-9]+', ' ', 'g')) || ' '
    ) STORED
);
CREATE TABLE IF NOT EXISTS osint.company_catalogue_staging
    (LIKE osint.company_catalogue INCLUDING DEFAULTS INCLUDING GENERATED INCLUDING CONSTRAINTS);

CREATE TABLE IF NOT EXISTS osint.company_previous_name_search (
    company_number TEXT NOT NULL,
    previous_name TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    previous_name_normalised TEXT GENERATED ALWAYS AS (
        ' ' || btrim(regexp_replace(lower(previous_name), '[^a-z0-9]+', ' ', 'g')) || ' '
    ) STORED
);
CREATE TABLE IF NOT EXISTS osint.company_previous_name_search_staging
    (LIKE osint.company_previous_name_search INCLUDING DEFAULTS INCLUDING GENERATED INCLUDING CONSTRAINTS);

-- All four physical tables are indexed so that an atomic name swap always
-- publishes an indexed search catalogue. Staging index names are distinct.
CREATE UNIQUE INDEX IF NOT EXISTS catalogue_number_idx
    ON osint.company_catalogue (company_number);
CREATE INDEX IF NOT EXISTS catalogue_name_exact_idx
    ON osint.company_catalogue (company_name_normalised);
CREATE INDEX IF NOT EXISTS catalogue_name_prefix_idx
    ON osint.company_catalogue (company_name_normalised text_pattern_ops);
CREATE INDEX IF NOT EXISTS catalogue_name_trigram_idx
    ON osint.company_catalogue USING GIN (company_name_normalised gin_trgm_ops);

CREATE UNIQUE INDEX IF NOT EXISTS catalogue_staging_number_idx
    ON osint.company_catalogue_staging (company_number);
CREATE INDEX IF NOT EXISTS catalogue_staging_name_exact_idx
    ON osint.company_catalogue_staging (company_name_normalised);
CREATE INDEX IF NOT EXISTS catalogue_staging_name_prefix_idx
    ON osint.company_catalogue_staging (company_name_normalised text_pattern_ops);
CREATE INDEX IF NOT EXISTS catalogue_staging_name_trigram_idx
    ON osint.company_catalogue_staging USING GIN (company_name_normalised gin_trgm_ops);

CREATE UNIQUE INDEX IF NOT EXISTS previous_name_unique_idx
    ON osint.company_previous_name_search (company_number, previous_name_normalised);
CREATE INDEX IF NOT EXISTS previous_name_exact_idx
    ON osint.company_previous_name_search (previous_name_normalised);
CREATE INDEX IF NOT EXISTS previous_name_prefix_idx
    ON osint.company_previous_name_search (previous_name_normalised text_pattern_ops);
CREATE INDEX IF NOT EXISTS previous_name_trigram_idx
    ON osint.company_previous_name_search USING GIN (previous_name_normalised gin_trgm_ops);

CREATE UNIQUE INDEX IF NOT EXISTS previous_name_staging_unique_idx
    ON osint.company_previous_name_search_staging (company_number, previous_name_normalised);
CREATE INDEX IF NOT EXISTS previous_name_staging_exact_idx
    ON osint.company_previous_name_search_staging (previous_name_normalised);
CREATE INDEX IF NOT EXISTS previous_name_staging_prefix_idx
    ON osint.company_previous_name_search_staging (previous_name_normalised text_pattern_ops);
CREATE INDEX IF NOT EXISTS previous_name_staging_trigram_idx
    ON osint.company_previous_name_search_staging USING GIN (previous_name_normalised gin_trgm_ops);
