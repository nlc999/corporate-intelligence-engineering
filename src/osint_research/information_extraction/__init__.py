"""Standalone financial event classification engineering showcase."""
from .classifier import EventClassifier, Prediction
from .dataset import LabelRecord, deduplicate, load_labels, sample_weight
from .labels import EVENT_LABELS

__all__ = ["EventClassifier", "Prediction", "LabelRecord", "deduplicate", "load_labels", "sample_weight", "EVENT_LABELS"]
