"""Optional real-PostgreSQL regression for capped search candidate ranking.

Uses session-local TEMP tables, never mutates the real osint schema. Set
OSINT_TEST_PG_DSN only to a test/development database. Unlike the fake-cursor
unit tests, these assertions execute the real ordered SQL against >2,000 matches.
"""
from __future__ import annotations

import os

import pytest

from osint_research.company_discovery.sql_queries import CANDIDATE_CAP, TIERS


@pytest.mark.parametrize("tier", ("current_prefix", "previous_prefix"))
def test_best_candidate_survives_more_matches_than_candidate_cap(tier: str) -> None:
    dsn = os.environ.get("OSINT_TEST_PG_DSN")
    if not dsn:
        pytest.skip("Set OSINT_TEST_PG_DSN to opt into real PostgreSQL tests")
    psycopg = pytest.importorskip("psycopg")
    assert CANDIDATE_CAP == 2000
    match_count = CANDIDATE_CAP + 105
    snapshot = "2026-01-01"

    with psycopg.connect(dsn, autocommit=False) as conn:
        # Everything is local to this database session and dropped on commit.
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE company_catalogue (
                    company_number text, company_name text, company_status text,
                    locality text, snapshot_date date, company_name_normalised text
                ) ON COMMIT DROP
            """)
            cur.execute("""
                CREATE TEMP TABLE company_previous_name_search (
                    company_number text, previous_name text,
                    snapshot_date date, previous_name_normalised text
                ) ON COMMIT DROP
            """)
            cur.execute("""
                CREATE TEMP TABLE ingested_company (
                    company_number text PRIMARY KEY
                ) ON COMMIT DROP
            """)
            current = tier == "current_prefix"
            catalogue = []
            aliases = []
            for i in range(match_count):
                number = f"TEST{i:06d}"
                matching_name = f"ALDER LONG CORPORATE HOLDINGS NUMBER {i:06d}"
                company_name = matching_name if current else f"UNRELATED LEGAL NAME {i:06d}"
                catalogue.append((
                    number, company_name, "active", "Synthetic", snapshot,
                    f" {company_name.lower()} ",
                ))
                if not current:
                    aliases.append((number, matching_name, snapshot, f" {matching_name.lower()} "))

            # Deliberately insert the *most relevant* (shortest) match last.
            best_name = "ALDER A"
            catalogue.append((
                "BESTMATCH", best_name if current else "ANOTHER REGISTERED NAME",
                "active", "Synthetic", snapshot,
                " alder a " if current else " another registered name ",
            ))
            if not current:
                aliases.append(("BESTMATCH", best_name, snapshot, " alder a "))

            cur.executemany(
                "INSERT INTO pg_temp.company_catalogue VALUES (%s, %s, %s, %s, %s, %s)",
                catalogue,
            )
            if aliases:
                cur.executemany(
                    "INSERT INTO pg_temp.company_previous_name_search VALUES (%s, %s, %s, %s)",
                    aliases,
                )

            table = "company_catalogue" if current else "company_previous_name_search"
            name_col = "company_name_normalised" if current else "previous_name_normalised"
            cur.execute(
                f"SELECT count(*) FROM pg_temp.{table} WHERE {name_col} LIKE %s",
                (" alder%",),
            )
            assert cur.fetchone()[0] == match_count + 1 > CANDIDATE_CAP

            sql = dict(TIERS)[tier].replace("osint.", "pg_temp.")
            params = {
                "norm_query": " alder ",
                "number_query": "",
                "prefix_query": " alder%",
                "contains_query": "% alder %",
                "exclude": [],
                "fetch_limit": 5,
            }
            first_result = None
            for _ in range(3):
                cur.execute(sql, params)
                rows = cur.fetchall()
                assert len(rows) == 5
                assert rows[0][0] == "BESTMATCH"
                assert len(set(row[0] for row in rows)) == len(rows)
                if first_result is None:
                    first_result = rows
                else:
                    assert rows == first_result



def test_many_previous_names_cannot_crowd_out_distinct_companies() -> None:
    """One registration with many good aliases must occupy only one page slot."""
    dsn = os.environ.get("OSINT_TEST_PG_DSN")
    if not dsn:
        pytest.skip("Set OSINT_TEST_PG_DSN to opt into real PostgreSQL tests")
    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE company_catalogue (
                    company_number text, company_name text, company_status text,
                    locality text, snapshot_date date, company_name_normalised text
                ) ON COMMIT DROP
            """)
            cur.execute("""
                CREATE TEMP TABLE company_previous_name_search (
                    company_number text, previous_name text,
                    snapshot_date date, previous_name_normalised text
                ) ON COMMIT DROP
            """)
            cur.execute("""
                CREATE TEMP TABLE ingested_company (
                    company_number text PRIMARY KEY
                ) ON COMMIT DROP
            """)
            snapshot = "2026-01-01"
            companies = [("CROWDED", "CURRENT CROWDED NAME", "active", "", snapshot,
                          " current crowded name ")]
            for i in range(6):
                companies.append((f"OTHER{i}", f"OTHER CURRENT NAME {i}",
                                  "active", "", snapshot, f" other current name {i} "))
            cur.executemany(
                "INSERT INTO pg_temp.company_catalogue VALUES (%s, %s, %s, %s, %s, %s)",
                companies,
            )
            aliases = [
                ("CROWDED", "ALDER " + "A" * n, snapshot,
                 " alder " + "a" * n + " ")
                for n in range(1, 25)
            ]
            aliases.extend([
                (f"OTHER{i}", f"ALDER VERY LONG OTHER COMPANY {i}", snapshot,
                 f" alder very long other company {i} ")
                for i in range(6)
            ])
            cur.executemany(
                "INSERT INTO pg_temp.company_previous_name_search "
                "VALUES (%s, %s, %s, %s)",
                aliases,
            )

            sql = dict(TIERS)["previous_prefix"].replace("osint.", "pg_temp.")
            params = {
                "norm_query": " alder ", "number_query": "",
                "prefix_query": " alder%", "contains_query": "% alder %",
                "exclude": [], "fetch_limit": 5,
            }
            for _ in range(3):
                cur.execute(sql, params)
                rows = cur.fetchall()
                assert [row[0] for row in rows] == [
                    "CROWDED", "OTHER0", "OTHER1", "OTHER2", "OTHER3",
                ]
                assert rows[0][4] == "ALDER A"
