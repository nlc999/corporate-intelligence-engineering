#!/usr/bin/env python3
"""Train MiniLM + sklearn logistic regression on synthetic demonstration labels."""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

from osint_research.information_extraction.dataset import load_labels
from osint_research.information_extraction.embeddings import MODEL_NAME, encode_sentences, load_encoder
from osint_research.information_extraction.training import fit_classifier


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the financial event classifier")
    parser.add_argument("--data", default="examples/synthetic_event_labels.jsonl")
    parser.add_argument("--output-dir", default="artifacts/event_classifier")
    parser.add_argument("--grouped", action="store_true", help="Split by source_id instead of stratification")
    args = parser.parse_args()

    rows, audit = load_labels(args.data)
    encoder = load_encoder()
    embeddings = encode_sentences(encoder, [row.sentence for row in rows])
    head, report, split = fit_classifier(embeddings, rows, grouped=args.grouped)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "event_head.pkl").open("wb") as handle:
        pickle.dump(head, handle)
    summary = {"note": "Synthetic dataset results, not historical/production scores.",
               "encoder": MODEL_NAME, "data_audit": audit, "split": split, "event_metrics": report}
    (output / "evaluation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Trained {split['n_train']} rows; evaluated {split['n_test']} held-out rows.")
    print(f"Accuracy: {report['accuracy']:.3f}; macro-F1: {report['macro_f1']:.3f}")
    print(f"Artifacts saved in {output} (trust-only pickle — do not commit)")


if __name__ == "__main__":
    main()
