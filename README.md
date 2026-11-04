# Corporate Intelligence Engineering

**Company search · Financial-text NLP · LLM orchestration · Data engineering · Systems reliability**

A curated, independently reviewable set of **adapted engineering implementations** from a larger privately developed corporate-intelligence research application. Each example isolates a technical problem, records design trade-offs and links to runnable code and tests. This is a technical portfolio, **not** a deployed product or a complete copy of the original application.

> All bundled company names, records, financial events and evaluation fixtures are invented. No confidential company or customer dataset is included.

## Start with three examples

### 01 — [Indexed company discovery and entity resolution](case_studies/company_discovery.md)

**Problem:** Find the correct registered company despite spelling variants, historical names and similar identities.

**Approach:** PostgreSQL B-tree and GIN trigram indexes, nine ordered retrieval tiers, canonical registration identifiers, deterministic candidate selection and snapshot-consistent catalogue publication.

**Inspect:** [Search implementation](src/osint_research/company_discovery/search.py) · [SQL indexes](sql/company_search.sql) · [Historical names](src/osint_research/company_discovery/historical_names.py) · [Tests](tests/test_search.py)

### 02 — [Financial event classification](case_studies/financial_event_classification.md)

**Problem:** Distinguish acquisitions, capital expenditure, divestments and other financial events in ambiguous text.

**Approach:** Pretrained MiniLM sentence embeddings, weighted logistic regression, dataset validation, class-safe inference and precision/recall/F1 evaluation.

**Inspect:** [Training](src/osint_research/information_extraction/training.py) · [Data validation](src/osint_research/information_extraction/dataset.py) · [Evaluation](src/osint_research/information_extraction/evaluation.py) · [Tests](tests/information_extraction/)

### 05 — [Caching and distributed job coordination](case_studies/caching_and_background_jobs.md)

**Problem:** Process concurrent company requests, avoid duplicated active work and recover after worker failures.

**Approach:** Local LRU and Redis caches, token-checked Redis job claims, lease recovery, bounded retries and PostgreSQL advisory-lock coordination.

**Inspect:** [Redis jobs](src/osint_research/platform_engineering/redis_jobs.py) · [PostgreSQL jobs](src/osint_research/platform_engineering/pg_jobs.py) · [Two-tier cache](src/osint_research/platform_engineering/two_tier_cache.py) · [Tests](tests/platform_engineering/)

## Further examples

| Case study | Engineering focus |
| --- | --- |
| [03 — Confidence-routed LLM enrichment](case_studies/llm_orchestration.md) | Quality gates, opt-in Gemini provider, response validation and deterministic failure handling |
| [04 — Multi-source ETL and data quality](case_studies/data_engineering.md) | Streaming ZIP/CSV processing, provenance, temporal rules and explainable rejection |
| [06 — Docker, Bash and S3 archival](case_studies/infrastructure_and_cloud.md) | Multi-stage container build, fail-fast tooling and integrity-checked archival |
| [07 — React, TypeScript and Recharts](frontend/README.md) | [Interactive workforce chart](frontend/src/WorkforceChart.tsx) with precise treatment of reported bounds |

## Reproduce the demonstrations

**Python 3.11+**, offline unit tests:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/demo_llm_orchestration.py
python scripts/demo_platform_engineering.py cache
```

**Company search**, using a disposable local PostgreSQL database:

```bash
docker compose up -d --wait
python -m pip install -e '.[postgres]'
python scripts/search_demo.py seed
python scripts/search_demo.py search 'GREENLEAF ENGINEERING LIMITED'
docker compose down
```

The seed command replaces demonstration table contents. Never run it against a valuable database.

**Frontend**, using Node.js 22+:

```bash
cd frontend
npm install
npm test
npm run build
npm run dev
```

The full MiniLM training example downloads publicly available encoder weights. Live Redis/PostgreSQL integrations, Gemini calls and S3 uploads are optional and require separate configuration. See the relevant case study before enabling them.

## Evidence and scope

- Code and schemas are **adapted, standalone examples**, not untouched production files. System interfaces and private datasets are intentionally omitted.
- Synthetic fixtures demonstrate reproducible behaviour; they do **not** establish real-world model accuracy, production-scale latency or commercial deployment.
- The job processing demonstration uses at-least-once semantics; exactly-once execution and benchmarked scaling are not claimed.
- Cloud archival has offline/mocked tests and an opt-in live path. The examples do not establish that a current cloud production environment is running.
- The case studies explain implementation boundaries, trade-offs and tests. CI should be evaluated from actual runs in **this** repository, not inferred from the presence of workflows.

## Copyright and permitted use

Copyright © 2026 Nils Langlois-Cannon. **All rights reserved.**

This repository is made available for technical review and demonstration. **No open-source or other software licence is granted.** Except as permitted by applicable law or GitHub's Terms of Service, no permission is given to copy, modify, redistribute or commercially use the original code without the copyright holder's prior authorisation.

Third-party software and dependencies remain subject to their respective licences. For permission requests, contact the repository owner through GitHub.
