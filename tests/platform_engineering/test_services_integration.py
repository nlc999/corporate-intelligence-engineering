"""Opt-in integration tests running REAL Redis Lua scripts and PostgreSQL SQL.

Provide OSINT_TEST_REDIS_URL and/or OSINT_TEST_PG_DSN pointing ONLY at a
disposable local test instance. Absent environment values => skipped.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from osint_research.platform_engineering.pg_jobs import PostgresJobs
from osint_research.platform_engineering.redis_jobs import Job, RedisJobQueue
from osint_research.platform_engineering.two_tier_cache import RedisBackend, TwoTierCache


def test_redis_queue_claim_ack_recovery_and_lock():
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional live Redis testing")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    q = RedisJobQueue(client, namespace=ns, clock=lambda: 1000, lease_seconds=10)
    try:
        job = Job(uuid4().hex, "refresh", {"company": "invented"})
        assert q.enqueue(job)
        assert not q.enqueue(job)
        claim = q.claim()
        assert claim and claim.job == job
        assert client.zcard(q.k("processing_deadlines")) == 1
        assert q.recover_expired() == 0
        assert q.acknowledge(claim)
        assert client.zcard(q.k("processing_deadlines")) == 0
        assert q.claim() is None
        with q.lock("company-1") as owned:
            assert owned
            with q.lock("company-1") as second:
                assert not second
        c = TwoTierCache(ns, redis_backend=RedisBackend(client))
        c.set("financials", {"revenue": 10})
        c.l1.clear()
        assert c.get("financials") == {"revenue": 10}
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_redis_expired_claim_redelivery():
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("No optional live Redis")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    now = [1000.0]
    q = RedisJobQueue(client, namespace=ns, clock=lambda: now[0], lease_seconds=10)
    try:
        j = Job(uuid4().hex, "refresh", {})
        assert q.enqueue(j)
        first = q.claim()
        assert client.zcard(q.k("processing_deadlines")) == 1
        now[0] = 1011
        assert q.recover_expired() == 1
        assert client.zcard(q.k("processing_deadlines")) == 0
        second = q.claim()
        assert second.job == j
        assert first.token != second.token
        assert client.zcard(q.k("processing_deadlines")) == 1
        assert not q.acknowledge(first)
        assert q.acknowledge(second)
        assert client.zcard(q.k("processing_deadlines")) == 0
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


@pytest.mark.parametrize("max_retries", [0, 2])
def test_redis_expired_worker_cannot_fail_before_recovery(max_retries):
    """Execute real Lua: stale failure must not schedule retries or terminal state."""
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional live Redis testing")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    now = [1000.0]
    queue = RedisJobQueue(
        client, namespace=ns, clock=lambda: now[0], lease_seconds=10,
    )
    try:
        job = Job(uuid4().hex, "refresh", {}, max_retries=max_retries)
        assert queue.enqueue(job)
        stale = queue.claim()
        assert stale is not None

        # Expiry is inclusive: the lease is no longer held at its deadline.
        now[0] = stale.deadline
        assert queue.fail(stale, "stale failure") is False
        assert queue.acknowledge(stale) is False
        assert client.lrange(queue.k("processing"), 0, -1) == [stale.raw]
        assert client.hget(queue.k("owners"), job.job_id) == stale.token
        assert client.zcard(queue.k("retry")) == 0
        assert client.get(queue.k(f"failed:{job.job_id}")) is None
        assert client.zcard(queue.k("processing_deadlines")) == 1

        # The rejected transition left processing unchanged for recovery.
        assert queue.recover_expired() == 1
        assert client.zcard(queue.k("processing_deadlines")) == 0
        recovered = queue.claim()
        assert recovered is not None
        assert recovered.job == job
        assert recovered.token != stale.token
        assert queue.fail(stale, "former owner") is False

        # A new, unexpired owner can fail legitimately in both modes.
        assert queue.fail(recovered, "current owner") is True
        assert client.zcard(queue.k("processing_deadlines")) == 0
        if max_retries:
            assert client.zcard(queue.k("retry")) == 1
            assert client.get(queue.k(f"failed:{job.job_id}")) is None
        else:
            assert client.zcard(queue.k("retry")) == 0
            assert client.get(queue.k(f"failed:{job.job_id}")) == "current owner"
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_redis_recovery_does_not_starve_old_expired_claim_beyond_scan_limit():
    """Oldest claim is at the end of processing, behind 105 healthy claims."""
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional real Redis tests")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    now = [1000.0]
    queue = RedisJobQueue(client, namespace=ns, clock=lambda: now[0], lease_seconds=10)
    try:
        oldest = Job(uuid4().hex, "refresh", {"position": "oldest"})
        assert queue.enqueue(oldest)
        stale = queue.claim()
        assert stale and stale.job == oldest

        # RPOPLPUSH prepends new claims: the old one is now past the old
        # LRANGE 0..4 window, while all 105 newer leases are still live.
        now[0] = 1005.0
        active = []
        for i in range(105):
            job = Job(uuid4().hex, "refresh", {"position": i})
            assert queue.enqueue(job)
            claim = queue.claim()
            assert claim is not None
            active.append(claim)
        assert client.lindex(queue.k("processing"), -1) == stale.raw
        assert client.zcard(queue.k("processing_deadlines")) == 106

        now[0] = stale.deadline  # Expiry is inclusive, newer leases still live.
        assert queue.recover_expired(scan_limit=5) == 1
        assert client.zcard(queue.k("processing_deadlines")) == 105
        assert client.llen(queue.k("processing")) == 105
        assert client.llen(queue.k("pending")) == 1
        assert client.hget(queue.k("owners"), stale.job.job_id) is None
        assert not queue.acknowledge(stale)
        assert queue.recover_expired(scan_limit=5) == 0

        redelivered = queue.claim()
        assert redelivered and redelivered.job == oldest
        assert redelivered.token != stale.token
        assert queue.acknowledge(redelivered)
        assert client.zcard(queue.k("processing_deadlines")) == 105
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_redis_recovery_drains_due_claims_in_bounded_batches():
    """Repeated small recovery batches must advance through all due claims."""
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional real Redis tests")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    now = [1000.0]
    queue = RedisJobQueue(client, namespace=ns, clock=lambda: now[0], lease_seconds=10)
    try:
        for i in range(5):
            assert queue.enqueue(Job(uuid4().hex, "refresh", {"i": i}))
            assert queue.claim() is not None
        assert client.zcard(queue.k("processing_deadlines")) == 5
        now[0] = 1011.0
        assert [queue.recover_expired(scan_limit=2) for _ in range(4)] == [
            2, 2, 1, 0,
        ]
        assert client.zcard(queue.k("processing_deadlines")) == 0
        assert client.llen(queue.k("processing")) == 0
        assert client.llen(queue.k("pending")) == 5
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_redis_active_duplicate_id_never_overwrites_claim_owner():
    """Expired enqueue dedup must not replace an active claim's owner fields."""
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional real Redis tests")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    now = [1000.0]
    queue = RedisJobQueue(client, namespace=ns, clock=lambda: now[0], lease_seconds=10)
    try:
        original = Job(uuid4().hex, "refresh", {"generation": "first"})
        assert queue.enqueue(original)
        first = queue.claim()
        assert first and first.job == original

        # Simulate dedup expiry while the original lease remains active.
        client.delete(queue.k(f"dedup:{original.job_id}"))
        newer = Job(original.job_id, "refresh", {"generation": "second"})
        assert queue.enqueue(newer)
        other = Job(uuid4().hex, "refresh", {"generation": "other"})
        assert queue.enqueue(other)

        # The oldest pending item is the duplicate. Skip it atomically.
        unrelated = queue.claim(scan_limit=3)
        assert unrelated and unrelated.job == other
        assert client.hget(queue.k("owners"), first.job.job_id) == first.token
        assert float(client.hget(queue.k("deadlines"), first.job.job_id)) == pytest.approx(first.deadline)
        assert client.zcard(queue.k("processing_deadlines")) == 2
        assert client.llen(queue.k("processing")) == 2
        assert queue.claim(scan_limit=1) is None
        assert client.llen(queue.k("pending")) == 1

        assert queue.acknowledge(first)
        assert client.zcard(queue.k("processing_deadlines")) == 1
        second = queue.claim()
        assert second and second.job == newer and second.token != first.token
        assert client.zcard(queue.k("processing_deadlines")) == 2
        assert queue.acknowledge(second)
        assert queue.acknowledge(unrelated)
        assert client.zcard(queue.k("processing_deadlines")) == 0
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_redis_expired_owner_requires_recovery_before_duplicate_claim():
    """Expired ownership is reserved until the deadline-indexed recovery."""
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional real Redis tests")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    now = [1000.0]
    queue = RedisJobQueue(client, namespace=ns, clock=lambda: now[0], lease_seconds=10)
    try:
        original = Job(uuid4().hex, "refresh", {"n": 1})
        assert queue.enqueue(original)
        first = queue.claim()
        assert first
        client.delete(queue.k(f"dedup:{original.job_id}"))
        newer = Job(original.job_id, "refresh", {"n": 2})
        assert queue.enqueue(newer)
        now[0] = 1011.0
        assert queue.claim(scan_limit=2) is None
        assert queue.recover_expired() == 1
        # Newer pending record stays eligible; both versions can eventually
        # run, but they cannot own the same job ID simultaneously.
        second = queue.claim(scan_limit=2)
        assert second
        assert client.hget(queue.k("owners"), original.job_id) == second.token
        assert not queue.acknowledge(first)
        assert queue.acknowledge(second)
        final = queue.claim()
        assert final and final.job.job_id == original.job_id
        assert queue.acknowledge(final)
        assert client.llen(queue.k("processing")) == 0
        assert client.zcard(queue.k("processing_deadlines")) == 0
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_redis_identical_retry_jobs_keep_distinct_scheduled_attempts():
    """Two accepted attempts with identical JSON must not collapse in ZADD."""
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional real Redis tests")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    now = [1000.0]
    queue = RedisJobQueue(client, namespace=ns, clock=lambda: now[0], lease_seconds=10)
    try:
        job = Job(uuid4().hex, "refresh", {"same": "payload"})
        assert queue.enqueue(job)
        first = queue.claim()
        assert first and queue.fail(first, "retry")

        # Both attempts are accepted sequentially after enqueue dedup expires;
        # their retry_count and JSON payload are deliberately identical.
        client.delete(queue.k(f"dedup:{job.job_id}"))
        assert queue.enqueue(job)
        second = queue.claim()
        assert second and second.token != first.token
        assert queue.fail(second, "retry")
        members = client.zrange(queue.k("retry"), 0, -1)
        assert len(members) == len(set(members)) == 2
        assert {member.split(":", 1)[0] for member in members} == {
            first.token, second.token,
        }
        assert all(Job.deserialize(member.split(":", 1)[1]).retry_count == 1
                   for member in members)

        now[0] = 1003.0
        assert queue.promote_due(limit=1) == 1
        assert queue.promote_due(limit=1) == 1
        assert queue.promote_due(limit=1) == 0
        assert client.llen(queue.k("pending")) == 2
        claimed = queue.claim()
        assert claimed and claimed.job.retry_count == 1
        assert queue.claim(scan_limit=2) is None  # second copy cannot overlap
        assert queue.acknowledge(claimed)
        other = queue.claim()
        assert other and other.job == claimed.job
        assert queue.acknowledge(other)
        assert client.llen(queue.k("pending")) == 0
        assert client.zcard(queue.k("retry")) == 0
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_redis_due_promotion_accepts_legacy_bare_json_members():
    """Previously scheduled bare-JSON retries still promote after upgrade."""
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional real Redis tests")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    queue = RedisJobQueue(client, namespace=ns, clock=lambda: 1001.0)
    try:
        legacy = Job(uuid4().hex, "refresh", {"legacy": True}, retry_count=1)
        client.zadd(queue.k("retry"), {legacy.serialize(): 1000.0})
        assert queue.promote_due() == 1
        claim = queue.claim()
        assert claim and claim.job == legacy
        assert queue.acknowledge(claim)
        assert client.zcard(queue.k("retry")) == 0
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


@pytest.mark.parametrize("bad_raw", [
    "not-json",
    '{"job_id":"broken"}',
    '{"job_id":"bad","kind":"refresh","payload":["not-a-map"],"retry_count":0,"max_retries":1}',
    '{"job_id":"bad","kind":"refresh","payload":{},"retry_count":"x","max_retries":1}',
])
def test_redis_pending_quarantines_invalid_jobs_before_claim(bad_raw):
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional real Redis tests")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0, socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    queue = RedisJobQueue(client, namespace=ns, clock=lambda: 1000.0)
    try:
        client.lpush(queue.k("pending"), bad_raw)
        valid = Job(uuid4().hex, "refresh", {"safe": True})
        assert queue.enqueue(valid)
        claim = queue.claim(scan_limit=2)
        assert claim and claim.job == valid
        assert client.llen(queue.k("pending")) == 0
        quarantine = client.lrange(queue.k("dead_letter"), 0, -1)
        assert len(quarantine) == 1
        assert json.loads(quarantine[0]) == {
            "source": "pending", "reason": "invalid_job", "raw": bad_raw,
        }
        assert client.hlen(queue.k("owners")) == 1
        assert queue.acknowledge(claim)
        assert client.hlen(queue.k("owners")) == 0
        assert client.llen(queue.k("processing")) == 0
        assert queue.claim() is None
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_redis_due_mixed_formats_quarantine_bad_members_and_preserve_good():
    url = os.getenv("OSINT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set OSINT_TEST_REDIS_URL for optional real Redis tests")
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=2.0, socket_connect_timeout=2.0,
    )
    ns = "test_" + uuid4().hex[:12]
    queue = RedisJobQueue(client, namespace=ns, clock=lambda: 1000.0)
    try:
        old = Job(uuid4().hex, "refresh", {"legacy": True}, retry_count=1)
        new = Job(uuid4().hex, "refresh", {"token": True}, retry_count=1)
        bad = [
            "not-a-token-or-json",
            "a" * 32 + ":not-json",
            '{"job_id":"bad","kind":"refresh","payload":null,"retry_count":1,"max_retries":3}',
        ]
        client.zadd(queue.k("retry"), {
            bad[0]: 900.0, bad[1]: 901.0, bad[2]: 902.0,
            old.serialize(): 903.0,
            ("b" * 32 + ":" + new.serialize()): 904.0,
        })
        # A short pass quarantines the first invalid member but cannot hide
        # the healthy members from subsequent bounded promotion calls.
        assert queue.promote_due(limit=1) == 0
        assert client.zcard(queue.k("retry")) == 4
        assert queue.promote_due(limit=10) == 2
        assert client.zcard(queue.k("retry")) == 0
        quarantine = [json.loads(x) for x in client.lrange(queue.k("dead_letter"), 0, -1)]
        assert len(quarantine) == 3
        assert {x["raw"] for x in quarantine} == set(bad)
        assert {x["source"] for x in quarantine} == {"retry"}
        assert all(x["reason"] == "invalid_job" for x in quarantine)
        assert client.llen(queue.k("pending")) == 2
        claimed = []
        for _ in range(2):
            claim = queue.claim()
            assert claim
            claimed.append(claim.job.job_id)
            assert queue.acknowledge(claim)
        assert set(claimed) == {old.job_id, new.job_id}
        assert queue.claim() is None
    finally:
        for key in client.scan_iter(match=f"{ns}:*"):
            client.delete(key)


def test_pg_concurrent_registration():
    dsn = os.getenv("OSINT_TEST_PG_DSN")
    if not dsn:
        pytest.skip("Set OSINT_TEST_PG_DSN for optional live PostgreSQL testing")
    psycopg = pytest.importorskip("psycopg")
    def connect():
        return psycopg.connect(dsn, autocommit=False)

    company = "SYN" + uuid4().hex[:10].upper()
    jobs = PostgresJobs(connect)
    dispatched = []
    def request(_):
        return jobs.submit(company, ("fetch", "parse"), lambda rid, name: dispatched.append(rid))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request, range(4)))
    assert sum(x.started for x in results) == 1
    assert len({x.run_id for x in results}) == 1
    assert dispatched == [results[0].run_id]
    assert jobs.finish(results[0].run_id, "COMPLETED") is False  # PENDING
    assert jobs.complete_stage(results[0].run_id, "fetch") is False
    assert jobs.begin(results[0].run_id)
    assert jobs.finish(results[0].run_id, "COMPLETED") is False  # No stages complete
    assert jobs.complete_stage(results[0].run_id, "fetch")
    assert jobs.finish(results[0].run_id, "COMPLETED") is False  # parse still pending
    assert jobs.complete_stage(results[0].run_id, "parse")
    assert jobs.finish(results[0].run_id, "COMPLETED")
    assert jobs.complete_stage(results[0].run_id, "fetch") is False
    assert jobs.finish(results[0].run_id, "FAILED") is False
    assert [t["status"] for t in jobs.status(results[0].run_id)["tasks"]] == [
        "COMPLETED", "COMPLETED",
    ]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM osint_demo_jobs.tasks WHERE run_id=%s::uuid", (results[0].run_id,))
            cur.execute("DELETE FROM osint_demo_jobs.runs WHERE run_id=%s::uuid", (results[0].run_id,))
    # Both synthetic task and run records have been removed.


def test_pg_terminal_failure_blocks_late_stages():
    """Check failed PENDING and RUNNING runs against a real disposable database."""
    dsn = os.getenv("OSINT_TEST_PG_DSN")
    if not dsn:
        pytest.skip("Set OSINT_TEST_PG_DSN for optional live PostgreSQL testing")
    psycopg = pytest.importorskip("psycopg")

    def connect():
        return psycopg.connect(dsn, autocommit=False)

    jobs = PostgresJobs(connect)
    run_ids = []
    try:
        for begin_first in (False, True):
            company = "SYN" + uuid4().hex[:10].upper()
            submitted = jobs.submit(company, ("fetch", "parse"), lambda *_: None)
            run_ids.append(submitted.run_id)
            if begin_first:
                assert jobs.begin(submitted.run_id)
                assert jobs.complete_stage(submitted.run_id, "fetch")
            assert jobs.finish(submitted.run_id, "FAILED", error="synthetic failure")
            assert jobs.complete_stage(submitted.run_id, "parse") is False
            assert jobs.finish(submitted.run_id, "COMPLETED") is False
            assert jobs.status(submitted.run_id)["status"] == "FAILED"
    finally:
        if run_ids:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM osint_demo_jobs.tasks WHERE run_id = ANY(%s::uuid[])",
                        (run_ids,),
                    )
                    cur.execute(
                        "DELETE FROM osint_demo_jobs.runs WHERE run_id = ANY(%s::uuid[])",
                        (run_ids,),
                    )


def test_pg_last_stage_and_finish_race_serializes_on_run_row():
    """Finalise or finish the last stage first; either order stays consistent."""
    dsn = os.getenv("OSINT_TEST_PG_DSN")
    if not dsn:
        pytest.skip("Set OSINT_TEST_PG_DSN for optional live PostgreSQL testing")
    psycopg = pytest.importorskip("psycopg")

    def connect():
        return psycopg.connect(dsn, autocommit=False)

    jobs = PostgresJobs(connect)
    company = "SYN" + uuid4().hex[:10].upper()
    run_id = None
    try:
        run_id = jobs.submit(company, ("fetch", "parse"), lambda *_: None).run_id
        assert jobs.begin(run_id)
        assert jobs.complete_stage(run_id, "fetch")

        # Independent connections race on the parent row. The earlier
        # transition owns the row lock through commit; finalisation must not
        # observe half-updated task state.
        with ThreadPoolExecutor(max_workers=2) as pool:
            finish_future = pool.submit(jobs.finish, run_id, "COMPLETED")
            stage_future = pool.submit(jobs.complete_stage, run_id, "parse")
            finished_first = finish_future.result()
            stage_completed = stage_future.result()
        assert stage_completed is True
        if not finished_first:
            assert jobs.finish(run_id, "COMPLETED") is True

        snapshot = jobs.status(run_id)
        assert snapshot["status"] == "COMPLETED"
        assert [t["status"] for t in snapshot["tasks"]] == ["COMPLETED", "COMPLETED"]
        assert jobs.complete_stage(run_id, "fetch") is False
    finally:
        if run_id is not None:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM osint_demo_jobs.tasks WHERE run_id=%s::uuid",
                        (run_id,),
                    )
                    cur.execute(
                        "DELETE FROM osint_demo_jobs.runs WHERE run_id=%s::uuid",
                        (run_id,),
                    )
