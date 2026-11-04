"""Small transparent Tier-1 monetary mention detector for invented GBP statements.

This is a deliberately limited standalone adaptation. It does NOT resolve
co-referents, convert currencies, or correctly identify the primary event when
multiple amounts appear; those are broader ingestion concerns.
"""
from __future__ import annotations

import re

from .models import Candidate

_GBP_AMOUNT = re.compile(
    r"(?:£|GBP\s*)(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<unit>billion|million|thousand|bn|m|k)?\b",
    re.IGNORECASE,
)
_UNITS = {None: 1, "k": 1_000, "thousand": 1_000,
          "m": 1_000_000, "million": 1_000_000,
          "bn": 1_000_000_000, "billion": 1_000_000_000}
_FINANCE_CUE = re.compile(
    r"\b(?:invest\w*|acquir\w*|purchas\w*|capex|cancel\w*|"
    r"profit\w*|revenue|sales|restructur\w*|divest\w*|contract\w*|"
    r"capital\s+expenditure)\b", re.IGNORECASE,
)


def detect_candidate(text: str, *, source_id: str, company_name: str,
                     company_number: str) -> Candidate:
    """Extract the first explicit GBP amount and note subject-name presence.

    Caller must provide source and subject company; this function does NOT
    establish actor identity beyond surface text matching.
    """
    match = _GBP_AMOUNT.search(text)
    if not match:
        raise ValueError("No GBP monetary candidate found")
    number = float(match.group("number").replace(",", ""))
    unit = match.group("unit")
    amount = number * _UNITS[unit.lower() if unit else None]
    return Candidate(
        source_id=source_id,
        sentence=text.strip(),
        company_name=company_name,
        company_number=company_number,
        amount_gbp=amount,
        financial_cue=bool(_FINANCE_CUE.search(text)),
        attribution="subject" if company_name.casefold() in text.casefold() else "ambiguous",
    )
