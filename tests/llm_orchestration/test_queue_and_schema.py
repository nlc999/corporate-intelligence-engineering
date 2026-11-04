import json
from dataclasses import replace

import pytest

from osint_research.llm_orchestration import Candidate, Prediction, route, plan_queue
from osint_research.llm_orchestration.queue import cache_key
from osint_research.llm_orchestration.schemas import parse_provider_response


def record(i, *, source="same", score=.2, label="profit"):
    c = Candidate(source_id=source, sentence=f"Financial report number {i} describes £7m in profits.",
                  company_name="Alder", company_number="00000001", amount_gbp=7_000_000,
                  financial_cue=True)
    return route(c, Prediction(label, score))


def test_five_item_cap_and_final_promotion_provenance():
    plan = plan_queue([record(i, score=.1 + i*.01) for i in range(7)])
    assert len(plan.queued) == 5
    overflow = [item.record for item in plan.other if item.record.final_status == "queue_cap_local"]
    assert len(overflow) == 2
    assert all(r.initial_status == "local_pending" and r.reason_code == "QUEUE_CAP_PROMOTED" for r in overflow)


def test_provider_priority_and_immutable_cache_key():
    plan = plan_queue([record(1, source="a", label="profit"), record(2, source="b", label="capex_investment"),
                       record(3, source="c", label="acquisition")])
    assert [item.record.prediction.event_type for item in plan.queued] == [
        "capex_investment", "acquisition", "profit"
    ]
    kwargs = dict(
        provider="mock_gemini", model="test",
        classifier_version="fixture-v1", prompt_policy_version="prompt-v1",
    )
    assert cache_key(plan.queued[0].record, **kwargs) == cache_key(
        plan.queued[0].record, **kwargs
    )


def test_cache_identity_includes_prediction_provider_source_and_versions():
    initial = record(1, source="a", label="profit")
    kwargs = dict(
        model="gemini-test", provider="mock_gemini",
        classifier_version="classifier-v1", prompt_policy_version="prompt-v1",
    )
    original = cache_key(initial, **kwargs)
    assert len(original) == 64
    assert original == cache_key(initial, **kwargs)

    changes = [
        (record(1, source="a", label="acquisition"), kwargs),
        (record(1, source="b", label="profit"), kwargs),
        (initial, {**kwargs, "model": "gemini-other"}),
        (initial, {**kwargs, "provider": "google_gemini"}),
        (initial, {**kwargs, "classifier_version": "classifier-v2"}),
        (initial, {**kwargs, "prompt_policy_version": "prompt-v2"}),
    ]
    assert all(cache_key(item, **options) != original for item, options in changes)

    # Confidence changes alone do not alter what Gemini sees or the parser
    # fallback event type; both records remain eligible for escalation.
    assert cache_key(record(1, source="a", score=.3), **kwargs) == original


def test_cache_identity_rejects_missing_prediction_or_versions():
    no_prediction = route(record(1).candidate, None, classifier_error="offline")
    kwargs = dict(
        model="gemini-test", provider="mock_gemini",
        classifier_version="classifier-v1", prompt_policy_version="prompt-v1",
    )
    with pytest.raises(ValueError, match="requires a local prediction"):
        cache_key(no_prediction, **kwargs)
    for field in kwargs:
        with pytest.raises(ValueError, match="must be nonempty"):
            cache_key(record(1), **{**kwargs, field: ""})


def test_duplicate_sentence_deduplication():
    one = record(1, score=.2)
    two = record(1, score=.3)
    plan = plan_queue([one, two])
    assert len(plan.queued) == 1
    assert plan.queued[0].record.prediction.confidence == .3
    assert any(item.record.final_status == "deduplicated" for item in plan.other)


def test_identical_sentence_for_distinct_subject_companies_is_not_deduplicated():
    alder = record(1, source="filing")
    other_company = replace(
        alder, candidate=replace(
            alder.candidate, company_number="00000002", company_name="Birch",
        ),
    )
    plan = plan_queue([alder, other_company])
    assert [item.source_index for item in plan.queued] == [0, 1]
    assert [item.record.candidate.company_number for item in plan.queued] == [
        "00000001", "00000002",
    ]
    assert not plan.other


def test_distinct_prediction_or_context_is_not_deduplicated():
    base = record(1)
    different_prediction = record(1, label="acquisition")
    different_context = replace(
        base, candidate=replace(base.candidate, supporting_context="Second paragraph"),
    )
    plan = plan_queue([base, different_prediction, different_context])
    assert sorted(item.source_index for item in plan.queued) == [0, 1, 2]
    assert not plan.other


def test_duplicates_do_not_consume_unique_source_slots():
    repeated = [record(0, score=.15 + i * .01) for i in range(6)]
    unique = [record(i, score=.25 + i * .01) for i in range(1, 5)]
    plan = plan_queue([*repeated, *unique])

    assert len(plan.queued) == 5
    assert len(plan.other) == 5
    assert {item.record.candidate.sentence for item in plan.queued} == {
        item.candidate.sentence for item in [repeated[0], *unique]
    }
    assert [item.source_index for item in plan.queued] == [5, 6, 7, 8, 9]
    assert sorted(item.source_index for item in plan.other) == [0, 1, 2, 3, 4]
    assert all(item.record.final_status == "deduplicated" for item in plan.other)
    assert not any(item.record.final_status == "queue_cap_local" for item in plan.other)


def test_source_cap_still_applies_to_distinct_events_after_deduplication():
    repeated = [record(0, score=.1), record(0, score=.3)]
    unique = [record(i, score=.2) for i in range(1, 6)]
    plan = plan_queue([*repeated, *unique], per_source_cap=5)
    assert len(plan.queued) == 5
    assert [item.record.final_status for item in plan.other].count("deduplicated") == 1
    assert [item.record.final_status for item in plan.other].count("queue_cap_local") == 1
    assert sorted(item.source_index for item in (*plan.queued, *plan.other)) == list(range(7))


def test_input_indices_survive_priority_queueing_and_deduplication():
    duplicate = record(1, source="duplicate", score=.2)
    inputs = [
        duplicate,
        record(2, source="ordinary", score=.8),
        record(3, source="urgent", score=.2, label="capex_investment"),
        duplicate,  # Same object occupies two separate input positions.
    ]
    plan = plan_queue(inputs)
    assert [item.source_index for item in plan.queued] == [2, 0]
    assert sorted(item.source_index for item in (*plan.queued, *plan.other)) == list(range(4))
    assert [(item.source_index, item.record.final_status) for item in plan.other] == [
        (1, "local"), (3, "deduplicated"),
    ]


def test_schema_alias_and_nested_shapes():
    parsed = parse_provider_response(json.dumps({"classification":{"metric_type":"investment",
                 "is_false_positive":False}, "geo_scope":"GB"}), tier2_event_type="profit")
    assert parsed.metric_type == "capex_investment"


@pytest.mark.parametrize("payload", [
    '{"metric_type":"space_mining","geo_scope":"GB","is_false_positive":false}',
    '{"metric_type":"profit","geo_scope":"Mars","is_false_positive":false}',
    '{"metric_type":"profit","geo_scope":"GB","is_false_positive":"false"}',
    '{"metric_type":"profit","geo_scope":"GB","is_false_positive":false,"extra":"unknown"}',
    'not JSON', '[]',
])
def test_invalid_response_is_rejected(payload):
    with pytest.raises((ValueError, TypeError)):
        parse_provider_response(payload, tier2_event_type="profit")
