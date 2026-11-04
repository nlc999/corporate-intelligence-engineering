"""Policy boundary: rejected materialisation does not delete source evidence."""

from datetime import date

import pytest

from osint_research.data_engineering.temporal_policy import (
    REASON_ACCEPT_CANDIDATE_YEAR,
    REASON_ACCEPT_DATELESS,
    REASON_ACCEPT_EXPLICIT,
    REASON_REJECT_ALL_CANDIDATES_PRE_MIN_YEAR,
    REASON_REJECT_DATELESS_STRICT,
    REASON_REJECT_MIN_YEAR,
    assess_temporal_materialisation,
)


def test_explicit_year_wins_even_with_newer_candidate_year():
    decision = assess_temporal_materialisation(
        explicit_year=1995, candidate_years=[2024]
    )
    assert decision.accepted is False
    assert decision.reason_code == REASON_REJECT_MIN_YEAR
    assert decision.evidence_year == 1995


def test_explicit_date_has_priority_over_explicit_year():
    decision = assess_temporal_materialisation(
        explicit_year=2025, explicit_date=date(1998, 6, 17)
    )
    assert (decision.accepted, decision.evidence_year) == (False, 1998)


@pytest.mark.parametrize("year", [2000, 2018, 2026])
def test_at_or_after_cutoff_accepted(year):
    decision = assess_temporal_materialisation(explicit_year=year)
    assert decision.accepted and decision.reason_code == REASON_ACCEPT_EXPLICIT


def test_all_historical_candidates_are_rejected():
    decision = assess_temporal_materialisation(candidate_years=["1990", "1998", "bad"])
    assert not decision.accepted
    assert decision.reason_code == REASON_REJECT_ALL_CANDIDATES_PRE_MIN_YEAR
    assert decision.evidence_year == 1998


def test_mixed_candidates_follow_original_policy():
    decision = assess_temporal_materialisation(candidate_years=["1998", "2024", "junk"])
    assert decision.accepted
    assert decision.reason_code == REASON_ACCEPT_CANDIDATE_YEAR
    assert decision.evidence_year == 2024


def test_dateless_allowed_by_default():
    decision = assess_temporal_materialisation()
    assert decision.accepted
    assert decision.reason_code == REASON_ACCEPT_DATELESS
    assert decision.evidence_year is None


def test_dateless_opt_in_strict_rejects():
    decision = assess_temporal_materialisation(allow_dateless=False)
    assert not decision.accepted
    assert decision.reason_code == REASON_REJECT_DATELESS_STRICT


def test_table_specific_year_override_is_explicit():
    decision = assess_temporal_materialisation(explicit_year=1995, min_year=1990)
    assert decision.accepted
    assert decision.min_year == 1990


def test_malformed_years_are_ignored_not_treated_as_historical():
    decision = assess_temporal_materialisation(
        candidate_years=["unknown", "date missing"],
        allow_dateless=False,
    )
    assert not decision.accepted
    assert decision.reason_code == REASON_REJECT_DATELESS_STRICT
