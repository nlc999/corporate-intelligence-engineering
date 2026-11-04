"""Historical Redis job/lock concepts adapted with atomic transitions.

This showcase uses at-least-once delivery, never promises exactly-once work.
Redis lists preserve FIFO; Lua scripts atomically transfer, claim, acknowledge,
and schedule retries. It is a demonstrator, not a production queue library.
"""
from __future__ import annotations

import json
import secrets
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    kind: str
    payload: dict[str, Any]
    retry_count: int = 0
    max_retries: int = 3

    def __post_init__(self):
        if not self.job_id or not self.kind:
            raise ValueError("Job identity and kind are required")
        if self.retry_count < 0 or self.max_retries < 0:
            raise ValueError("Retry counts cannot be negative")

    def serialize(self) -> str:
        return json.dumps({
            "job_id": self.job_id, "kind": self.kind,
            "payload": self.payload, "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }, sort_keys=True, separators=(",", ":"))

    @classmethod
    def deserialize(cls, raw: str) -> "Job":
        return cls(**json.loads(raw))


@dataclass(frozen=True, slots=True)
class Claim:
    job: Job
    raw: str
    token: str
    deadline: float


# A successful enqueue creates a bounded deduplication marker and inserts into
# the pending list in the *same Redis operation*. LPUSH/RPOP is FIFO.
_ENQUEUE = """
if redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[2]) then
  redis.call('LPUSH', KEYS[1], ARGV[1])
  return 1
end
return 0
"""

# Protect pending and scheduled JSON before an irreversible move.
# Both Lua scripts include this validation fragment independently.
_VALIDATE_JOB_LUA = """
local function decode_job(raw)
  local ok, job = pcall(cjson.decode, raw)
  if not ok or type(job) ~= 'table' then return nil end
  if type(job.job_id) ~= 'string' or job.job_id == '' then return nil end
  if type(job.kind) ~= 'string' or job.kind == '' then return nil end
  if type(job.payload) ~= 'table' then return nil end
  for key, _ in pairs(job.payload) do
    if type(key) ~= 'string' then return nil end
  end
  if type(job.retry_count) ~= 'number' or job.retry_count < 0
     or job.retry_count % 1 ~= 0 then return nil end
  if type(job.max_retries) ~= 'number' or job.max_retries < 0
     or job.max_retries % 1 ~= 0 then return nil end
  for key, _ in pairs(job) do
    if key ~= 'job_id' and key ~= 'kind' and key ~= 'payload'
       and key ~= 'retry_count' and key ~= 'max_retries' then return nil end
  end
  return job
end
"""

# Validate the oldest pending entry before removing it; quarantine malformed
# input in the same Lua execution, then continue scanning for valid work.
# A live job ID remains reserved even when the enqueue deduplication key expires.
_CLAIM = _VALIDATE_JOB_LUA + """
local attempts = math.min(redis.call('LLEN', KEYS[1]), tonumber(ARGV[3]))
for i = 1, attempts do
  local raw = redis.call('LINDEX', KEYS[1], -1)
  if not raw then break end
  local j = decode_job(raw)
  if not j then
    -- Write quarantine first; RPOP only follows a successful dead-letter push.
    redis.call('LPUSH', KEYS[6],
               cjson.encode({source='pending', reason='invalid_job', raw=raw}))
    redis.call('RPOP', KEYS[1])
  elseif redis.call('HEXISTS', KEYS[3], j.job_id) == 1 then
    -- Defer active IDs without blocking unrelated pending jobs.
    redis.call('RPOP', KEYS[1])
    redis.call('LPUSH', KEYS[1], raw)
  else
    redis.call('RPOP', KEYS[1])
    redis.call('LPUSH', KEYS[2], raw)
    redis.call('HSET', KEYS[3], j.job_id, ARGV[1])
    redis.call('HSET', KEYS[4], j.job_id, ARGV[2])
    redis.call('ZADD', KEYS[5], ARGV[2], ARGV[1] .. ':' .. raw)
    return raw
  end
end
return false
"""

_ACK = """
local j = cjson.decode(ARGV[1])
if redis.call('HGET', KEYS[2], j.job_id) ~= ARGV[2] then return 0 end
if tonumber(redis.call('HGET', KEYS[3], j.job_id) or '0') <= tonumber(ARGV[3])
  then return 0 end
if redis.call('LREM', KEYS[1], 1, ARGV[1]) ~= 1 then return 0 end
redis.call('HDEL', KEYS[2], j.job_id)
redis.call('HDEL', KEYS[3], j.job_id)
redis.call('ZREM', KEYS[5], ARGV[2] .. ':' .. ARGV[1])
redis.call('SET', KEYS[4], '1', 'EX', ARGV[4])
return 1
"""

# Failed jobs are moved either to a time-ordered retry schedule or terminal
# failure key, atomically. Retry timing survives process restart.
_FAIL = """
local j = cjson.decode(ARGV[1])
if redis.call('HGET', KEYS[2], j.job_id) ~= ARGV[2] then return 0 end
-- Match acknowledgement: a token alone does not authorise an expired worker.
-- Check before any mutation so recovery can safely reclaim the same job.
if tonumber(redis.call('HGET', KEYS[3], j.job_id) or '0') <= tonumber(ARGV[3])
  then return 0 end
if redis.call('LREM', KEYS[1], 1, ARGV[1]) ~= 1 then return 0 end
redis.call('HDEL', KEYS[2], j.job_id)
redis.call('HDEL', KEYS[3], j.job_id)
redis.call('ZREM', KEYS[6], ARGV[2] .. ':' .. ARGV[1])
if ARGV[4] == 'retry' then
  -- Two accepted claims can yield identical serialized retry jobs after the
  -- enqueue dedup TTL. Prefix by the unique claim token so ZADD never merges
  -- distinct retry attempts. _DUE strips the prefix before re-enqueueing.
  redis.call('ZADD', KEYS[4], ARGV[5], ARGV[2] .. ':' .. ARGV[6])
else
  redis.call('SET', KEYS[5], ARGV[7], 'EX', ARGV[8])
end
return 1
"""

# Retry entries can be legacy bare JSON or token:serialized_job.
# Validate both representations and continue after quarantining bad entries.
_DUE = _VALIDATE_JOB_LUA + """
local items = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1],
                           'LIMIT', 0, ARGV[2])
local promoted = 0
for _, member in ipairs(items) do
  local raw = member
  local valid_member = true
  if string.sub(member, 1, 1) ~= '{' then
    local sep = string.find(member, ':', 1, true)
    local token = sep and string.sub(member, 1, sep - 1) or ''
    if sep ~= 33 or not string.match(token, '^[0-9a-f]+$') then
      valid_member = false
    else
      raw = string.sub(member, sep + 1)
    end
  end
  if not valid_member or not decode_job(raw) then
    redis.call('LPUSH', KEYS[3],
               cjson.encode({source='retry', reason='invalid_job', raw=member}))
    redis.call('ZREM', KEYS[1], member)
  else
    redis.call('ZREM', KEYS[1], member)
    redis.call('LPUSH', KEYS[2], raw)
    promoted = promoted + 1
  end
end
return promoted
"""

# Iterate the *earliest expired deadlines*, not the newest processing entries.
# The index is written alongside the list by _CLAIM and cleared on every
# successful terminal transition. One script call handles at most scan_limit
# due candidates; repeated calls then make progress through the remaining due
# entries even if many newer, unexpired claims remain in processing.
_RECOVER = """
local items = redis.call('ZRANGEBYSCORE', KEYS[5], '-inf', ARGV[1],
                         'LIMIT', 0, ARGV[2])
local count = 0
for _, member in ipairs(items) do
  -- The token has no colons; serialized JSON may contain arbitrarily many.
  local sep = string.find(member, ':', 1, true)
  if not sep then
    redis.call('ZREM', KEYS[5], member)
  else
    local token = string.sub(member, 1, sep - 1)
    local raw = string.sub(member, sep + 1)
    local j = cjson.decode(raw)
    local owner = redis.call('HGET', KEYS[2], j.job_id)
    local expiry = tonumber(redis.call('HGET', KEYS[3], j.job_id) or '0')
    if owner ~= token then
      -- An obsolete entry cannot reclaim a newer holder's job.
      redis.call('ZREM', KEYS[5], member)
    elseif expiry > tonumber(ARGV[1]) then
      -- Repair a stale index score if a lease was extended externally.
      redis.call('ZADD', KEYS[5], expiry, member)
    else
      if redis.call('LREM', KEYS[1], 1, raw) == 1 then
        redis.call('HDEL', KEYS[2], j.job_id)
        redis.call('HDEL', KEYS[3], j.job_id)
        redis.call('LPUSH', KEYS[4], raw)
        count = count + 1
      end
      -- Remove dangling entries as well as successfully recovered claims.
      redis.call('ZREM', KEYS[5], member)
    end
  end
end
return count
"""

_LOCK_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisJobQueue:
    """A thin redis-py adapter. The client must use decode_responses=True."""

    def __init__(
        self, redis_client: Any, *, namespace: str = "osint_demo",
        clock: Callable[[], float] = time.time, lease_seconds: int = 30,
        result_ttl: int = 86400,
    ):
        if not namespace or ":" in namespace:
            raise ValueError("Invalid namespace")
        if lease_seconds < 1 or result_ttl < 1:
            raise ValueError("TTL must be positive")
        self.redis = redis_client
        self.ns = namespace
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.result_ttl = result_ttl

    def k(self, suffix: str) -> str:
        return f"{self.ns}:{suffix}"

    def enqueue(self, job: Job) -> bool:
        # Identical job IDs are deduplicated for the configured window.
        return bool(self.redis.eval(
            _ENQUEUE, 2, self.k("pending"), self.k(f"dedup:{job.job_id}"),
            job.serialize(), self.result_ttl,
        ))

    def promote_due(self, limit: int = 100) -> int:
        if limit < 1:
            raise ValueError("limit must be positive")
        return int(self.redis.eval(
            _DUE, 3, self.k("retry"), self.k("pending"),
            self.k("dead_letter"), self.clock(), limit,
        ))

    def claim(self, scan_limit: int = 100) -> Claim | None:
        """Skip active job IDs while preserving access to other pending work.

        When all scanned IDs remain active, return None rather than overwrite
        their owner metadata. Repeated bounded calls advance through the queue.
        """
        if scan_limit < 1:
            raise ValueError("scan_limit must be positive")
        token = secrets.token_hex(16)
        deadline = self.clock() + self.lease_seconds
        raw = self.redis.eval(
            _CLAIM, 6, self.k("pending"), self.k("processing"),
            self.k("owners"), self.k("deadlines"), self.k("processing_deadlines"),
            self.k("dead_letter"), token, deadline, scan_limit,
        )
        if not raw:
            return None
        return Claim(Job.deserialize(raw), raw, token, deadline)

    def acknowledge(self, claim: Claim) -> bool:
        return bool(self.redis.eval(
            _ACK, 5, self.k("processing"), self.k("owners"),
            self.k("deadlines"), self.k(f"completed:{claim.job.job_id}"),
            self.k("processing_deadlines"),
            claim.raw, claim.token, self.clock(), self.result_ttl,
        ))

    def fail(self, claim: Claim, reason: str) -> bool:
        count = claim.job.retry_count + 1
        retry = count <= claim.job.max_retries
        next_job = replace(claim.job, retry_count=count)
        # Exponential retry cap; scheduled in a Redis sorted set, not memory.
        delay = min(300, 2 ** min(count, 8))
        now = self.clock()
        return bool(self.redis.eval(
            _FAIL, 6, self.k("processing"), self.k("owners"),
            self.k("deadlines"), self.k("retry"),
            self.k(f"failed:{claim.job.job_id}"),
            self.k("processing_deadlines"),
            claim.raw, claim.token, now,
            "retry" if retry else "failed",
            now + delay, next_job.serialize(),
            reason[:300], self.result_ttl,
        ))

    def recover_expired(self, scan_limit: int = 100) -> int:
        """Recover up to scan_limit oldest expired indexed claims per call.

        Unlike a processing-list prefix scan, a long queue of healthy claims
        cannot hide expired claims at the back of the list.
        """
        if scan_limit < 1:
            raise ValueError("scan_limit must be positive")
        return int(self.redis.eval(
            _RECOVER, 5, self.k("processing"), self.k("owners"),
            self.k("deadlines"), self.k("pending"),
            self.k("processing_deadlines"), self.clock(), scan_limit,
        ))

    @contextmanager
    def lock(self, name: str, *, ttl: int = 60) -> Iterator[bool]:
        """Unique-token Redis lock; never release a lock owned by another worker.

        Caller must bound the critical section below TTL or implement renewal
        and fencing. An expired lease does not protect long-running writes.
        """
        if ttl < 1:
            raise ValueError("ttl must be positive")
        key = self.k(f"lock:{name}")
        token = uuid.uuid4().hex
        acquired = bool(self.redis.set(key, token, nx=True, ex=ttl))
        try:
            yield acquired
        finally:
            if acquired:
                self.redis.eval(_LOCK_RELEASE, 1, key, token)

    def process_once(self, handlers: dict[str, Callable[[Job], None]]) -> bool:
        """Process one task; handlers MUST be idempotent under redelivery."""
        self.promote_due()
        self.recover_expired()
        claim = self.claim()
        if claim is None:
            return False
        try:
            handler = handlers.get(claim.job.kind)
            if handler is None:
                raise KeyError(f"Unsupported job kind: {claim.job.kind}")
            handler(claim.job)
        except Exception as exc:
            if not self.fail(claim, type(exc).__name__):
                # A worker can lose its lease while its handler is running.
                # Do not silently report a successful failure transition.
                raise RuntimeError("Worker lost its claim; job may be redelivered") from exc
        else:
            if not self.acknowledge(claim):
                raise RuntimeError("Worker lost its claim; job may be redelivered")
        return True
