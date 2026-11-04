"""Source-structure checks; these do not substitute for a live DB test."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ddl_contains_the_expected_index_families():
    ddl = (ROOT / "sql/company_search.sql").read_text()
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in ddl
    assert "USING GIN (company_name_normalised gin_trgm_ops)" in ddl
    assert "company_name_normalised text_pattern_ops" in ddl
    assert "company_number TEXT" in ddl
    assert "company_previous_name_search_staging" in ddl


def test_no_real_registry_api_or_ingestion_endpoint_in_demo():
    from osint_research.company_discovery import search
    from osint_research.company_discovery import historical_names

    import inspect

    combined = inspect.getsource(search) + inspect.getsource(historical_names)
    assert "requests.get(" not in combined
    assert "httpx.get(" not in combined
    assert "api.company-information.service.gov.uk" not in combined
