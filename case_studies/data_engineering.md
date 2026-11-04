# Case Study 04 — Multi-Source ETL & Data Quality Engineering

> **Selected source extracts, not a copied application.** This case study publishes two small Python adaptations from a larger privately maintained corporate-intelligence ingestion system. Both run offline with invented fixtures. They illustrate distinct architectural boundaries: the property-register transform does **not** directly call the temporal policy.

## Problem and system context

Public corporate information has no universal record shape. A company-register snapshot contains legal identities; a property register can place several proprietors in one CSV row; company news often describes events without a reliable date. A robust pipeline must preserve the meaning, origin and time of evidence while turning source-specific data into useful relational information.

The broader ETL architecture separates source acquisition, durable raw evidence, parsing, resolution, canonical materialisation and analytics. This separation allows sources to evolve without forcing business rules into their crawlers.

~~~mermaid
flowchart LR
    A[External sources] --> B[Acquisition and source evidence]
    B --> C[Source-specific parsing and normalisation]
    C --> D[Ingest facts and unresolved candidates]
    D --> E[Validation and entity resolution]
    E --> F[Canonical data]
    F --> G[Analytics and read models]
    P[Source provenance and snapshot dates] -.-> B
    P -.-> C
    T[Temporal acceptance policy] -.-> E
~~~

**Published examples:** [ownership transformation](../src/osint_research/data_engineering/land_registry_transform.py) represents step C; [temporal policy](../src/osint_research/data_engineering/temporal_policy.py) represents a rule at step E. The other layers are documented context, **not** implemented by these two files.

## A — Stream, reshape and retain source context

The property-register sample draws from a bulk CCOD pipeline. Raw snapshots may contain up to four separate owner fields for one property. Keeping that shape in an analytics table would require four parallel sets of columns and special-case queries. The extractor therefore produces **one ownership record per actual proprietor**, preserving a common property title, owner position, registration number when provided, monthly snapshot date and source filename.

The extract retains source fields in the record's raw dictionary. This is valuable when upstream field formats change or a downstream record is challenged: engineers can reconstruct what the parser saw instead of trusting only a transformed output. A missing company number stays **unknown**; the demonstration never guesses identity from a company name.

The ZIP reader wraps the archive's CSV stream in a DictReader and yields each normalised record without loading all CSV rows into memory. The explicit monthly selection function supports backdated work, ignores change-only files and limits the number of requested snapshots. For this isolated sample, an archive containing multiple CSVs or missing the title header **fails closed** instead of choosing a potentially incorrect file.

### Synthetic before / after

| Source title | Proprietor 1 | Proprietor 2 | Monthly snapshot |
| --- | --- | --- | --- |
| SYN100 | Alder Properties Ltd | Northbridge Holdings Ltd | 2025-02 |

Becomes two independent rows with the **same** property title, snapshot month (2025-02-01), source filename (CCOD_FULL_2025_02.zip) and distinct proprietor indices (1 and 2). These entities and identifiers are invented.

## B — Separate extraction from business acceptance

The second file isolates a different failure mode: earlier temporal filters had been implemented inconsistently across different parsers. A regex might discard an old date before it could be recorded as evidence; another parser could let an old operational claim enter canonical state. The solution is **one documented acceptance decision function** invoked at materialisation boundaries, not scattered extraction heuristics.

Its default cutoff is the year **2000**. An explicit year or date takes precedence over nearby candidate years; otherwise, any candidate year on or after the cutoff permits materialisation. If all candidate years predate the cutoff, materialisation is rejected. Entirely undated evidence is accepted by default, but consumers can explicitly request strict dating. Calls can override the cutoff only where a table-specific exception is justified.

The function returns a structured **accepted/rejected verdict, stable reason code and evidence year**. This makes results auditable and testable, rather than hiding business logic inside a boolean predicate. Importantly, *rejected canonical materialisation does not imply deletion of the underlying source evidence*.

## Engineering trade-offs and what remains outside the extract

- **Source-specific transformation versus generic parsing:** an explicit proprietor-row mapper is less abstract but preserves source semantics and handles numbered columns predictably.
- **Streaming versus whole-file loading:** the reader yields rows incrementally. It still assumes a trusted archive and does not implement a production-grade decompression-size guard, object storage or bulk insert.
- **Snapshots versus current-state overwrite:** retaining the snapshot month avoids presenting past ownership as an unconditional current fact.
- **Central rule versus duplicated checks:** consistent decisions and reason codes reduce parser drift, but the cutoff represents a particular downstream analytical scope, not a universal claim that older data is invalid.
- **Failure recovery:** a production pipeline must track **successful file completion** in a dedicated transactional ledger, not infer completion merely because one row from the file exists. That stronger ledger is **not implemented** in this extract.
- **Publishing completeness:** the separate company-search case study demonstrates staged snapshot validation and atomic catalogue/index publication. Here we intentionally avoid duplicating its database and orchestration code.

## Reproducibility and boundaries

No network access, credentials, database or actual Land Registry records are required.

~~~bash
python -m pip install -e '.[dev]'
python -m pytest -q tests/data_engineering
~~~

The tests cover monthly selection, ZIP parsing, missing identifiers, one-to-many normalisation, preserved provenance, malformed archives and the precedence/strictness of temporal evidence. These are **unit tests of isolated adaptations**, not throughput benchmarks or end-to-end validation of a live ingestion system.

**CV linkage:** The work supports claims about modular Python ETL design, streaming source-file ingestion, relational normalisation, historical snapshot semantics and centralised data-quality rules. It does not by itself prove that a complete, scalable multi-source platform is published here.
