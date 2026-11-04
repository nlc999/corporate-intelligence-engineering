"""Pluggable provider transport. Offline mock is the default; live is opt-in."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import RoutingRecord

# Bump when the financial-event prompt or response interpretation changes.
# EnrichmentRunner uses this to invalidate cached classifications.
PROMPT_POLICY_VERSION = "financial-event-extraction-v1"


class ProviderQuotaError(RuntimeError):
    pass


class ProviderTransportError(RuntimeError):
    pass


class ProviderPermanentError(RuntimeError):
    pass


class ProviderConfigurationError(RuntimeError):
    """Missing credentials/dependencies or rejected client authentication."""


class Provider(Protocol):
    name: str

    def enrich(self, record: RoutingRecord) -> dict[str, Any] | str: ...


@dataclass
class MockGeminiProvider:
    """Fixture-driven deterministic provider, never contacts any network."""
    responses: dict[str, dict[str, Any] | str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    name: str = "mock_gemini"
    calls: int = 0

    def enrich(self, record: RoutingRecord) -> dict[str, Any] | str:
        self.calls += 1
        key = record.candidate.source_id
        failure = self.failures.get(key)
        if failure == "quota":
            raise ProviderQuotaError("429 RESOURCE_EXHAUSTED (synthetic)")
        if failure == "transport":
            raise ProviderTransportError("connection timeout (synthetic)")
        if failure == "permanent":
            raise ProviderPermanentError("invalid provider request (synthetic)")
        if failure == "malformed":
            return "{bad json"
        if key in self.responses:
            return self.responses[key]
        return {
            "metric_type": record.prediction.event_type,
            "geo_scope": "unknown",
            "is_false_positive": False,
            "reasoning": "Synthetic mock response only; no LLM inference occurred.",
        }


@dataclass
class GeminiProvider:
    """Optional real Gemini transport; never constructed implicitly."""
    name: str = "google_gemini"
    model: str = "gemini-2.5-flash-lite"

    def enrich(self, record: RoutingRecord) -> str:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise ProviderConfigurationError(
                "GEMINI_API_KEY must be provided explicitly for live mode"
            )
        try:
            import httpx
            from google import genai
            from google.genai import errors as genai_errors
        except ImportError as exc:
            raise ProviderConfigurationError(
                "Install the optional '[gemini]' extra"
            ) from exc

        candidate = record.candidate
        prompt = (
            "Subject company: " + candidate.company_name + " (" + candidate.company_number + ")\n"
            "Sentence to classify: " + candidate.sentence + "\n"
            "Supporting context: " + candidate.supporting_context[:400] + "\n"
            "Return the main event type, geography, whether this is a false positive, "
            "and the amount primarily associated with the event when ambiguous."
        )
        system = (
            "Classify corporate financial statements. Return a single JSON object with "
            "metric_type, geo_scope, change_direction, is_false_positive, reasoning, "
            "primary_amount_raw. Use null for unknown optional fields. "
            "Do not treat other companies' transactions as the subject company's events."
        )
        # Initialisation and request construction are not network-response
        # errors; surface invalid configuration and programmer errors directly.
        try:
            client = genai.Client(api_key=key)
        except ValueError as exc:
            raise ProviderConfigurationError("Invalid Gemini client configuration") from exc
        config = genai.types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.0,
        )

        try:
            response = client.models.generate_content(
                model=self.model, contents=prompt, config=config,
            )
        except genai_errors.APIError as exc:
            # Use the SDK's structured HTTP code, never substring matches on
            # untrusted error messages. Preserve the original error as cause.
            if exc.code == 429:
                raise ProviderQuotaError("Gemini request quota exceeded") from exc
            if exc.code in (408, 409, 500, 502, 503, 504):
                raise ProviderTransportError("Gemini service temporarily unavailable") from exc
            if exc.code in (401, 403):
                raise ProviderConfigurationError("Gemini API authentication failed") from exc
            raise ProviderPermanentError("Gemini API rejected the request") from exc
        except (httpx.TransportError, ConnectionError, TimeoutError) as exc:
            raise ProviderTransportError("Gemini network transport unavailable") from exc

        if not response.text:
            raise ProviderPermanentError("Empty provider response")
        return str(response.text)
