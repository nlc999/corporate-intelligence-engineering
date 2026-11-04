"""Explicit outcome marking: a locally recovered item is never labelled Gemini."""
from __future__ import annotations

from .models import EnrichmentResult, RoutingRecord


def from_local(record: RoutingRecord, *, reason: str, provider_calls: int = 0) -> EnrichmentResult:
    routed = record.finalise("fallback_local", reason)
    return EnrichmentResult(
        record=routed,
        event_type=routed.prediction.event_type if routed.prediction else None,
        geo_scope="unknown",
        provider="local_fallback",
        status="fallback_local",
        provider_calls=provider_calls,
        fallback_reason=reason,
    )


def from_deferred(record: RoutingRecord, *, reason: str, provider_calls: int = 0) -> EnrichmentResult:
    routed = record.finalise("deferred", reason)
    return EnrichmentResult(
        record=routed,
        event_type=routed.prediction.event_type if routed.prediction else None,
        geo_scope="unknown",
        provider="none",
        status="deferred",
        provider_calls=provider_calls,
        fallback_reason=reason,
    )
