from osint_research.company_discovery.models import CompanySearchResult
from osint_research.company_discovery.ranking import RANK, rank_key
from osint_research.company_discovery.sql_queries import SUGGEST_TIERS, TIERS


def item(number, name, kind, alias=None):
    return CompanySearchResult(number, name, match_kind=kind, matched_name=alias)


def test_nine_rank_classes_are_ordered():
    assert list(RANK) == [
        "number_exact", "current_exact", "previous_exact", "current_prefix",
        "previous_prefix", "current_phrase", "previous_phrase",
        "current_fuzzy", "previous_fuzzy",
    ]
    assert [name for name, _ in TIERS] == list(RANK)


def test_exact_current_beats_former_name():
    current = item("00000006", "GREENLEAF ENGINEERING LTD", "current_exact")
    previous = item("00000001", "ALDER RENEWABLES LTD", "previous_exact", "GREENLEAF ENGINEERING LTD")
    assert rank_key("greenleaf engineering ltd", current) < rank_key("greenleaf engineering ltd", previous)


def test_exact_former_name_beats_current_prefix():
    previous = item("00000001", "ALDER RENEWABLES LTD", "previous_exact", "GREENLEAF ENGINEERING")
    prefix = item("00000002", "GREENLEAF ENGINEERING SERVICES LTD", "current_prefix")
    assert rank_key("greenleaf engineering", previous) < rank_key("greenleaf engineering", prefix)


def test_fuzzy_tie_break_is_deterministic():
    near = item("00000001", "ALDER RENEWABLES", "current_fuzzy")
    far = item("00000002", "MERIDIAN COMPONENTS", "current_fuzzy")
    assert rank_key("alder renewable", near) < rank_key("alder renewable", far)


def test_suggestions_exclude_expensive_tiers():
    assert [kind for kind, _ in SUGGEST_TIERS] == [
        "number_exact", "current_exact", "previous_exact",
        "current_prefix", "previous_prefix",
    ]


def test_all_queries_are_bounded_and_use_stable_identity():
    for kind, sql in TIERS:
        assert "LIMIT 2000" in sql
        # The cap applies *after* a deterministic SQL ordering, not before it.
        assert sql.count("ORDER BY") == (3 if kind.startswith("previous_") else 2)
        candidate_cap = sql.index("LIMIT 2000")
        inner_order = sql.rindex("ORDER BY", 0, candidate_cap)
        final_order = sql.rindex("ORDER BY")
        assert inner_order < candidate_cap < final_order
        if kind.startswith("previous_"):
            assert "SELECT DISTINCT ON (ch.company_number)" in sql
            assert "FROM best_aliases" in sql
            assert sql.index("DISTINCT ON") < inner_order
            first_order = sql.index("ORDER BY")
            assert "ch.company_number" in sql[first_order:inner_order]
            # Deduplication precedes both the 2,000-candidate cap and page cap.
            assert sql.index("FROM best_aliases") < candidate_cap
        else:
            assert "DISTINCT ON" not in sql
        assert "name_similarity DESC NULLS LAST" in sql[inner_order:candidate_cap]
        expected_name = (
            "a.previous_name_normalised"
            if kind.startswith("previous_")
            else "ch.company_name_normalised"
        )
        assert (
            "length(searched_name)" if kind.startswith("previous_")
            else f"length({expected_name})"
        ) in sql[inner_order:candidate_cap]
        assert (
            "company_number" if kind.startswith("previous_") else "ch.company_number"
        ) in sql[inner_order:candidate_cap]
        assert "matched_name" in sql[inner_order:candidate_cap]
        assert "cand.company_number" in sql[final_order:]
        assert "osint.ingested_company" in sql
        assert "company_number" in sql
        assert "company_status" in sql
        if kind.startswith("previous_"):
            assert "a.snapshot_date = ch.snapshot_date" in sql
            assert "a.previous_name_normalised" in sql
        if kind.endswith("_fuzzy"):
            assert " %% %(norm_query)s" in sql
