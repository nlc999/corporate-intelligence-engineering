"""Gemini JSON normalisation plus strict showcase-specific semantic validation.

Strict taxonomies improve upon the original broader Pydantic shape contract.
They must not be described as unmodified production validation.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from osint_research.information_extraction.labels import EVENT_LABELS

EVENT_LABELS_WITH_EXTRA = frozenset(EVENT_LABELS) | frozenset({
    "government_support", "regulatory_action", "restructuring",
    "revenue", "operating_profit", "pre_tax_profit", "net_profit",
    "ebitda", "contract_award_value", "dividend", "other",
})
GEO_LABELS = frozenset({"unknown", "GB", "US", "CN", "EU", "global", "other"})
ALIASES = {
    "investment": "capex_investment", "capex": "capex_investment",
    "buyback": "share_buyback", "share_repurchase": "share_buyback",
    "share_repurchase_program": "share_buyback",
    "none": "other", "product": "other",
}


class GeminiClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    metric_type: str
    geo_scope: str = "unknown"
    change_direction: str | None = None
    is_false_positive: bool
    reasoning: str = Field(default="", max_length=2000)
    primary_amount_raw: str | None = None

    @field_validator("metric_type")
    @classmethod
    def check_metric(cls, value: str) -> str:
        if value not in EVENT_LABELS_WITH_EXTRA:
            raise ValueError("Unknown financial-event category")
        return value

    @field_validator("geo_scope")
    @classmethod
    def check_geo(cls, value: str) -> str:
        if value not in GEO_LABELS:
            raise ValueError("Unsupported geography value")
        return value


def parse_provider_response(raw: str | dict[str, Any], *, tier2_event_type: str) -> GeminiClassification:
    payload = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError("Expected exactly one response object")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object")
    item = dict(payload)
    nested = item.pop("classification", None)
    if "metric_type" not in item:
        if isinstance(nested, dict):
            item["metric_type"] = nested.get("metric_type") or nested.get("mention_type")
            if "is_false_positive" in nested and "is_false_positive" not in item:
                item["is_false_positive"] = nested["is_false_positive"]
        elif isinstance(nested, str):
            item["metric_type"] = nested
        else:
            item["metric_type"] = item.pop("financial_mention_type", tier2_event_type)
    item.pop("financial_mention_type", None)
    key = str(item.get("metric_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    item["metric_type"] = ALIASES.get(key, key)
    return GeminiClassification.model_validate(item)
