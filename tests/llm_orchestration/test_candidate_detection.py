"""The limited GBP detector is tested separately from pre-labelled fixtures."""
import pytest

from osint_research.llm_orchestration.candidate_detection import detect_candidate


def detect(text):
    return detect_candidate(text, source_id="a", company_name="Alder Renewables",
                            company_number="00000001")


def test_million_units_and_financial_cue():
    c = detect("Alder Renewables invested £240 million in manufacturing expansion.")
    assert c.amount_gbp == 240_000_000
    assert c.attribution == "subject" and c.financial_cue is True


def test_bn_and_thousand_units():
    assert detect("Alder Renewables invested GBP2.5bn.").amount_gbp == 2_500_000_000
    assert detect("Alder Renewables reported £750k profit.").amount_gbp == 750_000


def test_unattributed_mentions_remain_ambiguous():
    assert detect("The manufacturer announced £20m in revenue.").attribution == "ambiguous"


def test_non_gbp_text_fails_loudly():
    with pytest.raises(ValueError, match="GBP"):
        detect("The company reported $20 million in profit.")


def test_first_amount_limitation_is_explicit():
    assert detect("Alder Renewables invested £20m then cancelled £30m.").amount_gbp == 20_000_000
