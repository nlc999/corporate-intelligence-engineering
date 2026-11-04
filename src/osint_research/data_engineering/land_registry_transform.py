"""Streaming ownership normalisation for synthetic monthly property snapshots.

Adapted from a bulk property-register crawler's ZIP/CSV iteration, monthly
selection and up-to-four-proprietor flattening. Deliberately no HTTP or SQL:
acquisition and durable ingestion are distinct system boundaries.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path


_FULL_SNAPSHOT = re.compile(r"^CCOD_FULL_(\d{4})_(\d{2})\.zip$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class OwnershipRow:
    title_number: str
    proprietor_index: int
    proprietor_name: str
    company_number: str | None
    date_proprietor_added: str | None
    as_of_month: date
    source_file: str
    raw: dict[str, str]


def snapshot_month(filename: str) -> date:
    """Require a full monthly snapshot rather than an incremental change file."""
    match = _FULL_SNAPSHOT.fullmatch(Path(filename).name)
    if match is None:
        raise ValueError(f"Not a full monthly property snapshot: {filename!r}")
    return date(int(match.group(1)), int(match.group(2)), 1)


def select_snapshots(
    filenames: Iterable[str], *, months: int, until: date | None = None
) -> list[str]:
    """Select at most N distinct monthly snapshots, oldest to newest."""
    if months < 1:
        raise ValueError("months must be positive")
    selected: dict[date, str] = {}
    for filename in filenames:
        try:
            month = snapshot_month(filename)
        except ValueError:
            continue  # A source listing can also contain incremental files.
        if until is not None and (month.year, month.month) > (until.year, until.month):
            continue
        prior = selected.setdefault(month, filename)
        if prior != filename:
            raise ValueError(f"Ambiguous full snapshot for {month:%Y-%m}")
    return [selected[key] for key in sorted(selected)[-months:]]


def _first(row: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and value.strip():
            return value.strip()
    return ""


def normalise_proprietors(
    row: Mapping[str, str], *, source_file: str
) -> list[OwnershipRow]:
    """Flatten 0–4 numbered proprietor columns, retaining source evidence."""
    month = snapshot_month(source_file)
    title_number = _first(row, "Title Number", "Title number", "title_number")
    if not title_number:
        raise ValueError("Property record is missing its title number")

    result: list[OwnershipRow] = []
    for index in range(1, 5):
        name = _first(row, f"Proprietor Name ({index})", f"Proprietor Name {index}")
        if not name:
            continue
        company_number = _first(
            row,
            f"Company Registration No. ({index})",
            f"Company Registration No. {index}",
        )
        added = _first(
            row,
            f"Date Proprietor Added ({index})",
            f"Date Proprietor Added {index}",
            "Date Proprietor Added",
            "date_proprietor_added",
        )
        result.append(
            OwnershipRow(
                title_number=title_number,
                proprietor_index=index,
                proprietor_name=name,
                company_number=company_number or None,
                date_proprietor_added=added or None,
                as_of_month=month,
                source_file=source_file,
                raw=dict(row),
            )
        )
    return result


def iter_ownership_rows(zip_path: Path) -> Iterator[OwnershipRow]:
    """Yield normalised records without loading a whole compressed CSV.

    The demo accepts one CSV per ZIP; an ambiguous archive fails explicitly,
    rather than silently selecting a guessed source file.
    """
    snapshot_month(zip_path.name)
    with zipfile.ZipFile(zip_path) as archive:
        members = [
            name for name in archive.namelist()
            if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
        ]
        if len(members) != 1:
            raise ValueError(f"Expected one CSV in {zip_path.name}; got {len(members)}")
        with archive.open(members[0]) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
                reader = csv.DictReader(text)
                if not reader.fieldnames or not any(
                    key in reader.fieldnames
                    for key in ("Title Number", "Title number", "title_number")
                ):
                    raise ValueError("Missing property title header")
                for row in reader:
                    if None in row:
                        raise ValueError("Malformed CSV: extra unnamed columns")
                    yield from normalise_proprietors(row, source_file=zip_path.name)
