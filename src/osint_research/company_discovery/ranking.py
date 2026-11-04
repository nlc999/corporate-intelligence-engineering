"""Deterministic nine-tier registration relevance ranking.

Adapted from the implemented v2 ranking code. Search matching is performed by
indexed PostgreSQL tiers; this module orders their bounded returned candidates.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import CompanySearchResult
from .normalisation import normalise_company_name

RANK = {
    "number_exact": 0,
    "current_exact": 1,
    "previous_exact": 2,
    "current_prefix": 3,
    "previous_prefix": 4,
    "current_phrase": 5,
    "previous_phrase": 6,
    "current_fuzzy": 7,
    "previous_fuzzy": 8,
}

_NUMBER_PATTERN = re.compile(r"^[A-Z]{0,2}[0-9]{6,8}$")


def looks_like_company_number(query: str) -> bool:
    return bool(_NUMBER_PATTERN.fullmatch(query.strip().upper().replace(" ", "")))


def classify_current_match(query: str, result: CompanySearchResult) -> str:
    compact = query.strip().upper().replace(" ", "")
    if looks_like_company_number(query) and compact == result.company_number:
        return "number_exact"
    normalized_query = normalise_company_name(query)
    normalized_name = normalise_company_name(result.company_name)
    if normalized_query == normalized_name:
        return "current_exact"
    if normalized_name.startswith(normalized_query.rstrip()):
        return "current_prefix"
    if normalized_query in normalized_name:
        return "current_phrase"
    return "current_fuzzy"


def rank_key(query: str, result: CompanySearchResult) -> tuple:
    """Return the same relevance/tie-break ordering as indexed Search v2."""
    kind = result.match_kind or classify_current_match(query, result)
    normalized_query = normalise_company_name(query).strip()
    matched = normalise_company_name(result.matched_name or result.company_name).strip()
    similarity = (
        SequenceMatcher(None, normalized_query, matched).ratio()
        if kind.endswith("_fuzzy") else 0.0
    )
    return (
        RANK[kind],
        -similarity,
        max(0, len(matched) - len(normalized_query)),
        len(matched),
        normalise_company_name(result.company_name),
        result.company_number,
    )
