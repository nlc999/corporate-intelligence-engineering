from datetime import date

import pytest

from osint_research.company_discovery.historical_names import (
    RegistryCompany, aliases_for, validate_snapshot,
)


def company(number="00000001", snapshot=date(2026, 9, 30)):
    return RegistryCompany(
        company_number=number, company_name="ALDER RENEWABLES LIMITED",
        company_status="active", locality="Leeds",
        previous_names=("GREENLEAF ENGINEERING LTD", "Greenleaf-Engineering LTD",
                        "ALDER RENEWABLES LIMITED", "??"), snapshot_date=snapshot,
    )


def test_alias_dedup_and_current_name_suppression():
    assert aliases_for(company()) == [
        ("00000001", "GREENLEAF ENGINEERING LTD", date(2026, 9, 30))
    ]


def test_reject_mixed_snapshot():
    with pytest.raises(ValueError, match="mixed"):
        validate_snapshot([company(), company("00000002", date(2026, 8, 31))])


def test_reject_duplicate_registration():
    with pytest.raises(ValueError, match="Duplicate"):
        validate_snapshot([company(), company()])


def test_reject_empty_snapshot():
    with pytest.raises(ValueError):
        validate_snapshot([])
