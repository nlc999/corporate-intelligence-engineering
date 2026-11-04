"""Company-name normalisation from the indexed registry search implementation.

Keep this function in step with the PostgreSQL generated-column expression.
"""

from __future__ import annotations

import re


def normalise_company_name(value: str) -> str:
    """Collapse punctuation and lower-case; pad the indexed search key.

    The explicit padding permits whole-phrase matches at either edge of a name.
    This is an adapted extract of the original catalogue normalisation routine.
    """
    collapsed = re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()
    return f" {collapsed} " if collapsed else " "


def meaningful_length(value: str) -> int:
    """Count meaningful characters rather than indexing whitespace alone."""
    return len(normalise_company_name(value).strip())
