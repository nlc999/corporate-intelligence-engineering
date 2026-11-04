#!/usr/bin/env python3
"""Offline-first financial LLM routing showcase with explicit live-only opt-in."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from osint_research.llm_orchestration import (
    Candidate, EnrichmentRunner, GeminiProvider, MockGeminiProvider, Prediction, route,
    classify_and_route,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    p = argparse.ArgumentParser(description="Synthetic confidence-routed financial news enrichment")
    p.add_argument("--provider", choices=("mock", "gemini"), default="mock")
    p.add_argument("--allow-live", action="store_true", help="Explicitly authorise paid Gemini API requests")
    p.add_argument("--trained-head", type=Path, help="Optional trusted Case Study 02 classifier artifact")
    p.add_argument("--scenario", help="Run one example ID (default: all)")
    p.add_argument("--text", help="Optional real local-model input (must be GBP-denominated)")
    p.add_argument("--company", help="Subject company for --text")
    p.add_argument("--company-number", help="Registration number for --text")
    p.add_argument("--max-calls", type=int, default=20)
    args = p.parse_args()
    if args.provider == "gemini" and not args.allow_live:
        p.error("Live provider requires --allow-live; default mock mode makes no network calls")
    if args.provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        p.error("GEMINI_API_KEY is required in live mode; never commit credentials")
    if args.max_calls < 0:
        p.error("--max-calls must be nonnegative")

    rows = json.loads((ROOT / "examples/synthetic_routing_cases.json").read_text())["cases"]
    if args.scenario:
        rows = [row for row in rows if row["id"] == args.scenario]
        if not rows:
            p.error("Unknown synthetic scenario")
    if args.text and (not args.trained_head or not args.company or not args.company_number):
        p.error("--text requires --trained-head, --company, and --company-number")
    predictions = None
    if args.trained_head:
        from osint_research.information_extraction.classifier import from_artifact
        predictions = from_artifact(args.trained_head)

    records = []
    if args.text:
        from osint_research.llm_orchestration.candidate_detection import detect_candidate
        candidate = detect_candidate(args.text, source_id="custom-synthetic",
                                     company_name=args.company, company_number=args.company_number)
        records.append(classify_and_route(candidate, predictions))
        rows = []
    for row in rows:
        candidate = Candidate(**{k:row[k] for k in (
            "source_id", "sentence", "company_name", "company_number",
            "amount_gbp", "financial_cue", "attribution",
        )})
        if "classifier_error" in row and predictions is None:
            record = route(candidate, None, classifier_error=row["classifier_error"])
        elif predictions is not None:
            record = classify_and_route(candidate, predictions)
        else:
            record = route(candidate, Prediction(**row["prediction"]))
        records.append(record)

    fixtures = json.loads((ROOT / "examples/mock_gemini_responses.json").read_text())
    provider = (
        GeminiProvider()
        if args.provider == "gemini"
        else MockGeminiProvider(responses=fixtures["responses"], failures=fixtures["failures"])
    )
    runner = EnrichmentRunner(provider, max_calls=args.max_calls, backoff_seconds=(
        1.0 if args.provider == "gemini" else 0.0
    ))
    results = runner.process(records)
    print(json.dumps({
        "mode": args.provider,
        "notice": ("Mocked provider outputs: no real model predictions" if args.provider == "mock"
                   else "Live Gemini provider explicitly enabled"),
        "results": [result.as_dict() for result in results],
        "provider_calls": getattr(provider, "calls", None),
    }, indent=2))


if __name__ == "__main__":
    main()
