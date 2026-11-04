import pytest

from osint_research.information_extraction.dataset import LabelRecord
from osint_research.information_extraction.labels import EVENT_LABELS


def test_metrics_are_per_class_and_confusion_matrix():
    pytest.importorskip("sklearn")
    from osint_research.information_extraction.evaluation import evaluate_predictions
    report = evaluate_predictions(["profit", "acquisition", "profit"], ["profit", "profit", "profit"])
    assert report["accuracy"] == pytest.approx(2/3)
    assert report["n_test"] == 3
    assert len(report["confusion_matrix"]) == len(EVENT_LABELS)
    assert report["per_class"]["acquisition"]["recall"] == 0


def test_reproducible_stratified_split_and_weighted_fit():
    pytest.importorskip("sklearn")
    from osint_research.information_extraction.training import fit_classifier
    import numpy as np
    records = [LabelRecord(f"Example {label} {i}", label, source_id=f"{label}-{i}")
               for label in EVENT_LABELS for i in range(10)]
    embedding = np.array([[float(i % 10), float(i // 10), float(i % 3)] for i in range(len(records))])
    _, metrics_a, split_a = fit_classifier(embedding, records)
    _, metrics_b, split_b = fit_classifier(embedding, records)
    assert metrics_a == metrics_b
    assert split_a == split_b
    assert split_a["n_train"] == 72 and split_a["n_test"] == 18
    _, _, group_split = fit_classifier(embedding, records, grouped=True)
    assert group_split["split_mode"] == "grouped_by_source_id"


def test_no_silent_missing_classes_in_grouped_split():
    pytest.importorskip("sklearn")
    from osint_research.information_extraction.training import fit_classifier
    import numpy as np
    records = [LabelRecord(f"Example {i}", EVENT_LABELS[i // 3], source_id=f"source-{i//3}")
               for i in range(27)]
    vectors = np.eye(27)
    with pytest.raises(ValueError, match="lacks some event classes"):
        fit_classifier(vectors, records, grouped=True, test_size=0.34)
