"""Offline cache contract tests: no Redis daemon required."""
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep
from types import SimpleNamespace

import pytest

from osint_research.platform_engineering.lru_cache import LRUCache
from osint_research.platform_engineering.two_tier_cache import RedisBackend, TwoTierCache


class Clock:
    time = 0.0

    def __call__(self):
        return self.time


class FakeRedis:
    def __init__(self):
        self.storage = {}
        self.fail = False
        self.writes = []

    def get(self, key):
        if self.fail:
            raise ConnectionError("offline")
        return self.storage.get(key)

    def set(self, key, value, ttl):
        if self.fail:
            raise ConnectionError("offline")
        self.storage[key] = value
        self.writes.append((key, ttl))
        return True

    def delete(self, key):
        if self.fail:
            raise ConnectionError("offline")
        return self.storage.pop(key, None) is not None


def test_lru_eviction_and_touch():
    c = LRUCache(2, None)
    c.set("a", "A")
    c.set("b", "B")
    assert c.get("a") == "A"
    c.set("c", "C")
    assert c.get("b") is None
    assert c.stats()["evictions"] == 1


def test_ttl_deterministic_with_monotonic_clock():
    clock = Clock()
    c = LRUCache(2, 10, clock=clock)
    c.set("company", {"value": 1})
    clock.time = 9.9
    assert c.get("company") == {"value": 1}
    clock.time = 10
    assert c.get("company") is None


def test_reject_invalid_settings_and_none_value():
    with pytest.raises(ValueError):
        LRUCache(0)
    with pytest.raises(ValueError):
        LRUCache().set("empty", None)
    with pytest.raises(ValueError):
        LRUCache().set("bad", "x", ttl=0)


def test_l2_promotion_then_l1_hit():
    backend = FakeRedis()
    backend.storage["company:00000001"] = {"value": 7}
    c = TwoTierCache("company", redis_backend=backend)
    assert c.get("00000001") == {"value": 7}
    assert c.get("00000001") == {"value": 7}
    assert (c.stats()["l2_hits"], c.stats()["l1_hits"]) == (1, 1)


def test_write_through_ttls_and_invalidation():
    backend = FakeRedis()
    c = TwoTierCache("financials", redis_backend=backend, l1_ttl=5, l2_ttl=120)
    c.set("1", {"revenue": 11})
    assert backend.writes == [("financials:1", 120)]
    assert c.delete("1")
    assert c.get("1") is None


def test_redis_failure_falls_back_to_local_then_compute():
    backend = FakeRedis()
    c = TwoTierCache("company", redis_backend=backend)
    c.set("1", "cached")
    backend.fail = True
    assert c.get("1") == "cached"
    assert c.get_or_compute("2", lambda: "computed") == "computed"
    assert c.get("2") == "computed"
    assert c.stats()["redis_errors"] >= 2


def test_namespaces_are_isolated():
    backend = FakeRedis()
    a = TwoTierCache("a", redis_backend=backend)
    b = TwoTierCache("b", redis_backend=backend)
    a.set("1", "A")
    b.set("1", "B")
    assert a.get("1") == "A" and b.get("1") == "B"


def test_multithreaded_local_cache_has_bounded_size():
    cache = LRUCache(max_size=32, default_ttl=None)
    def visit(i):
        cache.set(str(i), i)
        return cache.get(str(i))
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(visit, range(200)))
    assert cache.stats()["size"] <= 32


def test_get_or_compute_none_is_not_cached():
    c = TwoTierCache("none")
    assert c.get_or_compute("1", lambda: None) is None
    assert c.get("1") is None



def test_concurrent_adapter_counters_do_not_lose_updates():
    """Force thread switches during +=; the adapter lock must protect them."""
    class YieldingCounter(int):
        def __add__(self, amount):
            sleep(0.0001)
            return YieldingCounter(int(self) + amount)

    backend = FakeRedis()
    cache = TwoTierCache("telemetry", redis_backend=backend, l1_max_size=512)
    cache.set("hot", "local")
    for i in range(100):
        backend.storage[f"telemetry:remote-{i}"] = f"value-{i}"

    # Replace only the integer counter values, not the cache or lock.
    for attr in ("_l1_hits", "_l2_hits", "_misses", "_redis_errors"):
        setattr(cache, attr, YieldingCounter(0))

    def concurrent_reads(keys):
        with ThreadPoolExecutor(max_workers=16) as executor:
            return list(executor.map(cache.get, keys))

    assert concurrent_reads(["hot"] * 100) == ["local"] * 100
    assert concurrent_reads([f"remote-{i}" for i in range(100)]) == [
        f"value-{i}" for i in range(100)
    ]
    assert concurrent_reads([f"missing-{i}" for i in range(100)]) == [None] * 100
    backend.fail = True
    assert concurrent_reads([f"error-{i}" for i in range(100)]) == [None] * 100

    snapshot = cache.stats()
    assert {key: snapshot[key] for key in (
        "l1_hits", "l2_hits", "misses", "redis_errors",
    )} == {"l1_hits": 100, "l2_hits": 100, "misses": 200, "redis_errors": 100}
    assert snapshot["l1"]["hits"] == 100


def test_counter_snapshot_readable_while_other_threads_get():
    cache = TwoTierCache("observations")
    cache.set("hot", 42)

    def task(index):
        if index % 5 == 0:
            snapshot = cache.stats()
            assert all(snapshot[field] >= 0 for field in (
                "l1_hits", "l2_hits", "misses", "redis_errors",
            ))
        else:
            assert cache.get("hot") == 42

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(task, range(500)))
    assert cache.stats()["l1_hits"] == 400



@pytest.mark.parametrize("operation", ["set", "delete"])
def test_inflight_l2_read_cannot_undo_newer_local_mutation(operation):
    """Block a stale read after sampling L2, then let a writer complete."""
    class BlockingRedis(FakeRedis):
        def __init__(self):
            super().__init__()
            self.read_started = Event()
            self.release_read = Event()

        def get(self, key):
            stale = super().get(key)
            self.read_started.set()
            if not self.release_read.wait(timeout=5):
                raise TimeoutError("Test failed to release Redis read")
            return stale

    backend = BlockingRedis()
    backend.storage["company:key"] = "old"
    cache = TwoTierCache("company", redis_backend=backend)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(cache.get, "key")
        assert backend.read_started.wait(timeout=3)
        try:
            if operation == "set":
                writer = pool.submit(cache.set, "key", "new")
            else:
                writer = pool.submit(cache.delete, "key")
            assert writer.result(timeout=3)
        finally:
            backend.release_read.set()
        assert reader.result(timeout=3) == ("new" if operation == "set" else None)

    # The stale read must neither return nor install the obsolete value.
    assert cache.l1.get("company:key") == ("new" if operation == "set" else None)
    assert cache.get("key") == ("new" if operation == "set" else None)
    assert cache.stats()["l2_hits"] == 0


def test_older_l2_read_cannot_overwrite_more_recent_l2_promotion():
    class OrderedRedis(FakeRedis):
        def __init__(self):
            super().__init__()
            self.read_started = Event()
            self.release_read = Event()
            self.calls = 0

        def get(self, key):
            self.calls += 1
            if self.calls == 1:
                stale = "old"
                self.read_started.set()
                if not self.release_read.wait(timeout=5):
                    raise TimeoutError("Test failed to release Redis read")
                return stale
            return "new"

    backend = OrderedRedis()
    cache = TwoTierCache("company", redis_backend=backend)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get, "key")
        assert backend.read_started.wait(timeout=3)
        try:
            assert pool.submit(cache.get, "key").result(timeout=3) == "new"
        finally:
            backend.release_read.set()
        assert first.result(timeout=3) == "new"
    assert cache.l1.get("company:key") == "new"
    assert cache.stats()["l2_hits"] == 1
    assert cache.stats()["l1_hits"] == 1



def test_unrelated_key_mutation_does_not_discard_valid_l2_read():
    class BlockingRedis(FakeRedis):
        def __init__(self):
            super().__init__()
            self.read_started = Event()
            self.release_read = Event()

        def get(self, key):
            result = super().get(key)
            self.read_started.set()
            if not self.release_read.wait(timeout=5):
                raise TimeoutError("Test failed to release Redis read")
            return result

    backend = BlockingRedis()
    backend.storage["shared:one"] = "one"
    cache = TwoTierCache("shared", redis_backend=backend)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(cache.get, "one")
        assert backend.read_started.wait(timeout=3)
        try:
            assert pool.submit(cache.set, "other", "updated").result(timeout=3)
        finally:
            backend.release_read.set()
        assert reader.result(timeout=3) == "one"
    assert cache.stats()["l2_hits"] == 1
    assert cache.l1.get("shared:one") == "one"
    assert all(not stripe for stripe in cache._active_reads)



@pytest.mark.parametrize("first, second", [
    ("set", "set"), ("set", "delete"), ("delete", "set"),
])
def test_same_key_write_through_operations_preserve_order(first, second):
    """Hold the first Redis write open; the second must not overtake it."""
    class BlockingBackend(FakeRedis):
        def __init__(self):
            super().__init__()
            self.first_in_redis = Event()
            self.release_first = Event()

        def _wait(self):
            self.first_in_redis.set()
            if not self.release_first.wait(timeout=5):
                raise TimeoutError("Test did not release the first Redis write")

        def set(self, key, value, ttl):
            if first == "set" and value == "first":
                self._wait()
            return super().set(key, value, ttl)

        def delete(self, key):
            if first == "delete":
                self._wait()
            return super().delete(key)

    backend = BlockingBackend()
    backend.storage["ordering:key"] = "initial"
    cache = TwoTierCache("ordering", redis_backend=backend)
    cache.l1.set("ordering:key", "initial")
    blocked_at_lock = Event()

    class ObservedLock:
        """Signal at the real same-key lock acquisition attempt."""
        def __init__(self, lock):
            self._lock = lock

        def __enter__(self):
            blocked_at_lock.set()
            self._lock.acquire()
            return self

        def __exit__(self, *_):
            self._lock.release()

    def next_write():
        if second == "set":
            return cache.set("key", "second")
        return cache.delete("key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            cache.set if first == "set" else cache.delete,
            "key", *(("first",) if first == "set" else ()),
        )
        assert backend.first_in_redis.wait(timeout=3)
        # The first writer already holds this lock. Wrap the *same* lock so
        # the second signals when it tries to acquire it, not at thread start.
        with cache._write_guard:
            entry = cache._write_locks["ordering:key"]
            entry.lock = ObservedLock(entry.lock)
        try:
            second_future = pool.submit(next_write)
            assert blocked_at_lock.wait(timeout=3)
            assert not second_future.done()
            assert cache.l1.get("ordering:key") == (
                "first" if first == "set" else None
            )
        finally:
            backend.release_first.set()
        first_future.result(timeout=3)
        second_future.result(timeout=3)

    expected = "second" if second == "set" else None
    assert cache.l1.get("ordering:key") == expected
    assert backend.storage.get("ordering:key") == expected
    assert cache._write_locks == {}
    assert all(not items for items in cache._pending_writes)


def test_unrelated_key_write_can_finish_while_another_redis_write_waits():
    class BlockingBackend(FakeRedis):
        def __init__(self):
            super().__init__()
            self.first_in_redis = Event()
            self.release_first = Event()

        def set(self, key, value, ttl):
            if key == "parallel:blocked":
                self.first_in_redis.set()
                if not self.release_first.wait(timeout=5):
                    raise TimeoutError("Test did not release blocked Redis write")
            return super().set(key, value, ttl)

    backend = BlockingBackend()
    cache = TwoTierCache("parallel", redis_backend=backend)
    with ThreadPoolExecutor(max_workers=2) as pool:
        blocked = pool.submit(cache.set, "blocked", "first")
        assert backend.first_in_redis.wait(timeout=3)
        try:
            # Must complete BEFORE the first write is released.
            assert pool.submit(cache.set, "other", "second").result(timeout=3)
            assert backend.storage["parallel:other"] == "second"
        finally:
            backend.release_first.set()
        assert blocked.result(timeout=3)
    assert cache._write_locks == {}


def test_local_delete_does_not_resurrect_stale_redis_value_while_pending():
    class DelayedDeletion(FakeRedis):
        def __init__(self):
            super().__init__()
            self.delete_started = Event()
            self.release_delete = Event()
            self.reads = 0

        def get(self, key):
            self.reads += 1
            return super().get(key)

        def delete(self, key):
            self.delete_started.set()
            if not self.release_delete.wait(timeout=5):
                raise TimeoutError("Test did not release Redis delete")
            return super().delete(key)

    backend = DelayedDeletion()
    backend.storage["delete:key"] = "old"
    cache = TwoTierCache("delete", redis_backend=backend)
    cache.l1.set("delete:key", "old")

    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(cache.delete, "key")
        assert backend.delete_started.wait(timeout=3)
        try:
            assert cache.get("key") is None
            assert cache.l1.get("delete:key") is None
            assert backend.reads == 0
        finally:
            backend.release_delete.set()
        assert deletion.result(timeout=3)
    assert cache.get("key") is None
    assert not backend.storage
    assert cache._write_locks == {}
    assert all(not items for items in cache._pending_writes)


def test_failed_redis_write_releases_key_lock_and_pending_state():
    backend = FakeRedis()
    cache = TwoTierCache("degrade", redis_backend=backend)
    backend.fail = True
    assert cache.set("key", "local")
    assert cache.get("key") == "local"
    assert cache.delete("key")
    assert cache._write_locks == {}
    assert all(not items for items in cache._pending_writes)

    backend.fail = False
    assert cache.set("key", "recovered")
    assert backend.storage["degrade:key"] == "recovered"
    assert cache.stats()["redis_errors"] == 2
    assert cache._write_locks == {}



@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), 31, True, "5"])
def test_redis_backend_rejects_missing_or_unbounded_socket_timeouts(value):
    settings = {"socket_timeout": value, "socket_connect_timeout": 2.0}
    client = SimpleNamespace(connection_pool=SimpleNamespace(connection_kwargs=settings))
    with pytest.raises(ValueError, match="socket_timeout"):
        RedisBackend(client)
    settings["socket_timeout"] = 2.0
    settings["socket_connect_timeout"] = value
    with pytest.raises(ValueError, match="socket_connect_timeout"):
        RedisBackend(client)


def test_redis_backend_requires_inspectable_client_socket_settings():
    with pytest.raises(ValueError, match="socket timeouts"):
        RedisBackend(object())


def test_redis_backend_factory_sets_explicit_bounded_timeouts(monkeypatch):
    captured = {}

    class FakeClientFactory:
        @staticmethod
        def from_url(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return SimpleNamespace(connection_pool=SimpleNamespace(
                connection_kwargs=kwargs,
            ))

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=FakeClientFactory))
    adapter = RedisBackend.from_url(
        "redis://127.0.0.1:6379/0",
        socket_timeout=0.5, socket_connect_timeout=0.25,
    )
    assert adapter.client.connection_pool.connection_kwargs["socket_timeout"] == 0.5
    assert captured["url"] == "redis://127.0.0.1:6379/0"
    assert captured["kwargs"]["socket_connect_timeout"] == 0.25
    assert captured["kwargs"]["decode_responses"] is True
    assert captured["kwargs"]["retry_on_timeout"] is False
    with pytest.raises(ValueError, match="socket_timeout"):
        RedisBackend.from_url("redis://ignored", socket_timeout=float("inf"))


def test_simulated_redis_socket_timeout_releases_writer_and_allows_following_write():
    class TimeoutClient:
        def __init__(self):
            self.connection_pool = SimpleNamespace(connection_kwargs={
                "socket_timeout": 0.1,
                "socket_connect_timeout": 0.1,
            })
            self.fail = True
            self.storage = {}

        def get(self, key):
            if self.fail:
                raise TimeoutError("simulated socket read timeout")
            return self.storage.get(key)

        def set(self, key, value, ex):
            if self.fail:
                raise TimeoutError("simulated socket write timeout")
            self.storage[key] = value
            return True

        def delete(self, key):
            if self.fail:
                raise TimeoutError("simulated socket delete timeout")
            return self.storage.pop(key, None) is not None

    remote = TimeoutClient()
    cache = TwoTierCache("timeout", redis_backend=RedisBackend(remote))
    assert cache.set("key", "local")
    assert cache.get("key") == "local"
    assert cache.stats()["redis_errors"] == 1
    assert cache._write_locks == {}
    assert all(not pending for pending in cache._pending_writes)

    remote.fail = False
    assert cache.set("key", "new")
    assert cache.delete("key")
    assert remote.storage == {}
    assert cache._write_locks == {}
    assert all(not pending for pending in cache._pending_writes)
