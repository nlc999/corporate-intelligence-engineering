#!/usr/bin/env python3
"""Score PostgreSQL search observations against *synthetic* graded judgments."""

from __future__ import annotations

import json
import os
from pathlib import Path

from osint_research.company_discovery.benchmark import evaluate_cases
from osint_research.company_discovery.search import PostgresCompanySearch

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    import psycopg

    judged = json.loads((ROOT / "examples/relevance_judgments.json").read_text())
    engine = PostgresCompanySearch(lambda: psycopg.connect(
        os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"),
        autocommit=False,
    ))
    observations = {
        case["id"]: [row.company_number for row in engine.search(case["query"], limit=10)]
        for case in judged["cases"]
    }
    print(json.dumps(evaluate_cases(judged["cases"], observations), indent=2))
    print("Synthetic-fixture relevance only; not a production accuracy or latency claim.")


if __name__ == "__main__":
    main()
