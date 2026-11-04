"""Isolated adaptation of financial-candidate quality assessment.

Amounts are already normalised into GBP in this demonstrator. This is not a
currency-conversion, news ingestion or real-world relevance model.
"""
from __future__ import annotations

from .models import Candidate

MIN_VALUE_GBP = 500_000
STRONG_VALUE_GBP = 1_000_000
STRONG_SENTENCE_CHARS = 40


def assess_quality(candidate: Candidate) -> tuple[str, tuple[str, ...]]:
    if candidate.amount_gbp < MIN_VALUE_GBP:
        return "NOISE", ("NOISE_VALUE_BELOW_MIN",)
    misses: list[str] = []
    if candidate.amount_gbp < STRONG_VALUE_GBP:
        misses.append("WEAK_VALUE")
    if len(candidate.sentence) < STRONG_SENTENCE_CHARS:
        misses.append("WEAK_SENTENCE_TOO_SHORT")
    if not candidate.financial_cue:
        misses.append("WEAK_NO_FINANCE_CUE")
    if not misses:
        return "STRONG", ("STRONG_ALL_CRITERIA_MET",)
    return "WEAK", tuple(misses)
