"""Stable nine-class event taxonomy (adapted from the financial parser)."""
from __future__ import annotations

EVENT_LABELS: tuple[str, ...] = (
    "acquisition",
    "capex_cancellation",
    "capex_investment",
    "cost_cutting",
    "divestment",
    "executive_compensation",
    "not_financial_event",
    "profit",
    "share_buyback",
)


def validate_event_labels(labels: object) -> tuple[str, ...]:
    """Fail closed when a saved model's classes deviate from our taxonomy."""
    try:
        classes = tuple(str(label) for label in labels)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("Classifier classes must be iterable") from exc
    if not classes or len(classes) != len(set(classes)):
        raise ValueError("Classifier classes must be present and unique")
    if not set(classes).issubset(EVENT_LABELS):
        raise ValueError(f"Unknown event classes: {sorted(set(classes) - set(EVENT_LABELS))}")
    return classes
