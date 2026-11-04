import pytest
from osint_research.information_extraction.classifier import EventClassifier
from osint_research.information_extraction.labels import validate_event_labels


class FakeEncoder:
    def encode(self, sentences, **kwargs):
        return [[float(len(x)), 1.0] for x in sentences]


class FakeHead:
    classes_ = ["acquisition", "not_financial_event", "profit"]

    def predict_proba(self, vectors):
        assert vectors[0][0] > 0
        return [[0.18, 0.22, 0.60]]


def test_classifier_returns_class_probability_ambiguity():
    prediction = EventClassifier(FakeEncoder(), FakeHead()).predict("Operating profit increased")
    assert prediction.event_type == "profit"
    assert prediction.probability == 0.6
    assert not prediction.ambiguous
    assert EventClassifier(FakeEncoder(), FakeHead(), threshold=0.7).predict("Profit grew").ambiguous


def test_taxonomy_invalid_model_classes_fail():
    class BadHead(FakeHead):
        classes_ = ["acquisition", "unknown"]
    with pytest.raises(ValueError, match="Unknown event"):
        EventClassifier(FakeEncoder(), BadHead())
    with pytest.raises(ValueError, match="unique"):
        validate_event_labels(["profit", "profit"])


def test_empty_sentence_and_threshold_rejected():
    c = EventClassifier(FakeEncoder(), FakeHead())
    with pytest.raises(ValueError, match="empty"):
        c.predict(" ")
    with pytest.raises(ValueError, match="threshold"):
        EventClassifier(FakeEncoder(), FakeHead(), threshold=1.1)


def test_backend_io_failure_has_explicit_recoverable_type_and_chained_cause():
    from osint_research.information_extraction.classifier import InferenceUnavailableError

    class OfflineEncoder:
        def encode(self, sentences, **kwargs):
            raise OSError("sensitive backend detail")

    classifier = EventClassifier(OfflineEncoder(), FakeHead())
    with pytest.raises(InferenceUnavailableError, match="backend unavailable") as caught:
        classifier.predict("Operating profit increased")
    assert isinstance(caught.value.__cause__, OSError)
    assert "sensitive" not in str(caught.value)


def test_head_backend_io_failure_is_recoverable():
    from osint_research.information_extraction.classifier import InferenceUnavailableError

    class OfflineHead(FakeHead):
        def predict_proba(self, vectors):
            raise ConnectionError("remote backend disconnected")

    classifier = EventClassifier(FakeEncoder(), OfflineHead())
    with pytest.raises(InferenceUnavailableError) as caught:
        classifier.predict("Operating profit increased")
    assert isinstance(caught.value.__cause__, ConnectionError)


def test_programming_errors_and_invalid_outputs_are_not_availability_failures():
    class BrokenEncoder:
        def encode(self, sentences, **kwargs):
            raise RuntimeError("unexpected implementation defect")

    with pytest.raises(RuntimeError, match="implementation defect"):
        EventClassifier(BrokenEncoder(), FakeHead()).predict("Operating profit increased")

    class MalformedHead(FakeHead):
        def predict_proba(self, vectors):
            return [[0.7]]  # Does not match the fitted class labels.

    with pytest.raises(ValueError, match="do not match"):
        EventClassifier(FakeEncoder(), MalformedHead()).predict("Operating profit increased")



@pytest.mark.parametrize("probabilities", [
    [float("nan"), 0.25, 0.75],
    [0.25, float("inf"), 0.75],
    [0.25, float("-inf"), 0.75],
    [-0.01, 0.26, 0.75],
    [1.01, 0.0, -0.01],
    [0.2, 0.2, 0.2],
    [0.0, 0.0, 0.0],
    ["not-a-number", 0.3, 0.7],
], ids=[
    "nan", "positive-infinity", "negative-infinity", "negative",
    "above-one", "unnormalised", "zero-distribution", "non-numeric",
])
def test_inference_rejects_malformed_probability_distribution(probabilities):
    class InvalidHead(FakeHead):
        def predict_proba(self, vectors):
            return [probabilities]

    with pytest.raises(ValueError, match="Prediction probabilities"):
        EventClassifier(FakeEncoder(), InvalidHead()).predict("Reported profit growth")


def test_roundoff_tolerance_preserves_valid_probability_distribution():
    class RoundedHead(FakeHead):
        def predict_proba(self, vectors):
            return [[0.1800001, 0.2200001, 0.5999995]]

    prediction = EventClassifier(FakeEncoder(), RoundedHead()).predict("Reported profit growth")
    assert prediction.event_type == "profit"
    assert prediction.probability == pytest.approx(0.5999995)
    assert not prediction.ambiguous
