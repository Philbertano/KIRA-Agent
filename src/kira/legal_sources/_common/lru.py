"""Two-tier LRU caches for the lookup Lambda.

MemoryLRU: in-process Python-object cache, hot tier.
TmpDiskLRU: file-on-disk cache, warm tier (survives across invocations of
    the same Lambda execution environment but not across cold starts).

Both are thread-unsafe by design; Lambda is single-threaded per execution
environment.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class MemoryLRU(Generic[K, V]):
    """Bounded LRU keyed by hashable K."""

    def __init__(self, *, max_items: int) -> None:
        if max_items <= 0:
            raise ValueError(f"max_items must be > 0, got {max_items}")
        self._max_items = max_items
        self._data: OrderedDict[K, V] = OrderedDict()

    @property
    def size(self) -> int:
        return len(self._data)

    def get(self, key: K) -> V | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: K, value: V) -> None:
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = value
            return
        self._data[key] = value
        if len(self._data) > self._max_items:
            self._data.popitem(last=False)
