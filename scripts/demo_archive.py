#!/usr/bin/env python3
"""Archived-partition showcase. Default mode is offline and read-only."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date
from pathlib import Path

from osint_research.cloud_archival import (
    PartitionRef, archive_partition, discover_eligible_partitions, plan_archive
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Non-destructive partition archival demo")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--retention-months", type=int, default=24)
    parser.add_argument("--dry-run", action="store_true", help="Explicit offline-only mode (default)")
    parser.add_argument("--live-upload", action="store_true",
                        help="Opt in to PostgreSQL pg_dump and AWS S3 upload")
    parser.add_argument("--confirm-live-upload", action="store_true",
                        help="Required with --live-upload; source partition is never deleted")
    parser.add_argument("--schema", default="ingest")
    parser.add_argument("--parent", default="fact_event")
    parser.add_argument("--partition", help="Exactly one existing PostgreSQL child partition")
    parser.add_argument("--bucket", help="Real S3 bucket, only used in live mode")
    args = parser.parse_args()
    if args.dry_run and args.live_upload:
        parser.error("--dry-run and --live-upload are mutually exclusive")
    if not args.live_upload:
        example = json.loads((ROOT / "examples/synthetic_partitions.json").read_text())
        cases = [PartitionRef(**entry) for entry in example["partitions"]]
        print(json.dumps({
            "mode": "OFFLINE - synthetic records; no database or AWS access",
            "as_of": args.as_of.isoformat(),
            "plans": [
                plan_archive(ref, as_of=args.as_of, retention_months=args.retention_months)
                for ref in cases
            ],
        }, indent=2))
        return

    if not args.confirm_live_upload:
        parser.error("--live-upload also requires --confirm-live-upload")
    if not args.bucket or not args.partition:
        parser.error("--live-upload requires --bucket and --partition")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        parser.error("Set DATABASE_URL in the environment for --live-upload")
    import boto3
    import psycopg

    wanted = PartitionRef(args.schema, args.parent, args.partition)
    with psycopg.connect(db_url) as connection:
        available = discover_eligible_partitions(
            connection, schema=args.schema, parent=args.parent,
            as_of=args.as_of, retention_months=args.retention_months,
        )
    if wanted not in available:
        parser.error("Partition is not an eligible child of the requested table")

    with tempfile.TemporaryDirectory(prefix="osint-archive-") as tmp:
        receipt = archive_partition(
            wanted, db_url=db_url, bucket=args.bucket,
            client=boto3.client("s3"),
            output_dir=Path(tmp), as_of=args.as_of,
            retention_months=args.retention_months,
        )
    print(json.dumps(receipt.__dict__, indent=2))
    print("Verified uploaded object. PostgreSQL source partition was NOT deleted.")


if __name__ == "__main__":
    main()
