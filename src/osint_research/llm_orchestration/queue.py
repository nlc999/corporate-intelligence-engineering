"""In-memory deterministic deferred-queue adaptation.

Production uses persisted PostgreSQL tasks. This isolated case study does not
claim restart durability or exactly-once delivery.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from .models import RoutingRecord

MAX_PER_SOURCE = 5


def priority(event_type: str) -> int:
    if event_type in {"capex_investment", "capex_cancellation"}:
        return 0
    if event_type == "acquisition":
        return 1
    return 2


def cache_key(
    item: RoutingRecord, *, model: str, provider: str,
    classifier_version: str, prompt_policy_version: str,
) -> str:
    """Hash the complete semantic context of a validated provider response.

    A model's explicit metric_type may be absent; parsing then defaults to the
    local prediction. Reusing an older result after a prediction or policy
    change would therefore be unsafe. Do not include confidence: once a record
    is queued it does not affect the provider payload or response parsing.
    """
    if item.prediction is None:
        raise ValueError("Cache identity requires a local prediction")
    if not all((model.strip(), provider.strip(),
                classifier_version.strip(), prompt_policy_version.strip())):
        raise ValueError("Cache identity versions and provider/model must be nonempty")
    c = item.candidate
    # JSON array encoding avoids delimiter ambiguities in arbitrary source text.
    identity = [
        "enrichment-v2", provider, model, classifier_version,
        prompt_policy_version, item.prediction.event_type, c.source_id,
        c.company_number, c.company_name, c.sentence, c.supporting_context,
    ]
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IndexedRoutingRecord:
    """Request-local position, separate from the original routing evidence."""
    source_index: int
    record: RoutingRecord


@dataclass(frozen=True)
class QueuePlan:
    queued: tuple[IndexedRoutingRecord, ...]
    other: tuple[IndexedRoutingRecord, ...]


def plan_queue(records: list[RoutingRecord], *, per_source_cap: int = MAX_PER_SOURCE) -> QueuePlan:
    """Prioritise enrichment while tracking each original input occurrence."""
    if per_source_cap < 0:
        raise ValueError("Queue cap cannot be negative")
    indexed = [IndexedRoutingRecord(i, record) for i, record in enumerate(records)]
    candidates: dict[str, list[IndexedRoutingRecord]] = {}
    other = [item for item in indexed if item.record.final_status != "local_pending"]
    for item in indexed:
        if item.record.final_status == "local_pending":
            candidates.setdefault(item.record.candidate.source_id, []).append(item)

    kept: list[IndexedRoutingRecord] = []
    for source_id in sorted(candidates):
        # Deduplicate *before* taking the per-source quota. A repeated sentence
        # must not consume slots that could enrich distinct company events.
        # The identity mirrors the request/parse-dependent parts of cache_key;
        # changing only the confidence preserves duplicate semantics.
        by_event: dict[tuple[str, str, str, str, str], IndexedRoutingRecord] = {}
        for item in candidates[source_id]:
            record = item.record
            candidate = record.candidate
            key = (
                candidate.company_number,
                candidate.company_name,
                candidate.sentence,
                candidate.supporting_context,
                record.prediction.event_type,
            )
            incumbent = by_event.get(key)
            if incumbent is None:
                by_event[key] = item
                continue

            # Keep the highest-confidence record, breaking ties by input order.
            # Every suppressed input still receives its own deduplication result.
            if record.prediction.confidence > incumbent.record.prediction.confidence:
                by_event[key] = item
                dropped = incumbent
            else:
                dropped = item
            other.append(IndexedRoutingRecord(
                dropped.source_index,
                dropped.record.finalise("deduplicated", "DUPLICATE_QUEUE_ITEM"),
            ))

        # The source cap now applies to unique events, not repeated records.
        # Prefer the most ambiguous remaining events for provider escalation.
        group = sorted(by_event.values(), key=lambda item: (
            item.record.prediction.confidence,
            item.record.candidate.sentence,
            item.record.candidate.company_number,
            item.source_index,
        ))
        kept.extend(group[:per_source_cap])
        other.extend(
            IndexedRoutingRecord(
                item.source_index,
                item.record.finalise(
                    "queue_cap_local", "QUEUE_CAP_PROMOTED",
                    "Accepted local prediction without Gemini",
                ),
            )
            for item in group[per_source_cap:]
        )

    queued = tuple(sorted(kept, key=lambda item: (
        priority(item.record.prediction.event_type),
        item.record.candidate.source_id,
        item.record.candidate.sentence,
        item.source_index,
    )))
    return QueuePlan(queued, tuple(other))
