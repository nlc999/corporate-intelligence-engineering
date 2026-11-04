import pytest

from osint_research.company_discovery.benchmark import evaluate_cases, score_case


def test_perfect_retrieval():
    assert score_case({"1": 3, "2": 1}, ["1", "2"])["ndcg_at_10"] == 1.0


def test_rank_penalty():
    result = score_case({"1": 3}, ["2", "1"])
    assert result["top1"] == 0.0
    assert result["mrr_at_10"] == 0.5


def test_empty_expected_and_returned():
    assert score_case({}, [])["top1"] == 1.0


def test_missing_observation_fails_loudly():
    with pytest.raises(ValueError, match="Missing"):
        evaluate_cases([{"id": "a", "relevance": {"1": 3}}], {})
