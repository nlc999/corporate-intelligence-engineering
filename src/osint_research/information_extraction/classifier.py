"""Probability-based financial event classification atop a sentence encoder.

Model classes are read from the trained sklearn head, not positional constants.
The reported probability is an uncalibrated model score, NOT a guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isclose, isfinite
from pathlib import Path

from .embeddings import encode_sentences, load_encoder
from .labels import validate_event_labels

DEFAULT_THRESHOLD = 0.52


class InferenceUnavailableError(RuntimeError):
    """An expected encoder/backend availability failure, safe to degrade.

    Invalid model outputs and programming errors are NOT availability failures.
    Callers can examine the chained exception for the original diagnosis.
    """


@dataclass(frozen=True)
class Prediction:
    event_type: str
    probability: float
    ambiguous: bool


class EventClassifier:
    def __init__(self, encoder, head, *, threshold: float = DEFAULT_THRESHOLD):
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        if not hasattr(head, "predict_proba"):
            raise ValueError("Classifier must expose predict_proba")
        self.classes = validate_event_labels(head.classes_)
        self.encoder, self.head, self.threshold = encoder, head, threshold

    def predict(self, sentence: str) -> Prediction:
        if not sentence.strip():
            raise ValueError("Sentence must not be empty")
        try:
            vector = encode_sentences(self.encoder, [sentence])[0]
            probs = self.head.predict_proba([vector])[0]
        except OSError as exc:
            # I/O, connectivity and unavailable model resources may degrade.
            # Do not hide ValueError, TypeError, AttributeError, RuntimeError,
            # or malformed estimator output as a recoverable inference outage.
            raise InferenceUnavailableError("Classifier inference backend unavailable") from exc
        if len(probs) != len(self.classes):
            raise ValueError("Prediction probabilities do not match classifier labels")
        # Shape is not enough: NaN can corrupt argmax and route ambiguity;
        # negative/non-normalised scores are not class probabilities.
        try:
            scores = [float(value) for value in probs]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Prediction probabilities must be numeric") from exc
        if any(not isfinite(value) or not 0.0 <= value <= 1.0 for value in scores):
            raise ValueError("Prediction probabilities must be finite and within [0, 1]")
        if not isclose(fsum(scores), 1.0, rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError("Prediction probabilities must sum to 1")
        index = max(range(len(scores)), key=scores.__getitem__)
        score = scores[index]
        return Prediction(self.classes[index], score, score < self.threshold)


def load_trusted_head(path: str | Path):
    """Unpickle only trusted artifacts YOU trained, never user-supplied files."""
    import pickle

    with Path(path).open("rb") as handle:
        head = pickle.load(handle)  # noqa: S301 - documented trusted-file boundary
    validate_event_labels(head.classes_)
    return head


def from_artifact(head_path: str | Path, *, threshold: float = DEFAULT_THRESHOLD) -> EventClassifier:
    # Reject invalid or missing artifacts before downloading/loading MiniLM.
    head = load_trusted_head(head_path)
    return EventClassifier(load_encoder(), head, threshold=threshold)
