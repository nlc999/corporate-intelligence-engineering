"""Reproducible linear-head training, adapted from the local NLP trainer.

The encoder is pretrained; only the scikit-learn logistic regression is fitted.
"""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from .dataset import LabelRecord, sample_weight
from .evaluation import evaluate_predictions
from .labels import validate_event_labels


def fit_classifier(embeddings, records: Sequence[LabelRecord], *, test_size: float = 0.2,
                   random_state: int = 42, grouped: bool = False):
    """Return (fitted head, evaluation report, split provenance).

    Default mirrors source stratified split. Optional group split prevents
    same-source leakage; it explicitly rejects train sets missing any class.
    """
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import GroupShuffleSplit, train_test_split
    except ImportError as exc:
        raise RuntimeError("Training needs the optional ML dependencies") from exc
    if len(embeddings) != len(records):
        raise ValueError("Embedding and record counts differ")
    if not 0 < test_size < 1:
        raise ValueError("test_size must be between 0 and 1")
    labels = [r.event_type for r in records]
    validate_event_labels(set(labels))
    if min(Counter(labels).values()) < 2:
        raise ValueError("Every included class must have at least two samples")
    if grouped:
        groups = [r.source_id or r.sentence.casefold().strip() for r in records]
        splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=random_state)
        train_ids, test_ids = next(splitter.split(embeddings, labels, groups))
        if set(train_ids) & set(test_ids):
            raise AssertionError("Grouped split overlap")
        if set(groups[int(i)] for i in train_ids) & set(groups[int(i)] for i in test_ids):
            raise AssertionError("Source group leaked across holdout")
    else:
        indexes = list(range(len(records)))
        train_ids, test_ids = train_test_split(indexes, test_size=test_size,
                                                random_state=random_state, stratify=labels)
    train_ids, test_ids = list(map(int, train_ids)), list(map(int, test_ids))
    train_labels = [labels[i] for i in train_ids]
    if set(train_labels) != set(labels):
        raise ValueError("Training split lacks some event classes; adjust data or split")
    X_train = [embeddings[i] for i in train_ids]
    X_test = [embeddings[i] for i in test_ids]
    head = LogisticRegression(C=1.0, max_iter=1000, random_state=random_state,
                              class_weight=None)
    head.fit(X_train, train_labels, sample_weight=[sample_weight(records[i]) for i in train_ids])
    predictions = list(head.predict(X_test))
    report = evaluate_predictions([labels[i] for i in test_ids], predictions)
    split = {"random_state": random_state, "test_size": test_size,
             "split_mode": "grouped_by_source_id" if grouped else "stratified",
             "n_train": len(train_ids), "n_test": len(test_ids),
             "label_counts": dict(Counter(labels))}
    return head, report, split
