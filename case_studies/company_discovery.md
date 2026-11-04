# Case study 01 — Company Discovery & Entity Resolution

## Problem

Company registers contain ambiguous current legal names, historical names, punctuation variants and misspellings. An interactive discovery service must distinguish a company's **stable registration identity** from a display name, avoid accidental ingestion, and answer common queries without invoking an expensive fuzzy search on every keystroke.

This case study presents a **standalone adaptation of selected existing engineering work**. It uses invented companies, a smaller schema, and dependency-isolated modules, while retaining the distinguishing search design: bounded tiers, normalised indexed keys, historical-name matching, snapshot consistency, and separation of catalogue membership from ingested entities.

## Design

| Tier | Match type | Index design |
| --- | --- | --- |
| 0 | Exact registration number | Unique B-tree |
| 1 | Exact current name | Normalised-name B-tree |
| 2 | Exact previous name | Historical-name B-tree |
| 3 | Current-name prefix | `text_pattern_ops` B-tree |
| 4 | Previous-name prefix | `text_pattern_ops` B-tree |
| 5 | Contained current phrase | GIN trigram |
| 6 | Contained previous phrase | GIN trigram |
| 7 | Fuzzy current name | GIN trigram similarity |
| 8 | Fuzzy previous name | GIN trigram similarity |

The PostgreSQL queries are issued as an **ordered waterfall**: a cheaper matching tier fills the requested result page before more expensive tiers are attempted. In former-name tiers, a `DISTINCT ON (company_number)` selection first retains the best matching alias for each registration before the ranked candidate cap and page cap; this prevents one company's numerous former names from crowding out other companies. Within each tier, PostgreSQL orders matching registrations **before** applying the 2,000-row candidate cap, using trigram similarity for fuzzy matches and stable length/name/registration tie-breakers. The outer query repeats that order when returning the requested page. This avoids arbitrary truncation and makes candidate selection deterministic even for broad matches.

**Performance trade-off:** the cap bounds the number of selected candidate rows, **not** total query work. PostgreSQL may need to inspect and sort more than 2,000 qualifying rows before choosing the best candidates. Trigram similarity in SQL also differs from the Python `SequenceMatcher` tie-breaker used for the final bounded results, so this demonstration still does **not** claim globally optimal fuzzy recall under every possible ranking metric. A production implementation should benchmark broad-query execution plans, latency, and retrieval quality before deployment.

The search normalises both the query and names to lower case, collapses punctuation to spaces, and applies a leading and trailing space. In PostgreSQL, the normalised key is a **stored generated column** so it can be indexed. The corresponding Python normalisation must remain equivalent to the generated-column expression.

### Worked company lookup (synthetic)

The [six-company fixture](../examples/synthetic_companies.json) contains both a currently named **GREENLEAF ENGINEERING LIMITED** (registration `00000006`) and **ALDER RENEWABLES LIMITED** (`00000001`), whose former names include GREENLEAF ENGINEERING LIMITED. Searching the exact name first reaches the **current-name exact** tier for `00000006`; the historical-name exact tier can subsequently identify `00000001` as a different registered company. The former-name result displays **ALDER RENEWABLES LIMITED** as the current legal name and retains the matched alias for explanation. The registration number, not the name alone, distinguishes these results. The fixtures are invented; this illustrates match semantics, **not a measured relevance benchmark**.

Try `python scripts/search_demo.py search 'GREENLEAF ENGINEERING LIMITED'` after seeding the disposable local database as described in the [main README](../README.md).

### Historical names and company identity

A former-name result returns the **current** legal company name, the stable registration number, a `previous_*` match kind, and the actual matched historical name. Multiple aliases of the same registration are deduplicated **within PostgreSQL before either LIMIT**, using deterministic alias ranking; identical historical names belonging to distinct registrations are not conflated.

A previous-name index row is joined to the published catalogue using both `company_number` **and** `snapshot_date`. This excludes historical-name rows left over from a different monthly generation. A read-only `REPEATABLE READ` transaction holds the same snapshot across the search tiers.

### Staging and refresh

[`historical_names.py`](../src/osint_research/company_discovery/historical_names.py) contains a small executable example of paired staging-table publication. It verifies that aliases correspond to staged registrations with the same snapshot date and swaps both catalogue and alias table names in one transaction. Advisory locking prevents overlapping demo refreshes. This is an **illustrative small-fixture loader**, not the large-scale bulk-download/streaming pipeline used by the broader system.

### Autocomplete and ingestion boundary

The standalone `suggest()` method uses only tiers 0–4, enforces at least three meaningful input characters and caps results at eight. It performs **no external API calls and no ingestion**. That is distinct from a submitted full search, where contained-phrase and trigram branches may be used.

A record's `already_ingested` field comes only from `osint.ingested_company`, not from search catalogue membership. In this sample there is no way for search or suggestion selection to start an ingestion job.

## Relevance methodology

[`benchmark.py`](../src/osint_research/company_discovery/benchmark.py) scores **explicitly graded synthetic cases**, using Top-1, mean reciprocal rank, recall and normalised discounted cumulative gain. [`examples/relevance_judgments.json`](../examples/relevance_judgments.json) contains a small self-contained illustration. Fake-cursor tests exercise ordering contracts, not PostgreSQL execution plans or indexing costs. The optional [PostgreSQL candidate-cap regression](../tests/test_postgres_candidate_limit.py) executes the actual SQL over **2,106 synthetic matching names** (current and former-name prefixes), checking that the shortest match inserted last survives the 2,000-row cap and that repeated executions return the same first page. A second real-PostgreSQL regression checks that 24 prefix-matching former names for one registration cannot crowd out six other registrations from a five-result page. The tests use **session-local temporary tables** rather than writing to the canonical `osint` schema.

Run it against a disposable local database with `OSINT_TEST_PG_DSN=postgresql://postgres:postgres@localhost:5432/postgres python -m pytest -q tests/test_postgres_candidate_limit.py`. Without that environment variable it skips. These correctness checks are not query-plan or latency benchmarks.

A credible production benchmark would require a human-adjudicated, date-pinned corpus of real registration identities, repeated query executions, `EXPLAIN (ANALYZE, BUFFERS)` plans and measured p50/p95/p99 latencies. **No such performance results are asserted here.**

## Engineering choices worth discussing in interviews

- **PostgreSQL rather than an additional search cluster:** sufficient indexing and bounded SQL can simplify operations while supporting the retrieval requirements. A separate engine would need a like-for-like measured comparison before adoption.
- **Fast path versus fuzzy path:** exact registration and current names should not pay the cost of large trigram candidate scans.
- **Canonical identifier versus names:** mergers, rebranding and similar company names cannot be resolved safely by display text alone.
- **Search versus ingestion:** a registry result is only a candidate for an explicit, separate data-processing operation.
- **Atomic publication:** maintaining one consistent generation across current-name and historical-name indexes matters more than updating either in isolation.

## Source boundaries

This publication contains **adapted extracts**, not a complete production implementation. Schema/table names, result payloads, database integration points and the small fixture loader have been simplified for standalone study. The external registry API, the original service router, frontend components, bulk downloader, task orchestration, operational telemetry and private datasets are **intentionally excluded**.

The code should be assessed as evidence of algorithm and system-design techniques, not as a deployed end-to-end platform.
