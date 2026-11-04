"""Snapshot-consistent PostgreSQL company discovery with bounded retrieval.

Adapted from an existing nine-tier company-discovery search implementation.
This module deliberately omits external registry API and ingestion side effects.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import CompanySearchResult
from .normalisation import meaningful_length, normalise_company_name
from .ranking import looks_like_company_number, rank_key
from .sql_queries import CANDIDATE_CAP, SUGGEST_TIERS, TIERS

MAX_SEARCH_RESULTS = 50
MAX_SUGGEST_RESULTS = 8


def _read_dict_rows(cursor: Any) -> list[dict[str, Any]]:
    """Convert psycopg's ordinary tuple cursor to dictionaries, without extras."""
    names = [column.name for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


class PostgresCompanySearch:
    """Read-only search. conn_factory supplies a non-autocommit connection.

    An explicit read-only REPEATABLE READ transaction avoids mixing the two
    snapshot generations while a catalogue/alias refresh swaps physical tables.
    """

    def __init__(self, conn_factory: Callable[[], Any]) -> None:
        self._conn_factory = conn_factory

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        suggest_only: bool = False,
        include_previous: bool = True,
    ) -> list[CompanySearchResult]:
        cleaned = query.strip()
        cap = MAX_SUGGEST_RESULTS if suggest_only else MAX_SEARCH_RESULTS
        limit = min(max(1, int(limit)), cap)
        length = meaningful_length(cleaned)
        if not cleaned or (suggest_only and length < 3):
            return []
        number_query = cleaned.upper().replace(" ", "") if looks_like_company_number(cleaned) else ""
        if not length and not number_query:
            return []

        norm_query = normalise_company_name(cleaned)
        params = {
            "norm_query": norm_query,
            "number_query": number_query,
            "prefix_query": f"{norm_query.rstrip()}%",
            "contains_query": f"%{norm_query}%",
        }
        seen: set[str] = set()
        results: list[CompanySearchResult] = []
        tiers = SUGGEST_TIERS if suggest_only else TIERS

        with self._conn_factory() as conn:
            if conn.autocommit:
                raise ValueError("Search connection must have autocommit=False")
            with conn.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            for kind, sql in tiers:
                if not include_previous and kind.startswith("previous_"):
                    continue
                if kind == "number_exact" and not number_query:
                    continue
                if number_query and kind != "number_exact":
                    continue
                if kind.endswith(("_phrase", "_fuzzy")) and length < 3:
                    continue
                remaining = limit - len(results)
                if remaining <= 0:
                    break
                with conn.cursor() as cursor:
                    cursor.execute(sql, {
                        **params,
                        "exclude": list(seen),
                        "fetch_limit": min(CANDIDATE_CAP, remaining * 4),
                    })
                    rows = _read_dict_rows(cursor)
                for row in rows:
                    company_number = row["company_number"]
                    if company_number in seen:
                        continue
                    seen.add(company_number)
                    results.append(CompanySearchResult.from_row(row, kind))
                    if len(results) >= limit:
                        break

        return sorted(results, key=lambda row: rank_key(cleaned, row))

    def suggest(self, query: str, *, limit: int = 8) -> list[CompanySearchResult]:
        """Use only exact/prefix tiers, never fuzzy scans or external API calls."""
        if len(query.strip()) > 120:
            return []
        return self.search(query, limit=limit, suggest_only=True)
