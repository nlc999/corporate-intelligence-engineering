#!/usr/bin/env python3
"""Evaluate a saved trusted model on independently written synthetic examples."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from osint_research.information_extraction.classifier import from_artifact
from osint_research.information_extraction.dataset import load_labels
from osint_research.information_extraction.evaluation import evaluate_predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="artifacts/event_classifier/event_head.pkl")
    parser.add_argument("--data", default="examples/unseen_event_examples.jsonl")
    args = parser.parse_args()
    records, _ = load_labels(args.data)
    classifier = from_artifact(args.model)
    predicted = [classifier.predict(row.sentence).event_type for row in records]
    report = evaluate_predictions([row.event_type for row in records], predicted)
    print(json.dumps(report, indent=2))
    print("Evaluation uses invented test statements, not the historical private corpus.")


if __name__ == "__main__":
    main()
