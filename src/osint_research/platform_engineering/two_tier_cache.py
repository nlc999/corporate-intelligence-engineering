"""L1 local LRU + L2 Redis with TTL and graceful degradation.

Adapted from a historical two-tier cache, with dependency injection and
per-instance L1 caches for reproducible tests. L1 and L2 are not guaranteed
to be immediately coherent across processes; see the case study.
"""
from __future__ import annotations

import json
import logging
from math import isfinite
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Iterator, Protocol

from .lru_cache import LRUCache

log = logging.getLogger(__name__)


class CacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: int) -> bool: ...
    def delete(self, key: str) -> bool: ...


class RedisBackend:
    """JSON redis-py adapter requiring bounded network socket waits.

    from_url() constructs a client with explicit timeouts. Injected redis-py
    clients must expose finite connection and socket timeouts through their
    connection pool. A socket timeout is not an overall retry deadline.
    """

    DEFAULT_SOCKET_TIMEOUT = 2.0
    MAX_SOCKET_TIMEOUT = 30.0

    @classmethod
    def _validate_timeout(cls, value: Any, name: str) -> None:
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or not 0 < value <= cls.MAX_SOCKET_TIMEOUT):
            raise ValueError(f"{name} must be finite and in (0, 30] seconds")

    @classmethod
    def from_url(
        cls, url: str, *, socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
        socket_connect_timeout: float = DEFAULT_SOCKET_TIMEOUT,
    ) -> RedisBackend:
        """Construct a redis-py client with explicit bounded socket waits."""
        cls._validate_timeout(socket_timeout, "socket_timeout")
        cls._validate_timeout(socket_connect_timeout, "socket_connect_timeout")
        import redis

        client = redis.Redis.from_url(
            url, decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            retry_on_timeout=False,
        )
        return cls(client)

    def __init__(self, client: Any):
        pool = getattr(client, "connection_pool", None)
        settings = getattr(pool, "connection_kwargs", None)
        if not isinstance(settings, dict):
            raise ValueError("RedisBackend needs a redis-py client with socket timeouts")
        self._validate_timeout(settings.get("socket_timeout"), "socket_timeout")
        self._validate_timeout(settings.get("socket_connect_timeout"), "socket_connect_timeout")
        self.client = client

    def get(self, key: str) -> Any | None:
        raw = self.client.get(key)
        return None if raw is None else json.loads(raw)

    def set(self, key: str, value: Any, ttl: int) -> bool:
        return bool(self.client.set(key, json.dumps(value), ex=ttl))

    def delete(self, key: str) -> bool:
        return bool(self.client.delete(key))


@dataclass(slots=True)
class _ActiveRead:
    """Only retained while one or more L2 reads are outstanding for a key."""
    generation: int = 0
    readers: int = 0


@dataclass(slots=True)
class _WriteLock:
    """Retained only while same-key writers are active or waiting."""
    lock: Any = field(default_factory=Lock)
    users: int = 0


class TwoTierCache:
    """Reads L1 then L2, promotes L2 hits to L1 and writes to both.

    Redis failures never prevent a functioning local cache. Exceptions are
    counted and logged without printing addresses, keys' values or secrets.
    """

    # Striped locks bound lock allocation. Each stripe tracks only keys with
    # in-flight L2 reads, so unrelated keys never invalidate one another.
    _PROMOTION_STRIPES = 64

    def __init__(
        self, namespace: str, *, redis_backend: CacheBackend | None = None,
        l1_ttl: int = 300, l2_ttl: int = 3600,
        l1_max_size: int = 500, l1: LRUCache | None = None,
    ):
        if not namespace or ":" in namespace:
            raise ValueError("Provide a nonempty namespace without ':'")
        if l1_ttl < 1 or l2_ttl < 1:
            raise ValueError("TTLs must be positive")
        self.namespace = namespace
        self.l1 = l1 if l1 is not None else LRUCache(l1_max_size, l1_ttl)
        self.l2 = redis_backend
        self.l1_ttl = l1_ttl
        self.l2_ttl = l2_ttl
        # Coordinate L1 mutations with in-flight L2 reads. These locks are
        # held only around local operations, NEVER around Redis/network I/O.
        self._promotion_locks = tuple(Lock() for _ in range(self._PROMOTION_STRIPES))
        self._active_reads: tuple[dict[str, _ActiveRead], ...] = tuple(
            {} for _ in range(self._PROMOTION_STRIPES)
        )
        # A write-through operation keeps its per-key lock across L2 I/O.
        # Waiter reference counts prevent removal/recreation while contended.
        self._write_guard = Lock()
        self._write_locks: dict[str, _WriteLock] = {}
        # A cache miss must not read stale L2 data during an active deletion
        # or after a local set has expired before its remote write finishes.
        self._pending_writes: tuple[set[str], ...] = tuple(
            set() for _ in range(self._PROMOTION_STRIPES)
        )
        # Separate lock for adapter telemetry; avoid cross-lock nesting.
        self._stats_lock = Lock()
        self._l1_hits = 0
        self._l2_hits = 0
        self._misses = 0
        self._redis_errors = 0

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def _promotion_slot(self, cache_key: str) -> int:
        return hash(cache_key) % self._PROMOTION_STRIPES

    @contextmanager
    def _ordered_write(self, cache_key: str) -> Iterator[None]:
        """Serialize same-key write-throughs; reclaim locks after last waiter."""
        with self._write_guard:
            entry = self._write_locks.get(cache_key)
            if entry is None:
                entry = _WriteLock()
                self._write_locks[cache_key] = entry
            entry.users += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._write_guard:
                entry.users -= 1
                if entry.users == 0:
                    del self._write_locks[cache_key]

    def _remote(self, fn: str, *args: Any) -> Any | None:
        if self.l2 is None:
            return None
        try:
            return getattr(self.l2, fn)(*args)
        except Exception as exc:  # Redis must be optional, not a read dependency
            with self._stats_lock:
                self._redis_errors += 1
            log.warning("Shared cache %s unavailable (%s)", fn, type(exc).__name__)
            return None

    def get(self, key: str) -> Any | None:
        cache_key = self._key(key)
        slot = self._promotion_slot(cache_key)
        lock = self._promotion_locks[slot]
        with lock:
            local = self.l1.get(cache_key)
            pending = local is None and cache_key in self._pending_writes[slot]
            if local is None and not pending:
                reads = self._active_reads[slot]
                state = reads.setdefault(cache_key, _ActiveRead())
                generation = state.generation
                state.readers += 1
        if local is not None:
            with self._stats_lock:
                self._l1_hits += 1
            return local
        if pending:
            # A deletion may have removed L1 while Redis still holds the old
            # value. Avoid promoting that value during the write-through.
            with self._stats_lock:
                self._misses += 1
            return None

        try:
            # This I/O never holds a promotion lock. Intervening mutations
            # increment only this key's in-flight generation.
            remote = self._remote("get", cache_key)
            with lock:
                if state.generation != generation:
                    # Return the new L1 value if present, or a miss after
                    # delete/expiry. Never serve or promote the stale read.
                    result = self.l1.get(cache_key)
                    source = "l1" if result is not None else "miss"
                elif remote is not None:
                    self.l1.set(cache_key, remote, ttl=self.l1_ttl)
                    state.generation += 1  # Invalidate older concurrent reads.
                    result, source = remote, "l2"
                else:
                    result, source = None, "miss"
        finally:
            with lock:
                state.readers -= 1
                if state.readers == 0:
                    del self._active_reads[slot][cache_key]

        with self._stats_lock:
            if source == "l1":
                self._l1_hits += 1
            elif source == "l2":
                self._l2_hits += 1
            else:
                self._misses += 1
        return result

    def set(self, key: str, value: Any) -> bool:
        cache_key = self._key(key)
        slot = self._promotion_slot(cache_key)
        lock = self._promotion_locks[slot]
        with self._ordered_write(cache_key):
            with lock:
                self.l1.set(cache_key, value, ttl=self.l1_ttl)
                self._pending_writes[slot].add(cache_key)
                state = self._active_reads[slot].get(cache_key)
                if state is not None:
                    state.generation += 1
            try:
                # Only another write to THIS key is blocked during Redis I/O.
                self._remote("set", cache_key, value, self.l2_ttl)
            finally:
                with lock:
                    self._pending_writes[slot].discard(cache_key)
        return True  # Local set succeeded regardless of Redis state

    def delete(self, key: str) -> bool:
        cache_key = self._key(key)
        slot = self._promotion_slot(cache_key)
        lock = self._promotion_locks[slot]
        with self._ordered_write(cache_key):
            with lock:
                local = self.l1.delete(cache_key)
                self._pending_writes[slot].add(cache_key)
                state = self._active_reads[slot].get(cache_key)
                if state is not None:
                    state.generation += 1  # Even a miss invalidates stale reads.
            try:
                remote = self._remote("delete", cache_key)
            finally:
                with lock:
                    self._pending_writes[slot].discard(cache_key)
        return bool(local or remote)

    def get_or_compute(self, key: str, compute: Any) -> Any:
        """Read-through convenience; compute must itself be safe to retry.

        Concurrent misses may compute the same value. This method intentionally
        does not claim distributed cache-stampede prevention.
        """
        found = self.get(key)
        if found is not None:
            return found
        result = compute()
        if result is not None:
            self.set(key, result)
        return result

    def stats(self) -> dict[str, Any]:
        # A coherent adapter-counter snapshot; L1 has its own separate lock
        # and its statistics are not part of an atomic cross-layer snapshot.
        with self._stats_lock:
            counters = {
                "l1_hits": self._l1_hits,
                "l2_hits": self._l2_hits,
                "misses": self._misses,
                "redis_errors": self._redis_errors,
            }
        return {
            "l1": self.l1.stats(),
            **counters,
            "l1_ttl": self.l1_ttl,
            "l2_ttl": self.l2_ttl,
        }
