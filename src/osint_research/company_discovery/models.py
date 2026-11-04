"""Minimal public-facing search result, detached from a larger application."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompanySearchResult:
    company_number: str
    company_name: str
    company_status: str = "active"
    locality: str | None = None
    already_ingested: bool = False
    match_kind: str | None = None
    matched_name: str | None = None

    @classmethod
    def from_row(cls, row: dict, kind: str) -> "CompanySearchResult":
        return cls(
            company_number=row["company_number"],
            company_name=row["company_name"],
            company_status=row.get("company_status") or "",
            locality=row.get("locality"),
            already_ingested=bool(row.get("already_ingested", False)),
            match_kind=kind,
            matched_name=row.get("matched_name") if kind.startswith("previous_") else None,
        )
