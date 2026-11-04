"""Thread-safe, bounded local LRU with optional TTL.

Adapted from a historical application LRU implementation. Uses monotonic time
for expiry, explicit capacity checks and per-instance storage.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any, Callable


@dataclass(slots=True)
class Entry:
    value: Any
    expires_at: float | None


class LRUCache:
    """LRU semantics: a successful read makes the entry most-recently used.

    None is reserved for cache misses, so storing None is explicitly disallowed.
    Every mutation of storage and hit counters takes the same reentrant lock.
    """

    def __init__(
        self, max_size: int = 1000, default_ttl: float | None = 300,
        *, clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be positive")
        if default_ttl is not None and default_ttl <= 0:
            raise ValueError("default_ttl must be positive or None")
        self._data: OrderedDict[str, Entry] = OrderedDict()
        self._max_size = max_size
        self._ttl = default_ttl
        self._clock = clock
        self._lock = RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            if item.expires_at is not None and self._clock() >= item.expires_at:
                del self._data[key]
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return item.value

    def set(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        if value is None:
            raise ValueError("Cannot cache None; it represents a miss")
        duration = self._ttl if ttl is None else ttl
        if duration is not None and duration <= 0:
            raise ValueError("TTL must be positive")
        with self._lock:
            expires_at = self._clock() + duration if duration is not None else None
            self._data[key] = Entry(value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)
                self._evictions += 1

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }
