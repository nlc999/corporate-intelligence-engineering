"""No AWS account, PostgreSQL, or pg_dump required: mocks exercise contracts."""
from __future__ import annotations

import hashlib
import io
from datetime import date
from pathlib import Path

import pytest

from osint_research.cloud_archival.partition_archive import (
    PartitionRef, archive_partition, discover_eligible_partitions, export_partition,
    eligible, file_digest, object_key, pg_dump_args, plan_archive,
    retention_cutoff, safe_identifier, verify_s3,
)


def ref(suffix="2022_01"):
    return PartitionRef("ingest", "fact_event", f"fact_event_{suffix}")


class FakeBody(io.BytesIO):
    pass


class FakeS3:
    def __init__(self, corrupt=False, wrong_metadata=False):
        self.objects = {}
        self.corrupt = corrupt
        self.wrong_metadata = wrong_metadata
        self.uploads = []

    def upload_file(self, filename, bucket, key, ExtraArgs):
        data = Path(filename).read_bytes()
        self.uploads.append((bucket, key, ExtraArgs))
        self.objects[(bucket, key)] = (data, ExtraArgs["Metadata"])

    def head_object(self, Bucket, Key):
        data, metadata = self.objects[(Bucket, Key)]
        if self.wrong_metadata:
            metadata = {"sha256": "0" * 64}
        return {"ContentLength": len(data), "Metadata": metadata}

    def get_object(self, Bucket, Key):
        data, _ = self.objects[(Bucket, Key)]
        return {"Body": FakeBody(b"corrupted" if self.corrupt else data)}


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)

    def cursor(self):
        return self.cur


def test_month_cutoff_and_reject_new_partitions():
    assert retention_cutoff(date(2026, 10, 18), 24) == date(2024, 10, 1)
    assert eligible(ref("2022_01"), as_of=date(2026, 10, 18))
    assert not eligible(ref("2024_10"), as_of=date(2026, 10, 18))
    assert not eligible(ref("2026_09"), as_of=date(2026, 10, 18))
    assert plan_archive(ref(), as_of=date(2026, 10, 18))["action"].startswith("export")
    with pytest.raises(ValueError):
        retention_cutoff(date.today(), 0)


@pytest.mark.parametrize("value", ["abc;DROP TABLE users", 'a.b', "../tmp", "", "0starts", "x"*64])
def test_identifier_rejection(value):
    with pytest.raises(ValueError):
        safe_identifier(value)


def test_partition_rejects_bad_months_and_unrelated_names():
    for value in ("fact_event_2022_13", "other_2022_01", "fact_event_0000_01"):
        with pytest.raises(ValueError):
            PartitionRef("ingest", "fact_event", value)


def test_discovery_uses_real_catalogue_relationships_and_bound_values():
    db = FakeConnection([
        ("ingest", "fact_event", "fact_event_2022_01"),
        ("ingest", "fact_event", "fact_event_2026_09"),
        ("other", "fact_event", "fact_event_2022_01"),
        ("ingest", "fact_event", "fact_event_default"),
    ])
    found = discover_eligible_partitions(
        db, schema="ingest", parent="fact_event",
        as_of=date(2026, 10, 18),
    )
    assert found == [ref()]
    assert "pg_inherits" in db.cur.sql
    assert db.cur.params == ("ingest", "fact_event")


def test_pg_dump_does_not_leak_password_to_process_arguments(tmp_path):
    args, env = pg_dump_args(
        "postgresql://demo:verysecret@localhost:5432/sample",
        ref(), tmp_path / "file.dump",
    )
    assert "verysecret" not in " ".join(args)
    assert env["PGPASSWORD"] == "verysecret"
    assert args[0] == "pg_dump"
    assert args[args.index("--table") + 1] == "ingest.fact_event_2022_01"
    assert "--format=custom" in args


def test_content_addressed_key_is_deterministic():
    checksum = hashlib.sha256(b"test").hexdigest()
    a = object_key(ref(), checksum)
    assert a == object_key(ref(), checksum)
    assert a.endswith(checksum + ".dump")
    with pytest.raises(ValueError):
        object_key(ref(), checksum, "../unsafe")


def _fake_runner(contents: bytes):
    def run(args, **kwargs):
        Path(args[args.index("--file") + 1]).write_bytes(contents)
        assert kwargs["timeout"] == 300
        assert kwargs["check"]
    return run


def test_archive_success_verifies_streaming_content_without_deletion(tmp_path):
    client = FakeS3()
    output = archive_partition(
        ref(), db_url="postgresql://demo:pass@localhost/demo",
        bucket="synthetic-archive-bucket", client=client,
        output_dir=tmp_path, as_of=date(2026, 10, 18),
        runner=_fake_runner(b"PGDMP synthetic fixture"),
    )
    assert output.verified
    assert output.size_bytes == len(b"PGDMP synthetic fixture")
    assert len(client.uploads) == 1
    assert client.uploads[0][2]["ServerSideEncryption"] == "AES256"
    assert (tmp_path / "fact_event_2022_01.dump").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fact_event_2022_01.dump"]


def test_corrupt_remote_archive_does_not_return_verified_result(tmp_path):
    client = FakeS3(corrupt=True)
    with pytest.raises(RuntimeError, match="integrity"):
        archive_partition(
            ref(), db_url="postgresql://demo@localhost/db",
            bucket="synthetic-archive-bucket", client=client,
            output_dir=tmp_path, as_of=date(2026, 10, 18),
            runner=_fake_runner(b"PGDMP GOOD DATA"),
        )
    target = tmp_path / "fact_event_2022_01.dump"
    assert target.exists()
    assert target.read_bytes() == b"PGDMP GOOD DATA"

    # Retry remote verification without re-running pg_dump.
    client.corrupt = False

    def must_not_export(*args, **kwargs):
        pytest.fail("Completed local dump should be reused")

    receipt = archive_partition(
        ref(), db_url="postgresql://demo@localhost/db",
        bucket="synthetic-archive-bucket", client=client,
        output_dir=tmp_path, as_of=date(2026, 10, 18),
        runner=must_not_export, reuse_existing=True,
    )
    assert receipt.verified
    assert receipt.size_bytes == len(b"PGDMP GOOD DATA")
    assert len(client.uploads) == 2
    assert list(tmp_path.iterdir()) == [target]


def test_remote_metadata_mismatch_rejected(tmp_path):
    client = FakeS3(wrong_metadata=True)
    with pytest.raises(RuntimeError, match="metadata"):
        archive_partition(
            ref(), db_url="postgresql://demo@localhost/db",
            bucket="synthetic-archive-bucket", client=client,
            output_dir=tmp_path, as_of=date(2026, 10, 18),
            runner=_fake_runner(b"PGDMP GOOD DATA"),
        )


def test_export_failure_does_not_upload(tmp_path):
    client = FakeS3()
    def fail(*args, **kwargs):
        raise RuntimeError("pg_dump failed")
    with pytest.raises(RuntimeError, match="pg_dump"):
        archive_partition(
            ref(), db_url="postgresql://demo@localhost/db",
            bucket="synthetic-archive-bucket", client=client,
            output_dir=tmp_path, as_of=date(2026, 10, 18), runner=fail,
        )
    assert client.uploads == []
    assert not list(tmp_path.iterdir())


def test_partial_pg_dump_failure_is_cleaned_and_retryable(tmp_path):
    target = tmp_path / "fact_event_2022_01.dump"
    staged = []

    def interrupt(args, **kwargs):
        temporary = Path(args[args.index("--file") + 1])
        assert temporary != target
        assert temporary.name.startswith(f".{target.name}.partial-")
        assert temporary.parent == target.parent
        temporary.write_bytes(b"PGDMP partial")
        staged.append(temporary)
        raise RuntimeError("pg_dump interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        export_partition(ref(), db_url="postgresql://demo@localhost/db",
                         target=target, runner=interrupt)
    assert not target.exists()
    assert staged and not staged[0].exists()
    assert list(tmp_path.iterdir()) == []

    assert export_partition(
        ref(), db_url="postgresql://demo@localhost/db", target=target,
        runner=_fake_runner(b"PGDMP recovered"),
    ) == target
    assert target.read_bytes() == b"PGDMP recovered"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("bad_bytes", [b"", b"tiny", b"NOT_A_DUMP"])
def test_invalid_dump_is_never_published(tmp_path, bad_bytes):
    target = tmp_path / "fact_event_2022_01.dump"
    with pytest.raises(RuntimeError):
        export_partition(
            ref(), db_url="postgresql://demo@localhost/db", target=target,
            runner=_fake_runner(bad_bytes),
        )
    assert list(tmp_path.iterdir()) == []


def test_racing_writer_cannot_be_overwritten(tmp_path):
    target = tmp_path / "fact_event_2022_01.dump"

    def competing(args, **kwargs):
        stage = Path(args[args.index("--file") + 1])
        stage.write_bytes(b"PGDMP valid staging file")
        target.write_bytes(b"already published")

    with pytest.raises(FileExistsError):
        export_partition(ref(), db_url="postgresql://demo@localhost/db",
                         target=target, runner=competing)
    assert target.read_bytes() == b"already published"
    assert list(tmp_path.iterdir()) == [target]


def test_old_partial_staging_file_does_not_block_new_export(tmp_path):
    abandoned = tmp_path / ".fact_event_2022_01.dump.partial-stale.tmp"
    abandoned.write_bytes(b"incomplete")
    target = tmp_path / "fact_event_2022_01.dump"
    assert export_partition(
        ref(), db_url="postgresql://demo@localhost/db",
        target=target, runner=_fake_runner(b"PGDMP valid"),
    ) == target
    # A different process might own the abandoned name; never delete it.
    assert abandoned.read_bytes() == b"incomplete"
    assert target.read_bytes() == b"PGDMP valid"


def test_ineligible_partition_blocks_export_and_upload(tmp_path):
    client = FakeS3()
    with pytest.raises(ValueError, match="eligible"):
        archive_partition(
            ref("2026_09"), db_url="postgresql://demo@localhost/db",
            bucket="synthetic-archive-bucket", client=client,
            output_dir=tmp_path, as_of=date(2026, 10, 18),
            runner=_fake_runner(b"ignore"),
        )
    assert not list(tmp_path.iterdir())


def test_duplicate_export_file_fails_closed(tmp_path):
    target = tmp_path / "fact_event_2022_01.dump"
    target.write_bytes(b"previous")
    with pytest.raises(FileExistsError):
        archive_partition(
            ref(), db_url="postgresql://demo@localhost/db",
            bucket="synthetic-archive-bucket", client=FakeS3(),
            output_dir=tmp_path, as_of=date(2026, 10, 18),
            runner=_fake_runner(b"would overwrite"),
        )
    assert target.read_bytes() == b"previous"



def test_explicit_retry_refuses_untrusted_or_symlinked_file(tmp_path):
    target = tmp_path / "fact_event_2022_01.dump"
    target.write_bytes(b"incomplete dump")
    client = FakeS3()

    def unexpected_export(*args, **kwargs):
        pytest.fail("Reuse must not invoke pg_dump")

    with pytest.raises(RuntimeError, match="custom-format header"):
        archive_partition(
            ref(), db_url="postgresql://demo@localhost/db",
            bucket="synthetic-archive-bucket", client=client,
            output_dir=tmp_path, as_of=date(2026, 10, 18),
            runner=unexpected_export, reuse_existing=True,
        )
    assert client.uploads == []
    assert target.read_bytes() == b"incomplete dump"
    target.unlink()
    target.symlink_to(tmp_path / "other.dump")
    with pytest.raises(RuntimeError, match="regular file"):
        archive_partition(
            ref(), db_url="postgresql://demo@localhost/db",
            bucket="synthetic-archive-bucket", client=client,
            output_dir=tmp_path, as_of=date(2026, 10, 18),
            runner=unexpected_export, reuse_existing=True,
        )
    assert client.uploads == []


def test_size_limit_is_checked_before_publication(tmp_path):
    from osint_research.cloud_archival.partition_archive import MAX_DEMO_BYTES

    target = tmp_path / "fact_event_2022_01.dump"

    def oversize(args, **kwargs):
        temporary = Path(args[args.index("--file") + 1])
        with temporary.open("wb") as stream:
            stream.write(b"PGDMP")
            stream.seek(MAX_DEMO_BYTES)
            stream.write(b"X")

    with pytest.raises(ValueError, match="64 MiB"):
        export_partition(
            ref(), db_url="postgresql://demo@localhost/db",
            target=target, runner=oversize,
        )
    assert list(tmp_path.iterdir()) == []
