"""Production-inspired policy contracts; independent of external model weights."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from osint_research.information_extraction.classifier import InferenceUnavailableError
from osint_research.llm_orchestration import Candidate, Prediction, route, assess_quality, classify_and_route


def candidate(amount=2_000_000, sentence="Alder Renewables invested £2 million in an additional factory.", **kw):
    fields = dict(source_id="s", sentence=sentence, company_name="Alder Renewables",
                  company_number="00000001", amount_gbp=amount, financial_cue=True)
    fields.update(kw)
    return Candidate(**fields)


def test_quality():
    assert assess_quality(candidate())[0] == "STRONG"
    assert assess_quality(candidate(amount=600_000))[0] == "WEAK"
    assert assess_quality(candidate(amount=12_000))[0] == "NOISE"


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        ({"amount": 12_000}, "NOISE_VALUE_BELOW_MIN"),
        ({"attribution": "other_company"}, "ATTRIBUTION_BLEED_SUPPRESSED"),
        ({"amount": 12_000, "attribution": "other_company"}, "NOISE_VALUE_BELOW_MIN"),
    ],
)
def test_rejected_candidate_skips_classifier_inference(changes, expected_reason):
    class NeverPredict:
        def predict(self, sentence):
            pytest.fail("Pre-rejected candidate must not call the classifier")

    result = classify_and_route(candidate(**changes), NeverPredict())
    assert result.final_status == "rejected"
    assert result.reason_code == expected_reason
    assert result.prediction is None
    assert (result.threshold is None) == (expected_reason == "NOISE_VALUE_BELOW_MIN")


def test_eligible_candidate_calls_classifier_once():
    seen = []

    class CountingClassifier:
        def predict(self, sentence):
            seen.append(sentence)
            return SimpleNamespace(event_type="profit", probability=0.8)

    c = candidate()
    result = classify_and_route(c, CountingClassifier())
    assert result.final_status == "local"
    assert result.reason_code == "LOCAL_CONFIDENCE_ACCEPTED"
    assert seen == [c.sentence]


def test_strong_threshold_boundary():
    assert route(candidate(), Prediction("profit", 0.449)).final_status == "local_pending"
    assert route(candidate(), Prediction("profit", 0.45)).final_status == "local"


def test_weak_threshold_boundary():
    weak = candidate(amount=800_000)
    assert route(weak, Prediction("profit", 0.549)).final_status == "local_pending"
    assert route(weak, Prediction("profit", 0.55)).final_status == "local"


def test_event_gate_and_amount_guard():
    weak = candidate(amount=800_000)
    assert route(weak, Prediction("capex_cancellation", 0.43)).reason_code == "EVENT_SPECIFIC_GATE_LOCAL"
    ambiguous = replace(weak, attribution="ambiguous")
    assert route(ambiguous, Prediction("capex_cancellation", 0.43)).final_status == "local"


def test_different_company_never_promoted():
    other = candidate(attribution="other_company")
    assert route(other, Prediction("capex_investment", 0.82)).reason_code == "ATTRIBUTION_BLEED_SUPPRESSED"


def test_not_financial_event_rejected():
    assert route(candidate(), Prediction("not_financial_event", 0.1)).final_status == "rejected"


def test_unattributed_executive_hard_drop():
    assert route(candidate(named_person=False), Prediction("executive_compensation", 0.2)).reason_code == "EXEC_COMP_NO_NAMED_PERSON"


def test_exception_does_not_fake_a_gemini_request():
    result = route(candidate(), None, classifier_error="ModelLoadingError")
    assert result.final_status == "degraded_pending"
    assert result.reason_code == "CLASSIFIER_EXCEPTION"


def test_case_study_classifier_adapter():
    class Stub:
        def predict(self, sentence):
            return SimpleNamespace(event_type="profit", probability=0.8)
    assert classify_and_route(candidate(), Stub()).final_status == "local"


def test_expected_inference_failure_degrades_with_safe_diagnostics(caplog):
    class Unavailable:
        def predict(self, sentence):
            raise InferenceUnavailableError("private backend detail") from OSError(
                "sensitive connection string"
            )

    result = classify_and_route(candidate(source_id="trace-17"), Unavailable())
    assert result.final_status == "degraded_pending"
    assert result.reason_code == "CLASSIFIER_EXCEPTION"
    assert result.reason_detail == "InferenceUnavailableError:OSError"
    assert "trace-17" in caplog.text
    assert "cause_type=OSError" in caplog.text
    assert "private backend detail" not in caplog.text
    assert "sensitive connection string" not in caplog.text


def test_unexpected_programming_exception_is_not_hidden(caplog):
    class Buggy:
        def predict(self, sentence):
            raise RuntimeError("unexpected defect")

    with pytest.raises(RuntimeError, match="unexpected defect"):
        classify_and_route(candidate(), Buggy())
    assert "Classifier inference unavailable" not in caplog.text


def test_invalid_inference_result_is_not_treated_as_outage():
    class InvalidProbability:
        def predict(self, sentence):
            return SimpleNamespace(event_type="profit", probability=1.7)

    with pytest.raises(ValueError, match="Confidence"):
        classify_and_route(candidate(), InvalidProbability())

    class MissingAttribute:
        def predict(self, sentence):
            return object()

    with pytest.raises(AttributeError):
        classify_and_route(candidate(), MissingAttribute())


def test_emergency_disable_recorded():
    r = route(candidate(), Prediction("profit", 0.2), escalation_disabled=True)
    assert r.final_status == "local" and r.reason_code == "ESCALATION_DISABLED_LOCAL"
