"""Snapshot-matched alias indexing and all-or-nothing catalogue publication.

A small, runnable adaptation of the indexed alias loading and transactional
paired-table swap used in a larger company discovery implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from .normalisation import normalise_company_name


@dataclass(frozen=True, slots=True)
class RegistryCompany:
    company_number: str
    company_name: str
    company_status: str
    locality: str
    previous_names: tuple[str, ...]
    snapshot_date: date


def aliases_for(company: RegistryCompany) -> list[tuple[str, str, date]]:
    """One row per unique meaningful normalised *former* legal name."""
    current = normalise_company_name(company.company_name)
    seen: set[str] = set()
    result: list[tuple[str, str, date]] = []
    for name in company.previous_names:
        normalised = normalise_company_name(name)
        if normalised == " " or normalised == current or normalised in seen:
            continue
        seen.add(normalised)
        result.append((company.company_number, name, company.snapshot_date))
    return result


def validate_snapshot(companies: Iterable[RegistryCompany]) -> list[RegistryCompany]:
    """Reject ambiguous or cross-generation input before any database mutation."""
    rows = list(companies)
    if not rows:
        raise ValueError("A snapshot must include at least one company")
    dates = {row.snapshot_date for row in rows}
    if len(dates) != 1:
        raise ValueError("Cannot publish mixed snapshot dates")
    numbers = [row.company_number for row in rows]
    if len(set(numbers)) != len(numbers):
        raise ValueError("Duplicate company identifiers in snapshot")
    return rows


def _swap(cursor: Any, live_name: str, staging_name: str) -> None:
    """Rename paired physical tables inside the surrounding transaction."""
    old = f"{live_name}_old"
    # Identifiers are static constants supplied only by refresh_snapshot below.
    cursor.execute(f"ALTER TABLE osint.{live_name} RENAME TO {old}")
    cursor.execute(f"ALTER TABLE osint.{staging_name} RENAME TO {live_name}")
    cursor.execute(f"ALTER TABLE osint.{old} RENAME TO {staging_name}")


def refresh_snapshot(conn: Any, companies: Iterable[RegistryCompany]) -> None:
    """Publish catalogue + aliases atomically via their indexed staging twins.

    Requires schema from sql/company_search.sql and a psycopg 3 connection with
    autocommit disabled. Intended for synthetic demo fixtures, not a bulk ETL
    replacement. The one transaction protects both staging swaps and validations.
    """
    rows = validate_snapshot(companies)
    if conn.autocommit:
        raise ValueError("Snapshot refresh requires autocommit=False")

    with conn.transaction():
        with conn.cursor() as cur:
            # Advisory lock prevents competing refreshers from interleaving.
            cur.execute("SELECT pg_advisory_xact_lock(314159265)")
            cur.execute("TRUNCATE TABLE osint.company_catalogue_staging")
            cur.execute("TRUNCATE TABLE osint.company_previous_name_search_staging")
            cur.executemany(
                """INSERT INTO osint.company_catalogue_staging
                   (company_number, company_name, company_status, locality,
                    previous_names, snapshot_date)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                [
                    (
                        r.company_number, r.company_name, r.company_status,
                        r.locality, list(r.previous_names), r.snapshot_date,
                    ) for r in rows
                ],
            )
            aliases = [alias for company in rows for alias in aliases_for(company)]
            if aliases:
                cur.executemany(
                    """INSERT INTO osint.company_previous_name_search_staging
                       (company_number, previous_name, snapshot_date)
                       VALUES (%s, %s, %s)""",
                    aliases,
                )
            cur.execute("""
                SELECT COUNT(*) FROM osint.company_previous_name_search_staging a
                LEFT JOIN osint.company_catalogue_staging ch
                  ON ch.company_number = a.company_number
                 AND ch.snapshot_date = a.snapshot_date
                WHERE ch.company_number IS NULL
            """)
            if cur.fetchone()[0] != 0:
                raise RuntimeError("Alias index contains an orphan or stale snapshot")
            # Readers in read-only REPEATABLE READ see one consistent version.
            _swap(cur, "company_catalogue", "company_catalogue_staging")
            _swap(cur, "company_previous_name_search", "company_previous_name_search_staging")
