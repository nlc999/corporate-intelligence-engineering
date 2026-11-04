"""Reusable evaluation of event-type labels, with per-class results."""
from __future__ import annotations

from typing import Sequence

from .labels import EVENT_LABELS


def evaluate_predictions(y_true: Sequence[str], y_pred: Sequence[str]) -> dict:
    if not y_true or len(y_true) != len(y_pred):
        raise ValueError("Need nonempty equally sized label sequences")
    if not set(y_true).issubset(EVENT_LABELS) or not set(y_pred).issubset(EVENT_LABELS):
        raise ValueError("Unexpected event class in evaluation")
    try:
        from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
    except ImportError as exc:
        raise RuntimeError("Evaluation needs the optional ML dependencies") from exc

    report = classification_report(y_true, y_pred, labels=list(EVENT_LABELS),
                                   output_dict=True, zero_division=0)
    return {
        "n_test": len(y_true),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(report["macro avg"]["f1-score"]),
        "per_class": {label: report[label] for label in EVENT_LABELS},
        "classes": list(EVENT_LABELS),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=list(EVENT_LABELS)).tolist(),
    }
