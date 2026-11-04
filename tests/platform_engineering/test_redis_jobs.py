"""Unit tests for queue serialization, command contracts and ownership.

Actual Redis Lua execution is tested separately in the optional integration
suite; these fake Redis tests DO NOT establish crash recovery by themselves.
"""
import json
import pytest

from osint_research.platform_engineering.redis_jobs import Job, RedisJobQueue


class FakeRedis:
    def __init__(self, scripted=None):
        self.calls = []
        self.scripted = iter(scripted or [])
        self.set_result = True
    def eval(self, script, numkeys, *args):
        self.calls.append(("eval", script, numkeys, args))
        return next(self.scripted)
    def set(self, *args, **kwargs):
        self.calls.append(("set", args, kwargs))
        return self.set_result


def sample():
    return Job("job-1", "refresh", {"company": "00000001"}, max_retries=2)


def test_serialization_roundtrip_and_validation():
    assert Job.deserialize(sample().serialize()) == sample()
    with pytest.raises(ValueError):
        Job("", "refresh", {})


def test_enqueue_uses_one_atomic_script_and_dedup_key():
    r = FakeRedis([1, 0])
    q = RedisJobQueue(r)
    assert q.enqueue(sample()) is True
    assert q.enqueue(sample()) is False
    assert r.calls[0][2] == 2
    assert "dedup:job-1" in r.calls[0][3][1]
    assert "LPUSH" in r.calls[0][1]


def test_claim_ack_and_owner_verification():
    raw = sample().serialize()
    r = FakeRedis([raw, 1])
    q = RedisJobQueue(r, clock=lambda: 100, lease_seconds=30)
    claim = q.claim()
    assert claim and claim.job == sample()
    assert claim.deadline == 130
    assert q.acknowledge(claim)
    claim_script, claim_keys, claim_args = r.calls[0][1], r.calls[0][3][:6], r.calls[0][3][6:]
    assert r.calls[0][2] == 6
    assert claim_keys[-2:] == (
        q.k("processing_deadlines"), q.k("dead_letter")
    )
    assert claim_args == (claim.token, claim.deadline, 100)
    assert "RPOPLPUSH" not in claim_script
    assert "pcall(cjson.decode, raw)" in claim_script
    assert claim_script.index("LINDEX', KEYS[1], -1") < claim_script.index("RPOP', KEYS[1]")
    assert claim_script.index("LPUSH', KEYS[6]") < claim_script.index("RPOP', KEYS[1]")
    assert claim_script.index("HEXISTS', KEYS[3]") < claim_script.index("HSET', KEYS[3]")
    assert claim_script.index("RPOP', KEYS[1]") < claim_script.index("LPUSH', KEYS[2]")
    assert claim_script.index("LPUSH', KEYS[2]") < claim_script.index("ZADD', KEYS[5]")
    ack_script, ack_keys = r.calls[1][1], r.calls[1][3][:5]
    assert r.calls[1][2] == 5
    assert ack_keys[-1] == q.k("processing_deadlines")
    assert "HGET" in ack_script and "LREM" in ack_script
    assert ack_script.index("LREM") < ack_script.index("ZREM', KEYS[5]")


def test_failure_stores_durable_retry_schedule():
    r = FakeRedis([sample().serialize(), 1])
    q = RedisJobQueue(r, clock=lambda: 100)
    claim = q.claim()
    assert q.fail(claim, "temporary")
    args = r.calls[1][3][r.calls[1][2]:]  # exclude KEYS; only Lua ARGV
    assert args[3] == "retry"
    assert args[4] == 102
    assert Job.deserialize(args[5]).retry_count == 1
    assert r.calls[1][2] == 6
    assert r.calls[1][3][5] == q.k("processing_deadlines")
    assert "ZADD" in r.calls[1][1] and "ZREM', KEYS[6]" in r.calls[1][1]
    assert "ARGV[2] .. ':' .. ARGV[6]" in r.calls[1][1]  # token-scoped retry


def test_failure_lease_guard_precedes_all_redis_mutation():
    """Fake client verifies the Lua contract; live integration tests execute it."""
    r = FakeRedis([sample().serialize(), 1])
    q = RedisJobQueue(r, clock=lambda: 100, lease_seconds=10)
    claim = q.claim()
    assert q.fail(claim, "transient")
    script = r.calls[1][1]
    args = r.calls[1][3][r.calls[1][2]:]  # actual Lua ARGV
    assert "HGET', KEYS[2]" in script  # Ownership
    assert "HGET', KEYS[3]" in script  # Lease
    assert "tonumber(ARGV[3])" in script  # Current time
    assert script.index("HGET', KEYS[3]") < script.index("LREM")
    assert script.index("LREM") < script.index("ZREM', KEYS[6]")
    assert args[2] == 100  # Same time used to enforce the deadline.
    assert args[4] == 102  # Same time used to schedule the retry.


def test_failed_handler_surfaces_rejected_failure_transition():
    r = FakeRedis([0, 0, sample().serialize(), 0])
    q = RedisJobQueue(r)

    def fail_handler(job):
        raise ValueError("synthetic handler failure")

    with pytest.raises(RuntimeError, match="Worker lost its claim") as caught:
        q.process_once({"refresh": fail_handler})
    assert isinstance(caught.value.__cause__, ValueError)


def test_permanent_failure_after_retry_limit():
    exhausted = Job("job-1", "refresh", {}, retry_count=2, max_retries=2)
    r = FakeRedis([exhausted.serialize(), 1])
    q = RedisJobQueue(r, clock=lambda: 100)
    assert q.fail(q.claim(), "terminal")
    assert r.calls[1][3][r.calls[1][2] + 3] == "failed"


def test_recovery_and_retry_promotion_use_atomic_scripts():
    r = FakeRedis([2, 1])
    q = RedisJobQueue(r, clock=lambda: 200)
    assert q.promote_due() == 2
    assert q.recover_expired() == 1
    assert "ZRANGEBYSCORE" in r.calls[0][1]
    assert r.calls[0][2] == 3
    assert r.calls[0][3][:3] == (
        q.k("retry"), q.k("pending"), q.k("dead_letter")
    )
    due_script = r.calls[0][1]
    assert "string.sub(member, 1, 1) ~= '{'" in due_script  # legacy JSON
    assert "string.sub(member, sep + 1)" in due_script  # token prefix
    assert "pcall(cjson.decode, raw)" in due_script
    assert due_script.index("LPUSH', KEYS[3]") < due_script.index("ZREM', KEYS[1], member")
    assert due_script.index("ZREM', KEYS[1], member") < due_script.index("LPUSH', KEYS[2], raw")
    assert "return promoted" in due_script
    recover_script, recover_keys = r.calls[1][1], r.calls[1][3][:5]
    assert r.calls[1][2] == 5
    assert recover_keys[-1] == q.k("processing_deadlines")
    assert "ZRANGEBYSCORE" in recover_script
    assert "LRANGE" not in recover_script  # No fixed-prefix starvation
    assert "ZREM" in recover_script
    assert r.calls[1][3][5:] == (200, 100)


def test_distributed_lock_uses_compare_and_delete():
    r = FakeRedis([1])
    q = RedisJobQueue(r)
    with q.lock("company-1") as acquired:
        assert acquired
    assert "GET" in r.calls[-1][1] and "DEL" in r.calls[-1][1]


def test_not_owner_never_releases_lock():
    r = FakeRedis()
    r.set_result = False
    q = RedisJobQueue(r)
    with q.lock("company-1") as acquired:
        assert not acquired
    assert len(r.calls) == 1


def test_process_once_requires_idempotent_handler():
    r = FakeRedis([0, 0, sample().serialize(), 1])
    q = RedisJobQueue(r)
    processed = []
    assert q.process_once({"refresh": lambda job: processed.append(job.job_id)})
    assert processed == ["job-1"]



def test_recovery_limit_selects_due_claims_not_list_prefix():
    r = FakeRedis([3])
    q = RedisJobQueue(r, clock=lambda: 400)
    assert q.recover_expired(scan_limit=3) == 3
    _, script, nkeys, args = r.calls[0]
    assert nkeys == 5
    assert args[:nkeys] == (
        q.k("processing"), q.k("owners"), q.k("deadlines"),
        q.k("pending"), q.k("processing_deadlines"),
    )
    assert args[nkeys:] == (400, 3)
    assert "ZRANGEBYSCORE" in script and "'LIMIT', 0, ARGV[2]" in script
    assert "LRANGE" not in script
    assert "LPUSH', KEYS[4]" in script
    assert "ZREM', KEYS[5]" in script
    with pytest.raises(ValueError, match="scan_limit"):
        q.recover_expired(scan_limit=0)



def test_claim_skips_active_ids_with_bounded_atomic_scan():
    r = FakeRedis([sample().serialize(), None])
    q = RedisJobQueue(r, clock=lambda: 10)
    assert q.claim(scan_limit=7).job == sample()
    assert q.claim(scan_limit=1) is None
    _, script, nkeys, args = r.calls[0]
    assert nkeys == 6
    assert args[:nkeys][-1] == q.k("dead_letter")
    assert args[nkeys + 2] == 7
    assert "math.min(redis.call('LLEN', KEYS[1]), tonumber(ARGV[3]))" in script
    assert "HEXISTS', KEYS[3]" in script
    assert "LPUSH', KEYS[1], raw" in script
    assert "return false" in script
    with pytest.raises(ValueError, match="scan_limit"):
        q.claim(scan_limit=0)



def test_dead_letter_contract_preserves_source_raw_and_bounded_progress():
    """Lua contract assertions; real Redis scenarios are in service tests."""
    r = FakeRedis([None, 2])
    q = RedisJobQueue(r)
    assert q.claim(scan_limit=4) is None
    assert q.promote_due(limit=3) == 2
    _, claim_script, claim_count, claim_args = r.calls[0]
    assert claim_count == 6
    assert claim_args[-1] == 4
    assert "source='pending'" in claim_script
    assert "reason='invalid_job'" in claim_script
    assert "raw=raw" in claim_script
    assert "decode_job(raw)" in claim_script
    assert "RPOP', KEYS[1]" in claim_script

    _, due_script, due_count, due_args = r.calls[1]
    assert due_count == 3
    assert due_args[-1] == 3
    assert "source='retry'" in due_script
    assert "raw=member" in due_script
    assert "decode_job(raw)" in due_script
    assert "valid_member = false" in due_script
