"""Typed boundaries for a small, synthetic, three-tier financial NLP showcase."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

Status = Literal["local", "local_pending", "rejected", "degraded_pending", "queue_cap_local", "deduplicated", "gemini", "fallback_local", "deferred", "failed"]


@dataclass(frozen=True)
class Candidate:
    source_id: str
    sentence: str
    company_name: str
    company_number: str
    amount_gbp: float
    financial_cue: bool
    attribution: Literal["subject", "ambiguous", "other_company"] = "subject"
    named_person: bool = False
    supporting_context: str = ""

    def __post_init__(self) -> None:
        if not self.source_id or not self.sentence.strip():
            raise ValueError("Source ID and nonempty sentence required")
        if self.amount_gbp < 0:
            raise ValueError("GBP amount cannot be negative")


@dataclass(frozen=True)
class Prediction:
    event_type: str
    confidence: float

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("Confidence must be in [0, 1]")


@dataclass(frozen=True)
class RoutingRecord:
    candidate: Candidate
    prediction: Prediction | None
    quality: Literal["STRONG", "WEAK", "NOISE"]
    threshold: float | None
    initial_status: Status
    final_status: Status
    reason_code: str
    reason_detail: str = ""

    def finalise(self, status: Status, reason: str, detail: str = "") -> "RoutingRecord":
        return replace(self, final_status=status, reason_code=reason, reason_detail=detail)


@dataclass(frozen=True)
class EnrichmentResult:
    record: RoutingRecord
    event_type: str | None
    geo_scope: str
    provider: str
    status: Status
    provider_calls: int = 0
    is_false_positive: bool = False
    primary_amount_raw: str | None = None
    fallback_reason: str | None = None

    def as_dict(self) -> dict:
        r = self.record
        return {
            "source_id": r.candidate.source_id,
            "company_number": r.candidate.company_number,
            "sentence": r.candidate.sentence,
            "quality": r.quality,
            "event_type": self.event_type,
            "confidence": r.prediction.confidence if r.prediction else None,
            "threshold": r.threshold,
            "initial_status": r.initial_status,
            "final_status": self.status,
            "reason_code": r.reason_code,
            "reason_detail": r.reason_detail,
            "provider": self.provider,
            "provider_calls": self.provider_calls,
            "geo_scope": self.geo_scope,
            "is_false_positive": self.is_false_positive,
            "primary_amount_raw": self.primary_amount_raw,
            "fallback_reason": self.fallback_reason,
        }
