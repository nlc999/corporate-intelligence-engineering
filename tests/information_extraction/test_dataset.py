import json

import pytest

from osint_research.information_extraction.dataset import LabelRecord, deduplicate, load_labels, sample_weight


def row(label="capex_investment", **kwargs):
    return LabelRecord("The invented firm approved a factory expansion", label, **kwargs)


def test_sample_weight_matches_training_provenance_rules():
    assert sample_weight(row(label_source="real_reviewed")) == 1.0
    assert sample_weight(row(label_source="synthetic_demo")) == 0.75
    assert sample_weight(row(label_source="synthetic_demo", rewrite_style="paraphrase")) == pytest.approx(0.675)
    assert sample_weight(row(label_source="synthetic_demo", capex_confidence="weak_capex")) == pytest.approx(0.45)


def test_duplicates_are_removed_case_and_whitespace_insensitive():
    got, audit = deduplicate([row(), LabelRecord(" the INVENTED firm   approved a factory expansion ", "capex_investment")])
    assert len(got) == 1
    assert audit["duplicates_removed"] == 1


def test_label_conflicts_fail_loudly():
    with pytest.raises(ValueError, match="Conflicting labels"):
        deduplicate([row(), row("divestment")])


def test_invalid_class_and_empty_are_rejected():
    with pytest.raises(ValueError, match="Unknown event"):
        deduplicate([row("geographical_scope")])
    with pytest.raises(ValueError, match="Empty training"):
        deduplicate([LabelRecord(" ", "profit")])


def test_jsonl_load_and_validation(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({"sentence": "Company agreed to acquire a unit", "event_type": "acquisition"})+"\n")
    recs, audit = load_labels(path)
    assert len(recs) == 1 and audit["unique"] == 1
    path.write_text('{"sentence": "X"}\n')
    with pytest.raises(ValueError, match="labels.jsonl:1"):
        load_labels(path)


def test_synthetic_fixture_covers_nine_classes():
    from pathlib import Path
    source = Path(__file__).resolve().parents[2] / "examples/synthetic_event_labels.jsonl"
    records, audit = load_labels(source)
    from osint_research.information_extraction.labels import EVENT_LABELS
    assert set(r.event_type for r in records) == set(EVENT_LABELS)
    assert audit["loaded"] == audit["unique"]
