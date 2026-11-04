"""Pure retrieval metrics for explicitly judged search cases.

Uses the same conventional MRR@10, Recall@10 and nDCG@10 methodology as the
original relevance benchmark. A fixture is not evidence of deployed latency.
"""

from __future__ import annotations

import math
import statistics
from typing import Any


def score_case(relevance: dict[str, int], results: list[str], limit: int = 10) -> dict[str, float]:
    """Score a result list against explicit graded company-number judgments."""
    if not relevance:
        return {"top1": float(not results), "mrr_at_10": 0.0,
                "recall_at_10": 1.0 if not results else 0.0,
                "ndcg_at_10": 1.0 if not results else 0.0}
    top = results[:limit]
    grades = [relevance.get(number, 0) for number in top]
    first = next((i for i, grade in enumerate(grades, 1) if grade > 0), None)
    recall = len(set(top) & relevance.keys()) / len(relevance)
    dcg = sum((2 ** grade - 1) / math.log2(i + 2) for i, grade in enumerate(grades))
    best = sorted(relevance.values(), reverse=True)[:limit]
    ideal = sum((2 ** grade - 1) / math.log2(i + 2) for i, grade in enumerate(best))
    return {
        "top1": float(bool(grades and grades[0])),
        "mrr_at_10": 1 / first if first else 0.0,
        "recall_at_10": recall,
        "ndcg_at_10": dcg / ideal if ideal else 0.0,
    }


def evaluate_cases(cases: list[dict[str, Any]], observations: dict[str, list[str]]) -> dict[str, Any]:
    """Score only judged cases; missing observations are an explicit error."""
    metrics: list[dict[str, float]] = []
    for case in cases:
        if case["id"] not in observations:
            raise ValueError(f"Missing observation: {case['id']}")
        metrics.append(score_case(case["relevance"], observations[case["id"]]))
    if not metrics:
        raise ValueError("At least one judged case is required")
    return {
        "cases": len(metrics),
        **{key: round(statistics.mean(row[key] for row in metrics), 4) for key in metrics[0]},
    }
