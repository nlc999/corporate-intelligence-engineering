"""Auditable JSONL labelling, conflict detection and provenance weighting.

Adapted from the original training data validation and compute_sample_weight.
Labels may include a synthetic source ID; user-facing fixtures are invented.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .labels import EVENT_LABELS


@dataclass(frozen=True)
class LabelRecord:
    sentence: str
    event_type: str
    label_source: str = "synthetic_demo"
    source_id: str | None = None
    rewrite_style: str | None = None
    capex_confidence: str | None = None


def normalise_sentence(sentence: str) -> str:
    return re.sub(r"\s+", " ", sentence.strip().lower())


def sample_weight(record: LabelRecord) -> float:
    """Provenance-based multipliers preserved from the source training pipeline."""
    weight = 1.0
    if "synthetic" in record.label_source.lower():
        weight *= 0.75
    if record.rewrite_style:
        weight *= 0.9
    if record.event_type == "capex_investment" and record.capex_confidence == "weak_capex":
        weight *= 0.6
    return weight


def deduplicate(records: Iterable[LabelRecord]) -> tuple[list[LabelRecord], dict[str, int]]:
    seen: dict[str, LabelRecord] = {}
    removed = 0
    for record in records:
        key = normalise_sentence(record.sentence)
        if not key:
            raise ValueError("Empty training sentence")
        if record.event_type not in EVENT_LABELS:
            raise ValueError(f"Unknown event label: {record.event_type}")
        prior = seen.get(key)
        if prior is not None:
            if prior.event_type != record.event_type:
                raise ValueError(f"Conflicting labels for sentence: {key[:90]}")
            removed += 1
            continue
        seen[key] = record
    return list(seen.values()), {"loaded": len(seen) + removed, "unique": len(seen), "duplicates_removed": removed}


def load_labels(path: str | Path) -> tuple[list[LabelRecord], dict[str, int]]:
    source = Path(path)
    rows: list[LabelRecord] = []
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
                sentence = data["sentence"]
                label = data["event_type"]
                if not isinstance(sentence, str) or not isinstance(label, str):
                    raise ValueError("sentence/event_type must be strings")
                record = LabelRecord(
                    sentence=sentence,
                    event_type=label,
                    label_source=str(data.get("label_source", "synthetic_demo")),
                    source_id=data.get("source_id"),
                    rewrite_style=data.get("rewrite_style"),
                    capex_confidence=data.get("capex_confidence"),
                )
            except (KeyError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid JSONL at {source}:{line_number}: {exc}") from exc
            rows.append(record)
    if not rows:
        raise ValueError("Training data must not be empty")
    return deduplicate(rows)
