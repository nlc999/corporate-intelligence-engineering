"""Deterministic deferred enrichment orchestrator, fake network by default.

The original system has a persistent PostgreSQL backlog, rich quota/transport
policies and a separate promotion phase. This is a bounded, in-memory extract,
NOT a reproduction of its production scheduling or database semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
import time

from pydantic import ValidationError

from .fallback import from_deferred, from_local
from .models import EnrichmentResult, RoutingRecord
from .providers import (
    PROMPT_POLICY_VERSION, Provider, ProviderPermanentError,
    ProviderQuotaError, ProviderTransportError,
)
from .queue import QueuePlan, cache_key, plan_queue
from .schemas import GeminiClassification, parse_provider_response


@dataclass
class EnrichmentRunner:
    provider: Provider
    model: str = "gemini-2.5-flash-lite"
    classifier_version: str = "local-prediction-v1"
    prompt_policy_version: str = PROMPT_POLICY_VERSION
    max_calls: int = 20
    transport_attempts: int = 2
    quota_attempts: int = 2
    backoff_seconds: float = 0
    sleep_fn: Callable[[float], Any] = time.sleep
    history: dict[str, GeminiClassification] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_calls < 0 or self.transport_attempts < 1 or self.quota_attempts < 1:
            raise ValueError("Invalid provider budget or retry policy")

    def process(self, records: list[RoutingRecord]) -> list[EnrichmentResult]:
        plan: QueuePlan = plan_queue(records)
        results: list[tuple[int, EnrichmentResult]] = [
            (item.source_index, self._unchanged(item.record)) for item in plan.other
        ]
        calls = 0
        quota_exhausted = False
        consecutive_transport_errors = 0

        for item in plan.queued:
            record = item.record
            key = cache_key(
                record, model=getattr(self.provider, "model", self.model),
                provider=self.provider.name,
                classifier_version=self.classifier_version,
                prompt_policy_version=self.prompt_policy_version,
            )
            cached = self.history.get(key)
            if cached is not None:
                results.append((
                    item.source_index,
                    self._resolved(record, cached, source="history_cache", calls=0),
                ))
                continue
            if quota_exhausted:
                results.append((
                    item.source_index,
                    from_local(record, reason="QUOTA_DEGRADED_FALLBACK"),
                ))
                continue
            if calls >= self.max_calls:
                results.append((
                    item.source_index,
                    from_local(record, reason="RUN_BUDGET_EXHAUSTED"),
                ))
                continue

            outcome: EnrichmentResult | None = None
            quota_failures = 0
            transport_failures = 0
            while calls < self.max_calls:
                calls += 1
                try:
                    raw = self.provider.enrich(record)
                except ProviderQuotaError:
                    quota_failures += 1
                    if quota_failures >= self.quota_attempts or calls >= self.max_calls:
                        quota_exhausted = True
                        outcome = from_local(record, reason="QUOTA_DEGRADED_FALLBACK",
                                             provider_calls=quota_failures + transport_failures)
                        break
                    self._backoff(quota_failures)
                except ProviderTransportError:
                    transport_failures += 1
                    consecutive_transport_errors += 1
                    if transport_failures >= self.transport_attempts or consecutive_transport_errors >= 2:
                        outcome = from_deferred(record, reason="TRANSPORT_DEFERRED",
                                                provider_calls=quota_failures + transport_failures)
                        break
                    self._backoff(transport_failures)
                except ProviderPermanentError:
                    outcome = from_local(record, reason="PERMANENT_REQUEST_FALLBACK",
                                         provider_calls=quota_failures + transport_failures + 1)
                    break
                else:
                    # A provider bug or configuration error must NOT look like
                    # malformed model output. Catch validation only here.
                    try:
                        parsed = parse_provider_response(
                            raw, tier2_event_type=record.prediction.event_type,
                        )
                    except (ValueError, TypeError, ValidationError):
                        outcome = from_local(
                            record, reason="INVALID_PROVIDER_OUTPUT_FALLBACK",
                            provider_calls=quota_failures + transport_failures + 1,
                        )
                    else:
                        self.history[key] = parsed
                        consecutive_transport_errors = 0
                        outcome = self._resolved(
                            record, parsed, source=self.provider.name,
                            calls=quota_failures + transport_failures + 1,
                        )
                    break
            if outcome is None:
                outcome = from_local(record, reason="RUN_BUDGET_EXHAUSTED",
                                     provider_calls=quota_failures + transport_failures)
            results.append((item.source_index, outcome))

        # Provider processing is priority-sorted; output order follows the
        # original request, including equal-valued and finalised duplicates.
        return [result for _, result in sorted(results, key=lambda item: item[0])]

    def _backoff(self, attempt: int) -> None:
        if self.backoff_seconds > 0:
            self.sleep_fn(self.backoff_seconds * 2 ** (attempt - 1))

    @staticmethod
    def _resolved(record: RoutingRecord, parsed: GeminiClassification, *,
                  source: str, calls: int) -> EnrichmentResult:
        negative = parsed.is_false_positive
        status = "rejected" if negative else "gemini"
        reason = "GEMINI_FALSE_POSITIVE" if negative else (
            "CACHE_REPLAY" if source == "history_cache" else "GEMINI_VALIDATED"
        )
        routed = record.finalise(status, reason)
        return EnrichmentResult(
            record=routed, event_type=None if negative else parsed.metric_type,
            geo_scope=parsed.geo_scope, provider=source, status=status,
            is_false_positive=negative, primary_amount_raw=parsed.primary_amount_raw,
            provider_calls=calls,
        )

    @staticmethod
    def _unchanged(record: RoutingRecord) -> EnrichmentResult:
        return EnrichmentResult(
            record=record,
            event_type=record.prediction.event_type if record.prediction else None,
            geo_scope="unknown", provider="local" if record.final_status in (
                "local", "queue_cap_local"
            ) else "none", status=record.final_status,
        )
