#!/usr/bin/env python3
"""Predict an event class with a locally trained, trusted model artifact."""
from __future__ import annotations

import argparse
import json

from osint_research.information_extraction.classifier import from_artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sentence", help="One financial statement")
    parser.add_argument("--model", default="artifacts/event_classifier/event_head.pkl")
    parser.add_argument("--threshold", type=float, default=0.52)
    args = parser.parse_args()
    prediction = from_artifact(args.model, threshold=args.threshold).predict(args.sentence)
    print(json.dumps({"event_type": prediction.event_type,
                      "predicted_probability": round(prediction.probability, 4),
                      "ambiguous": prediction.ambiguous}, indent=2))


if __name__ == "__main__":
    main()
