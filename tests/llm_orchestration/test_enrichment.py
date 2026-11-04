"""End-to-end isolated routing and provider failure tests, with zero network I/O."""
from dataclasses import replace

import pytest

from osint_research.llm_orchestration import (
    Candidate, EnrichmentRunner, MockGeminiProvider, Prediction, route, plan_queue,
)
from osint_research.llm_orchestration.providers import (
    GeminiProvider, ProviderConfigurationError, ProviderPermanentError,
    ProviderQuotaError, ProviderTransportError,
)


def candidate(source="a", sentence="The group reported approximately £2 million of profit last year."):
    return Candidate(source_id=source, sentence=sentence, company_name="Alder",
                     company_number="00000001", amount_gbp=2_000_000, financial_cue=True)


def pending(source="a", score=.2):
    return route(candidate(source), Prediction("profit", score))


def test_mock_success_and_result_trace():
    p = MockGeminiProvider(responses={"a":{"metric_type":"profit",
                           "geo_scope":"GB","is_false_positive":False}})
    result = EnrichmentRunner(p).process([pending()])[0]
    assert result.status == "gemini"
    assert result.provider == "mock_gemini"
    assert result.provider_calls == 1
    assert result.as_dict()["initial_status"] == "local_pending"
    assert result.as_dict()["reason_code"] == "GEMINI_VALIDATED"


def test_cached_result_no_second_provider_call():
    p = MockGeminiProvider()
    engine = EnrichmentRunner(p)
    first = engine.process([pending()])[0]
    second = engine.process([pending()])[0]
    assert first.status == second.status == "gemini"
    assert second.provider == "history_cache"
    assert second.provider_calls == 0
    assert p.calls == 1


def test_reclassification_and_policy_changes_force_new_provider_calls():
    # The LLM reply deliberately omits metric_type. Its interpretation must
    # follow the current local prediction, never a stale cached prediction.
    provider = MockGeminiProvider(responses={
        "a": {"geo_scope": "GB", "is_false_positive": False},
    })
    runner = EnrichmentRunner(
        provider, classifier_version="classifier-v1",
        prompt_policy_version="prompt-v1",
    )
    original = pending("a", score=.2)
    reclassified = route(original.candidate, Prediction("acquisition", .2))
    assert original.final_status == reclassified.final_status == "local_pending"

    first = runner.process([original])[0]
    assert (first.event_type, first.provider, provider.calls) == (
        "profit", "mock_gemini", 1,
    )

    second = runner.process([reclassified])[0]
    assert (second.event_type, second.provider, provider.calls) == (
        "acquisition", "mock_gemini", 2,
    )
    cached = runner.process([reclassified])[0]
    assert (cached.event_type, cached.provider, cached.provider_calls) == (
        "acquisition", "history_cache", 0,
    )
    assert provider.calls == 2

    runner.classifier_version = "classifier-v2"
    assert runner.process([reclassified])[0].provider == "mock_gemini"
    runner.prompt_policy_version = "prompt-v2"
    assert runner.process([reclassified])[0].provider == "mock_gemini"
    assert provider.calls == 4


def test_provider_model_and_source_changes_invalidate_history():
    provider = MockGeminiProvider()
    provider.model = "mock-model-v1"
    runner = EnrichmentRunner(provider)
    assert runner.process([pending("a")])[0].provider == "mock_gemini"
    assert runner.process([pending("a")])[0].provider == "history_cache"
    provider.model = "mock-model-v2"
    assert runner.process([pending("a")])[0].provider == "mock_gemini"
    assert runner.process([pending("b")])[0].provider == "mock_gemini"
    assert provider.calls == 3


def test_repeated_quota_falls_back_without_faking_gemini():
    p = MockGeminiProvider(failures={"a":"quota"})
    result = EnrichmentRunner(p).process([pending()])[0]
    assert result.status == "fallback_local"
    assert result.provider == "local_fallback"
    assert result.fallback_reason == "QUOTA_DEGRADED_FALLBACK"
    assert result.provider_calls == 2


def test_degraded_quota_closes_rest_via_fallback():
    p = MockGeminiProvider(failures={"a":"quota"})
    outputs = EnrichmentRunner(p).process([pending("a"), pending("b")])
    assert [o.status for o in outputs] == ["fallback_local", "fallback_local"]
    assert p.calls == 2


def test_invalid_json_is_explicit_fallback():
    p = MockGeminiProvider(failures={"a":"malformed"})
    result = EnrichmentRunner(p).process([pending()])[0]
    assert result.status == "fallback_local"
    assert result.fallback_reason == "INVALID_PROVIDER_OUTPUT_FALLBACK"


def test_permanent_request_error_is_explicit():
    p = MockGeminiProvider(failures={"a":"permanent"})
    result = EnrichmentRunner(p).process([pending()])[0]
    assert result.fallback_reason == "PERMANENT_REQUEST_FALLBACK"


def test_transport_retry_and_defer():
    p = MockGeminiProvider(failures={"a":"transport"})
    result = EnrichmentRunner(p).process([pending()])[0]
    assert p.calls == 2
    assert result.provider == "none"
    assert result.status == "deferred"
    assert result.fallback_reason == "TRANSPORT_DEFERRED"


def test_zero_budget_keeps_provider_unused():
    p = MockGeminiProvider()
    result = EnrichmentRunner(p, max_calls=0).process([pending()])[0]
    assert p.calls == 0
    assert result.fallback_reason == "RUN_BUDGET_EXHAUSTED"


def test_false_positive_not_promoted():
    p = MockGeminiProvider(responses={"a":{
        "metric_type":"other","geo_scope":"unknown","is_false_positive":True
    }})
    result = EnrichmentRunner(p).process([pending()])[0]
    assert result.status == "rejected" and result.event_type is None
    assert result.is_false_positive is True


def test_unqueued_candidates_never_call_provider():
    p = MockGeminiProvider()
    records = [
        route(candidate("a"), Prediction("profit", .99)),
        route(candidate("b"), Prediction("not_financial_event", .9)),
        route(candidate("c"), None, classifier_error="NoModel"),
    ]
    out = EnrichmentRunner(p).process(records)
    assert [r.status for r in out] == ["local", "rejected", "degraded_pending"]
    assert p.calls == 0


def test_attribution_and_quality_rejection_without_calls():
    p = MockGeminiProvider()
    out = EnrichmentRunner(p).process([
        route(replace(candidate(), attribution="other_company"), Prediction("profit", .15)),
        route(replace(candidate("z"), amount_gbp=1000), Prediction("profit", .1)),
    ])
    assert all(r.status == "rejected" for r in out)
    assert p.calls == 0


def test_offline_end_to_end_queue_with_mixed_outcomes():
    p = MockGeminiProvider(failures={"article-a":"permanent"})
    inputs = [pending("article-a"), route(candidate("article-b"), Prediction("profit", .9))]
    output = EnrichmentRunner(p).process(inputs)
    assert [item.status for item in output] == ["fallback_local", "local"]
    assert all(item.as_dict()["company_number"] == "00000001" for item in output)


def test_source_order_preserves_identical_records_and_priority_processing():
    repeated = pending("duplicate")
    inputs = [
        repeated,
        route(candidate("local"), Prediction("profit", .99)),
        route(candidate("urgent"), Prediction("capex_investment", .20)),
        repeated,
    ]
    provider = MockGeminiProvider()
    results = EnrichmentRunner(provider).process(inputs)
    assert len(results) == len(inputs)
    assert [item.record.candidate.source_id for item in results] == [
        "duplicate", "local", "urgent", "duplicate",
    ]
    assert [item.status for item in results] == [
        "gemini", "local", "gemini", "deduplicated",
    ]
    assert provider.calls == 2
    assert results[0].record.candidate == results[3].record.candidate


def test_enrichment_preserves_distinct_companies_with_identical_source_sentences():
    source = pending("shared")
    second = route(
        replace(source.candidate, company_name="Birch", company_number="00000002"),
        Prediction("profit", .2),
    )
    provider = MockGeminiProvider()
    results = EnrichmentRunner(provider).process([source, second])

    assert [item.record.candidate.company_number for item in results] == [
        "00000001", "00000002",
    ]
    assert [item.status for item in results] == ["gemini", "gemini"]
    assert provider.calls == 2


def test_enrichment_source_cap_counts_unique_events_before_processing():
    same_event = pending("common")
    repeated = [same_event] * 6
    unique = [
        route(
            candidate("common", f"Alder reported £2 million of profit for division {i}."),
            Prediction("profit", .2),
        )
        for i in range(1, 5)
    ]
    provider = MockGeminiProvider()
    results = EnrichmentRunner(provider).process([*repeated, *unique])

    assert len(results) == 10
    assert [result.status for result in results].count("gemini") == 5
    assert [result.status for result in results].count("deduplicated") == 5
    assert all(result.status == "gemini" for result in results[6:])
    assert provider.calls == 5


def test_source_order_preserved_when_later_higher_confidence_duplicate_wins():
    earlier = pending("repeat", score=.20)
    preferred = pending("repeat", score=.30)
    records = [earlier, route(candidate("local"), Prediction("profit", .99)), preferred]
    results = EnrichmentRunner(MockGeminiProvider()).process(records)
    assert [item.status for item in results] == ["deduplicated", "local", "gemini"]
    assert [item.record.prediction.confidence for item in results] == [.20, .99, .30]


def test_source_order_preserved_for_empty_input_and_queue_overflow():
    assert EnrichmentRunner(MockGeminiProvider()).process([]) == []
    inputs = [
        route(candidate("same", f"Alder reported £2 million of profit for division {i}."),
              Prediction("profit", .1 + i * .01))
        for i in range(7)
    ]
    outputs = EnrichmentRunner(MockGeminiProvider()).process(inputs)
    assert len(outputs) == len(inputs)
    assert [item.record.candidate.sentence for item in outputs] == [
        item.candidate.sentence for item in inputs
    ]
    assert [item.status for item in outputs].count("queue_cap_local") == 2


@pytest.mark.parametrize("failure", [
    ValueError("configuration missing"),
    TypeError("unexpected provider signature"),
    RuntimeError("implementation defect"),
    ProviderConfigurationError("authentication rejected"),
])
def test_provider_errors_are_not_disguised_as_invalid_json(failure):
    class BrokenProvider:
        name = "broken"
        def enrich(self, record):
            raise failure

    with pytest.raises(type(failure), match=str(failure)):
        EnrichmentRunner(BrokenProvider()).process([pending()])


@pytest.mark.parametrize("raw", [
    "{not valid json",
    {"metric_type": "profit", "geo_scope": "Mars", "is_false_positive": False},
    {"metric_type": "profit", "is_false_positive": "false"},
])
def test_response_validation_alone_triggers_invalid_output_fallback(raw):
    provider = MockGeminiProvider(responses={"a": raw})
    result = EnrichmentRunner(provider).process([pending()])[0]
    assert result.status == "fallback_local"
    assert result.fallback_reason == "INVALID_PROVIDER_OUTPUT_FALLBACK"
    assert provider.calls == 1


def test_gemini_missing_key_reports_configuration_not_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
        EnrichmentRunner(GeminiProvider()).process([pending()])


@pytest.mark.parametrize("status_code, expected", [
    (429, ProviderQuotaError),
    (503, ProviderTransportError),
    (401, ProviderConfigurationError),
    (400, ProviderPermanentError),
])
def test_gemini_sdk_api_status_is_classified_without_message_inspection(
    monkeypatch, status_code, expected,
):
    """Fake the optional SDK: no installed Google client or network is needed."""
    import sys
    from types import ModuleType, SimpleNamespace

    class FakeAPIError(Exception):
        def __init__(self, code):
            self.code = code
            super().__init__("contradictory message says timeout and 429")

    class FakeTransportError(Exception):
        pass

    google = ModuleType("google")
    google.__path__ = []
    genai = ModuleType("google.genai")
    genai.__path__ = []
    errors = ModuleType("google.genai.errors")
    errors.APIError = FakeAPIError
    genai.errors = errors
    genai.types = SimpleNamespace(GenerateContentConfig=lambda **kw: kw)

    class Client:
        def __init__(self, api_key):
            assert api_key == "synthetic-test-key"
            self.models = SimpleNamespace(generate_content=self.generate_content)

        @staticmethod
        def generate_content(**kwargs):
            raise FakeAPIError(status_code)

    genai.Client = Client
    google.genai = genai
    httpx = ModuleType("httpx")
    httpx.TransportError = FakeTransportError
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.errors", errors)
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")

    with pytest.raises(expected) as caught:
        GeminiProvider().enrich(pending())
    assert isinstance(caught.value.__cause__, FakeAPIError)
    assert str(status_code) not in str(caught.value)


def test_gemini_unexpected_sdk_exception_is_not_permanent_fallback(monkeypatch):
    """A programming error from the SDK call must retain its original type."""
    import sys
    from types import ModuleType, SimpleNamespace

    google = ModuleType("google")
    google.__path__ = []
    genai = ModuleType("google.genai")
    genai.__path__ = []
    errors = ModuleType("google.genai.errors")
    errors.APIError = type("APIError", (Exception,), {})
    genai.errors = errors
    genai.types = SimpleNamespace(GenerateContentConfig=lambda **kw: kw)

    class Client:
        def __init__(self, api_key):
            self.models = SimpleNamespace(generate_content=self.generate_content)

        @staticmethod
        def generate_content(**kwargs):
            raise ValueError("unexpected SDK bug")

    genai.Client = Client
    google.genai = genai
    httpx = ModuleType("httpx")
    httpx.TransportError = type("TransportError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.errors", errors)
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")

    with pytest.raises(ValueError, match="unexpected SDK bug"):
        EnrichmentRunner(GeminiProvider()).process([pending()])
