from osint_research.company_discovery.normalisation import (
    meaningful_length, normalise_company_name,
)


def test_punctuation_and_case():
    assert normalise_company_name("  GREENLEAF--Engineering, LTD  ") == " greenleaf engineering ltd "


def test_empty_and_punctuation_only():
    assert normalise_company_name("!!") == " "
    assert meaningful_length("!!!") == 0


def test_important_padding():
    assert normalise_company_name("Alder") == " alder "
