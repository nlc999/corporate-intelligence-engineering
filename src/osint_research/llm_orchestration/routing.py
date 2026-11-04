"""Deterministic decision routing; never invokes Google, trains ML or queues jobs.

The 0.45/0.55 quality gates and event-specific exception floors reflect the
existing source design; classifier probabilities are NOT calibrated certainty.
"""
from __future__ import annotations

import logging
from typing import Protocol

from ..information_extraction.classifier import InferenceUnavailableError
from .candidate_quality import assess_quality
from .models import Candidate, Prediction, RoutingRecord

STRONG_THRESHOLD = 0.45
WEAK_THRESHOLD = 0.55
EVENT_GATES = {
    "capex_investment": 0.45,
    "capex_cancellation": 0.42,
    "executive_compensation": 0.40,
    "government_support": 0.45,
    "regulatory_action": 0.45,
    "restructuring": 0.45,
}
CAPEX_TYPES = frozenset(("capex_investment", "capex_cancellation"))
log = logging.getLogger(__name__)


class PredictsEvent(Protocol):
    def predict(self, sentence: str): ...


def route(candidate: Candidate, prediction: Prediction | None, *, classifier_error: str | None = None,
          escalation_disabled: bool = False) -> RoutingRecord:
    quality, reasons = assess_quality(candidate)
    threshold = None if quality == "NOISE" else (
        STRONG_THRESHOLD if quality == "STRONG" else WEAK_THRESHOLD
    )

    def make(status: str, code: str, detail: str = "") -> RoutingRecord:
        return RoutingRecord(candidate, prediction, quality, threshold, status, status, code, detail)

    if quality == "NOISE":
        return make("rejected", reasons[0])
    if candidate.attribution == "other_company":
        return make("rejected", "ATTRIBUTION_BLEED_SUPPRESSED")
    if classifier_error is not None or prediction is None:
        # Original worker records a degraded local_pending mention on exception,
        # without necessarily putting it on the Gemini queue.
        return make("degraded_pending", "CLASSIFIER_EXCEPTION", classifier_error or "unavailable")
    if prediction.event_type == "not_financial_event":
        return make("rejected", "NOT_FINANCIAL_EVENT")

    escalating = prediction.confidence < threshold
    if escalating and prediction.event_type in EVENT_GATES:
        floor = EVENT_GATES[prediction.event_type]
        attributed = candidate.attribution != "ambiguous"
        if prediction.event_type == "executive_compensation":
            attributed = attributed and candidate.named_person
        if (prediction.event_type in CAPEX_TYPES and quality == "WEAK"
                and candidate.amount_gbp > 0 and candidate.financial_cue
                and candidate.attribution == "ambiguous"):
            # A verified monetary amount permits the original weak-capex
            # relaxation only after the attribution-bleed rejection above.
            attributed = True
        if prediction.confidence >= floor and attributed:
            escalating = False
            return make("local", "EVENT_SPECIFIC_GATE_LOCAL", f"floor={floor:.2f}")

    if escalating and prediction.event_type == "executive_compensation" and not candidate.named_person:
        return make("rejected", "EXEC_COMP_NO_NAMED_PERSON")
    if escalating and escalation_disabled:
        return make("local", "ESCALATION_DISABLED_LOCAL")
    if escalating:
        code = "QUALITY_STRONG_LOW_CONFIDENCE" if quality == "STRONG" else "QUALITY_WEAK_LOW_CONFIDENCE"
        return make("local_pending", code, f"{prediction.confidence:.3f} < {threshold:.3f}")
    return make("local", "LOCAL_CONFIDENCE_ACCEPTED")


def classify_and_route(candidate: Candidate, classifier: PredictsEvent, *,
                       escalation_disabled: bool = False) -> RoutingRecord:
    """Apply cheap quality/attribution gates before expensive model inference.

    The fixture-only route() API independently checks the same gates; keep
    that policy authoritative for both the bypass and classified paths.
    """
    quality, _ = assess_quality(candidate)
    if quality == "NOISE" or candidate.attribution == "other_company":
        # No inference, Gemini escalation or false "classifier outage" on
        # evidence already rejected by deterministic business rules.
        return route(candidate, None)
    try:
        result = classifier.predict(candidate.sentence)
    except InferenceUnavailableError as exc:
        # Only an explicitly classified infrastructure outage can be degraded.
        # Keep type-only diagnostics; never log private input or exception text.
        cause_name = type(exc.__cause__).__name__ if exc.__cause__ else "unspecified"
        log.warning(
            "Classifier inference unavailable (source_id=%s, cause_type=%s)",
            candidate.source_id, cause_name,
        )
        return route(
            candidate, None,
            classifier_error=f"InferenceUnavailableError:{cause_name}",
        )
    # Validation and programmer errors must propagate with their original stack.
    prediction = Prediction(event_type=result.event_type, confidence=result.probability)
    return route(candidate, prediction, escalation_disabled=escalation_disabled)
