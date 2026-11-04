"""Central acceptance policy for extracted facts with temporal evidence.

A focused adaptation of a production ETL policy boundary: preserve raw
source facts independently, but gate promotion into canonical analytics.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

MIN_MATERIALISABLE_YEAR = 2000

REASON_ACCEPT_EXPLICIT = "temporal_policy_accept_explicit"
REASON_ACCEPT_CANDIDATE_YEAR = "temporal_policy_accept_candidate_year"
REASON_ACCEPT_DATELESS = "temporal_policy_accept_dateless"
REASON_REJECT_MIN_YEAR = "temporal_policy_reject_min_year"
REASON_REJECT_ALL_CANDIDATES_PRE_MIN_YEAR = (
    "temporal_policy_reject_all_candidates_pre_min_year"
)
REASON_REJECT_DATELESS_STRICT = "temporal_policy_reject_dateless_strict"


@dataclass(frozen=True)
class TemporalPolicyDecision:
    accepted: bool
    reason_code: str
    min_year: int
    evidence_year: int | None = None


def assess_temporal_materialisation(
    *,
    explicit_year: int | None = None,
    explicit_date: date | None = None,
    candidate_years: Iterable[int | str] | None = None,
    allow_dateless: bool = True,
    min_year: int = MIN_MATERIALISABLE_YEAR,
) -> TemporalPolicyDecision:
    """Return a reasoned acceptance decision, never mutate source evidence.

    An explicit date/year takes priority. Without one, consider candidate
    years near the extracted fact; completely dateless evidence is accepted
    by default, unless a consumer requires strict dating.
    """
    if explicit_date is not None:
        explicit_year = explicit_date.year

    if explicit_year is not None:
        if explicit_year < min_year:
            return TemporalPolicyDecision(
                False, REASON_REJECT_MIN_YEAR, min_year, explicit_year
            )
        return TemporalPolicyDecision(
            True, REASON_ACCEPT_EXPLICIT, min_year, explicit_year
        )

    parsed_candidates: list[int] = []
    for raw in candidate_years or []:
        try:
            parsed_candidates.append(int(raw))
        except (TypeError, ValueError):
            continue

    if parsed_candidates:
        qualifying = [year for year in parsed_candidates if year >= min_year]
        if qualifying:
            return TemporalPolicyDecision(
                True, REASON_ACCEPT_CANDIDATE_YEAR, min_year, max(qualifying)
            )
        return TemporalPolicyDecision(
            False,
            REASON_REJECT_ALL_CANDIDATES_PRE_MIN_YEAR,
            min_year,
            max(parsed_candidates),
        )

    if allow_dateless:
        return TemporalPolicyDecision(True, REASON_ACCEPT_DATELESS, min_year)
    return TemporalPolicyDecision(False, REASON_REJECT_DATELESS_STRICT, min_year)
