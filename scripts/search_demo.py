#!/usr/bin/env python3
"""Run the actual indexed SQL query path against a disposable PostgreSQL DB.

Requires: pip install -e '.[postgres]'
Database schema: sql/company_search.sql (apply once as an admin-capable user).
This script never calls external APIs or starts company ingestion.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from osint_research.company_discovery.historical_names import RegistryCompany, refresh_snapshot
from osint_research.company_discovery.search import PostgresCompanySearch

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic corporate registry search")
    parser.add_argument("action", choices=["seed", "search", "suggest"])
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"))
    args = parser.parse_args()
    import psycopg  # optional runtime dependency; only required for this script

    if args.action == "seed":
        fixture = json.loads((ROOT / "examples/synthetic_companies.json").read_text())
        items = [
            RegistryCompany(
                company_number=r["company_number"], company_name=r["company_name"],
                company_status=r["company_status"], locality=r["locality"],
                previous_names=tuple(r["previous_names"]),
                snapshot_date=date.fromisoformat(fixture["snapshot_date"]),
            ) for r in fixture["companies"]
        ]
        with psycopg.connect(args.dsn, autocommit=False) as conn:
            refresh_snapshot(conn, items)
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE osint.ingested_company")
                cur.executemany(
                    "INSERT INTO osint.ingested_company(company_number) VALUES (%s)",
                    [(number,) for number in fixture["ingested_company_numbers"]],
                )
        print(f"Loaded {len(items)} invented registrations and {len(fixture['ingested_company_numbers'])} ingested IDs")
        return
    if not args.query:
        parser.error("Provide a company name or number to search")
    search = PostgresCompanySearch(lambda: psycopg.connect(args.dsn, autocommit=False))
    results = search.suggest(args.query) if args.action == "suggest" else search.search(args.query)
    for item in results:
        former = f" | formerly: {item.matched_name}" if item.matched_name else ""
        print(f"{item.match_kind:16} {item.company_number} {item.company_name}{former} | ingested={item.already_ingested}")
    if not results:
        print("No matches in the synthetic catalogue")


if __name__ == "__main__":
    main()
