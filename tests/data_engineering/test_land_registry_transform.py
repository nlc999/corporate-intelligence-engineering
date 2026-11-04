"""Synthetic-only tests of the isolated property snapshot transformation."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import pytest

from osint_research.data_engineering.land_registry_transform import (
    iter_ownership_rows,
    normalise_proprietors,
    select_snapshots,
    snapshot_month,
)


def test_month_selection_is_bounded_unique_and_backdatable():
    names = [
        "CCOD_FULL_2025_03.zip",
        "CCOD_FULL_2025_01.zip",
        "CCOD_COU_2025_02.zip",  # change-only, ignored
        "CCOD_FULL_2025_02.zip",
        "CCOD_FULL_2025_02.zip",
        "CCOD_FULL_2025_04.zip",
    ]
    assert select_snapshots(names, months=2, until=date(2025, 3, 28)) == [
        "CCOD_FULL_2025_02.zip",
        "CCOD_FULL_2025_03.zip",
    ]
    assert select_snapshots(names, months=1) == ["CCOD_FULL_2025_04.zip"]
    assert select_snapshots(names, months=2, until=date(2024, 12, 1)) == []


@pytest.mark.parametrize("name", ["CCOD_COU_2025_03.zip", "CCOD_FULL_2025_13.zip", "random.zip"])
def test_bad_month_or_snapshot_type_rejected(name):
    with pytest.raises(ValueError):
        snapshot_month(name)


def test_month_count_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        select_snapshots([], months=0)


def test_one_to_many_preserves_identity_and_raw_evidence():
    source = {
        "Title Number": "SYN1234",
        "Proprietor Name (1)": "Alder Properties Ltd",
        "Company Registration No. (1)": "SYN00001",
        "Proprietor Name (3)": "Northbridge Holdings Ltd",
        "Company Registration No. (3)": "SYN00003",
        "Date Proprietor Added": "2024-01-19",
    }
    rows = normalise_proprietors(source, source_file="CCOD_FULL_2025_02.zip")
    assert [(r.proprietor_index, r.company_number) for r in rows] == [
        (1, "SYN00001"), (3, "SYN00003")
    ]
    assert {r.title_number for r in rows} == {"SYN1234"}
    assert {r.source_file for r in rows} == {"CCOD_FULL_2025_02.zip"}
    assert {r.as_of_month for r in rows} == {date(2025, 2, 1)}
    assert rows[0].raw == source
    assert rows[0].raw is not source


def test_missing_registration_is_not_conjured_from_company_name():
    rows = normalise_proprietors(
        {"Title Number": "SYN1234", "Proprietor Name 2": "Unknown Holdings"},
        source_file="CCOD_FULL_2025_02.zip",
    )
    assert len(rows) == 1
    assert rows[0].proprietor_index == 2
    assert rows[0].company_number is None


def test_blank_proprietors_do_not_create_phantom_owners():
    assert normalise_proprietors(
        {"Title Number": "SYN1234", "Proprietor Name (1)": "   "},
        source_file="CCOD_FULL_2025_02.zip",
    ) == []


def test_missing_property_identity_fails_explicitly():
    with pytest.raises(ValueError, match="title number"):
        normalise_proprietors(
            {"Proprietor Name (1)": "Alder"},
            source_file="CCOD_FULL_2025_02.zip",
        )


def _make_zip(tmp_path, *, fields, records, second_csv=False):
    path = tmp_path / "CCOD_FULL_2025_02.zip"
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=fields)
    writer.writeheader()
    writer.writerows(records)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("properties.csv", text.getvalue())
        if second_csv:
            archive.writestr("other.csv", text.getvalue())
    return path


def test_streaming_zip_parser_flattens_rows(tmp_path):
    zip_path = _make_zip(
        tmp_path,
        fields=["Title Number", "Proprietor Name (1)", "Proprietor Name (2)"],
        records=[
            {"Title Number": "SYN100", "Proprietor Name (1)": "Alder", "Proprietor Name (2)": "Northbridge"},
            {"Title Number": "SYN101", "Proprietor Name (1)": "Meridian", "Proprietor Name (2)": ""},
        ],
    )
    rows = list(iter_ownership_rows(zip_path))
    assert [(r.title_number, r.proprietor_index) for r in rows] == [
        ("SYN100", 1), ("SYN100", 2), ("SYN101", 1),
    ]


def test_multiple_csvs_fail_closed(tmp_path):
    zip_path = _make_zip(
        tmp_path, fields=["Title Number"], records=[{"Title Number": "SYN100"}],
        second_csv=True,
    )
    with pytest.raises(ValueError, match="Expected one CSV"):
        list(iter_ownership_rows(zip_path))


def test_missing_title_header_fails(tmp_path):
    zip_path = _make_zip(
        tmp_path, fields=["Other"], records=[{"Other": "anything"}]
    )
    with pytest.raises(ValueError, match="title header"):
        list(iter_ownership_rows(zip_path))
