"""Non-destructive PostgreSQL-to-S3 archival evidence; offline-safe by default.

Adapted from historically implemented archival work. This module never executes
DROP TABLE, DETACH PARTITION, or other destructive PostgreSQL operations.
External clients (psycopg, boto3) are only required for an explicit live upload.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
MAX_DEMO_BYTES = 64 * 1024 * 1024
CHUNK = 1024 * 1024


def safe_identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid PostgreSQL identifier")
    return value


@dataclass(frozen=True)
class PartitionRef:
    schema: str
    parent: str
    partition: str

    def __post_init__(self) -> None:
        for field in (self.schema, self.parent, self.partition):
            safe_identifier(field)
        suffix = re.escape(self.parent) + r"_([0-9]{4})_(0[1-9]|1[0-2])"
        if not re.fullmatch(suffix, self.partition):
            raise ValueError("Expected parent_YYYY_MM partition name")
        # Reject impossible dates, even when the name matches the regex.
        _ = self.month

    @property
    def month(self) -> date:
        year, month = self.partition[len(self.parent) + 1:].split("_")
        return date(int(year), int(month), 1)


@dataclass(frozen=True)
class ArchiveReceipt:
    partition: str
    key: str
    size_bytes: int
    sha256: str
    verified: bool


def retention_cutoff(as_of: date, retention_months: int) -> date:
    if not 1 <= retention_months <= 1200:
        raise ValueError("Retention period must be 1..1200 months")
    index = as_of.year * 12 + as_of.month - 1 - retention_months
    return date(index // 12, index % 12 + 1, 1)


def eligible(ref: PartitionRef, *, as_of: date, retention_months: int = 24) -> bool:
    return ref.month < retention_cutoff(as_of, retention_months)


def discover_eligible_partitions(
    conn: Any, *, schema: str, parent: str, as_of: date, retention_months: int = 24
) -> list[PartitionRef]:
    """Discover real partition children through pg_inherits, using bound params."""
    safe_identifier(schema)
    safe_identifier(parent)
    sql = """
        SELECT child_ns.nspname, parent_class.relname, child.relname
        FROM pg_inherits inh
        JOIN pg_class child ON child.oid = inh.inhrelid
        JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
        JOIN pg_class parent_class ON parent_class.oid = inh.inhparent
        JOIN pg_namespace parent_ns ON parent_ns.oid = parent_class.relnamespace
        WHERE parent_ns.nspname = %s AND parent_class.relname = %s
        ORDER BY child.relname
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (schema, parent))
        rows = cursor.fetchall()
    matches = []
    for child_schema, parent_name, child_name in rows:
        try:
            ref = PartitionRef(child_schema, parent_name, child_name)
        except (ValueError, TypeError):
            continue
        if ref.schema == schema and eligible(ref, as_of=as_of, retention_months=retention_months):
            matches.append(ref)
    return matches


def plan_archive(ref: PartitionRef, *, as_of: date, retention_months: int = 24) -> dict[str, str | bool]:
    """No DB, filesystem, network or AWS side effects."""
    return {
        "partition": f"{ref.schema}.{ref.partition}",
        "partition_month": ref.month.isoformat(),
        "eligible": eligible(ref, as_of=as_of, retention_months=retention_months),
        "action": "export-upload-verify (no database deletion)",
    }


def object_key(ref: PartitionRef, checksum: str, prefix: str = "archives") -> str:
    if not prefix or not re.fullmatch(r"[a-z0-9_/-]+", prefix) or ".." in prefix or prefix.startswith("/"):
        raise ValueError("Invalid key prefix")
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid SHA-256 checksum")
    return (
        f"{prefix.rstrip('/')}/{ref.schema}/{ref.parent}/"
        f"{ref.month.year:04d}/{ref.month.month:02d}/{ref.partition}-{checksum}.dump"
    )


def pg_dump_args(db_url: str, ref: PartitionRef, target: Path) -> tuple[list[str], dict[str, str]]:
    parsed = urlsplit(db_url)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("Expected PostgreSQL URL with hostname and database")
    port = parsed.port or 5432
    if not 1 <= port <= 65535:
        raise ValueError("Invalid PostgreSQL port")
    args = [
        "pg_dump",
        "--host", parsed.hostname,
        "--port", str(port),
        "--username", unquote(parsed.username or "postgres"),
        "--dbname", unquote(parsed.path.lstrip("/")),
        "--format=custom", "--no-owner",
        "--table", f"{ref.schema}.{ref.partition}",
        "--file", str(target),
    ]
    environment = dict(os.environ)
    if parsed.password is not None:
        environment["PGPASSWORD"] = unquote(parsed.password)
    return args, environment


def export_partition(
    ref: PartitionRef, *, db_url: str, target: Path, runner: Callable[..., Any] = subprocess.run
) -> Path:
    """Validate a private same-directory dump, then publish without overwriting.

    An atomic hard link creates the final pathname only if it is unoccupied.
    Unlike os.replace(), it never silently overwrites a concurrent export.
    """
    if target.exists() or target.is_symlink():
        raise FileExistsError("Archive output already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, stage_name = tempfile.mkstemp(
        prefix=f".{target.name}.partial-", suffix=".tmp", dir=target.parent,
    )
    os.close(fd)
    stage = Path(stage_name)
    try:
        args, env = pg_dump_args(db_url, ref, stage)
        runner(args, check=True, timeout=300, capture_output=True, text=True, env=env)
        _validated_local_digest(stage)
        os.link(stage, target)
        return target
    finally:
        # Only remove this attempt's staging path. Completed local exports
        # remain available for audit and explicit retry after S3 failures.
        stage.unlink(missing_ok=True)


def _validated_local_digest(path: Path) -> tuple[int, str]:
    """Check the pg_dump custom-format header and size; not a restore test."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Archive is not a regular file")
    size = path.stat().st_size
    if size < 5:
        raise RuntimeError("pg_dump did not produce a non-empty custom-format archive")
    if size > MAX_DEMO_BYTES:
        raise ValueError("Demo archives are limited to 64 MiB")
    with path.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise RuntimeError("Archive does not have the pg_dump custom-format header")
    return file_digest(path)


def file_digest(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            total += len(chunk)
            h.update(chunk)
    return total, h.hexdigest()


def verify_s3(client: Any, *, bucket: str, key: str, size: int, checksum: str) -> None:
    """Object metadata and a streamed readback must both agree."""
    head = client.head_object(Bucket=bucket, Key=key)
    if head.get("ContentLength") != size:
        raise RuntimeError("S3 object size mismatch")
    if (head.get("Metadata") or {}).get("sha256") != checksum:
        raise RuntimeError("S3 checksum metadata mismatch")
    result = client.get_object(Bucket=bucket, Key=key)
    body = result["Body"]
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = body.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DEMO_BYTES:
                raise RuntimeError("Remote archive exceeds demo size cap")
            digest.update(chunk)
    finally:
        body.close()
    if total != size or digest.hexdigest() != checksum:
        raise RuntimeError("S3 integrity verification failed: downloaded content SHA-256 mismatch")


def archive_partition(
    ref: PartitionRef, *, db_url: str, bucket: str, client: Any, output_dir: Path,
    as_of: date, retention_months: int = 24,
    runner: Callable[..., Any] = subprocess.run,
    reuse_existing: bool = False,
) -> ArchiveReceipt:
    """Export, upload, verify; NEVER detach or drop the partition.

    Reusing a completed local dump requires explicit opt-in. Invalid files,
    including partial exports or symlinks, cannot be uploaded through reuse.
    """
    if not BUCKET.fullmatch(bucket) or ".." in bucket:
        raise ValueError("Invalid S3 bucket")
    if not eligible(ref, as_of=as_of, retention_months=retention_months):
        raise ValueError("Partition is not eligible for archival")
    target = output_dir / f"{ref.partition}.dump"
    if not (reuse_existing and (target.exists() or target.is_symlink())):
        export_partition(ref, db_url=db_url, target=target, runner=runner)
    size, checksum = _validated_local_digest(target)
    key = object_key(ref, checksum)
    client.upload_file(
        str(target), bucket, key,
        ExtraArgs={"ServerSideEncryption": "AES256", "Metadata": {"sha256": checksum}},
    )
    verify_s3(client, bucket=bucket, key=key, size=size, checksum=checksum)
    return ArchiveReceipt(ref.partition, key, size, checksum, True)
