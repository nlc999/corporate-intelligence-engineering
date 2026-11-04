"""Deterministic, indexed-predicate SQL tiers adapted from the search service.

A bounded candidate set is ranked *before* LIMIT. Former-name matches are
deduplicated by registration before the candidate and page caps.
PostgreSQL may still scan/sort more than CANDIDATE_CAP qualifying rows; the cap
limits materialised candidates, not total query work. pg_trgm similarity ranks
fuzzy SQL candidates, which the Python ranking layer can subsequently rerank.
All user input is passed as psycopg parameters. %% escapes the trigram operator.
"""

from __future__ import annotations

CANDIDATE_CAP = 2_000


def tier_sql(predicate: str, *, previous: bool = False, fuzzy: bool = False) -> str:
    join = (
        "JOIN osint.company_previous_name_search a\n"
        "      ON a.company_number = ch.company_number\n"
        "     AND a.snapshot_date = ch.snapshot_date"
        if previous else ""
    )
    matched_name = "a.previous_name" if previous else "NULL::text"
    searched_name = (
        "a.previous_name_normalised" if previous else "ch.company_name_normalised"
    )
    similarity = (
        f"similarity({searched_name}, %(norm_query)s)"
        if fuzzy else "NULL::double precision"
    )
    select = f"""
    SELECT {'DISTINCT ON (ch.company_number)' if previous else ''}
        ch.company_number,
        ch.company_name,
        ch.company_status,
        ch.locality,
        {matched_name} AS matched_name,
        {searched_name} AS searched_name,
        {similarity} AS name_similarity
    FROM osint.company_catalogue ch
    {join}
    WHERE {predicate}
      AND lower(COALESCE(ch.company_status, '')) = 'active'
      AND NOT (ch.company_number = ANY(%(exclude)s))
"""
    if previous:
        # Pick the best alias for EACH company before either LIMIT; otherwise
        # several former names from one company can fill the result window.
        # DISTINCT ON requires company_number to be the first sort expression.
        candidate_cte = f"""
WITH best_aliases AS (
    {select}
    ORDER BY
        ch.company_number,
        name_similarity DESC NULLS LAST,
        length({searched_name}),
        searched_name,
        ch.company_name,
        matched_name
),
cand AS (
    SELECT * FROM best_aliases
    ORDER BY
        name_similarity DESC NULLS LAST,
        length(searched_name),
        searched_name,
        company_name,
        company_number,
        matched_name
    LIMIT {CANDIDATE_CAP}
)
"""
    else:
        candidate_cte = f"""
WITH cand AS (
    {select}
    ORDER BY
        name_similarity DESC NULLS LAST,
        length({searched_name}),
        searched_name,
        ch.company_name,
        ch.company_number,
        matched_name
    LIMIT {CANDIDATE_CAP}
)
"""
    return f"""{candidate_cte}
SELECT
    cand.company_number,
    cand.company_name,
    cand.company_status,
    cand.locality,
    cand.matched_name,
    (c.company_number IS NOT NULL) AS already_ingested
FROM cand
LEFT JOIN osint.ingested_company c ON c.company_number = cand.company_number
ORDER BY
    cand.name_similarity DESC NULLS LAST,
    length(cand.searched_name),
    cand.searched_name,
    cand.company_name,
    cand.company_number,
    cand.matched_name
LIMIT %(fetch_limit)s
"""


TIERS: tuple[tuple[str, str], ...] = (
    ("number_exact", tier_sql("ch.company_number = %(number_query)s")),
    ("current_exact", tier_sql("ch.company_name_normalised = %(norm_query)s")),
    ("previous_exact", tier_sql("a.previous_name_normalised = %(norm_query)s", previous=True)),
    ("current_prefix", tier_sql("ch.company_name_normalised LIKE %(prefix_query)s")),
    ("previous_prefix", tier_sql("a.previous_name_normalised LIKE %(prefix_query)s", previous=True)),
    ("current_phrase", tier_sql("ch.company_name_normalised LIKE %(contains_query)s")),
    ("previous_phrase", tier_sql("a.previous_name_normalised LIKE %(contains_query)s", previous=True)),
    ("current_fuzzy", tier_sql("ch.company_name_normalised %% %(norm_query)s", fuzzy=True)),
    ("previous_fuzzy", tier_sql("a.previous_name_normalised %% %(norm_query)s", previous=True, fuzzy=True)),
)

SUGGEST_KINDS = frozenset({
    "number_exact", "current_exact", "previous_exact", "current_prefix", "previous_prefix"
})
SUGGEST_TIERS = tuple((kind, query) for kind, query in TIERS if kind in SUGGEST_KINDS)
