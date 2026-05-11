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


from collections import OrderedDict as _OrderedDict
from pathlib import Path


class TmpDiskLRU:
    """File-on-disk LRU with a byte budget.

    Keys are flat filenames (no path separators); the cache stores each
    value as a single file under `root`. Evicts least-recently-used when
    `bytes_used` would exceed `max_bytes`.
    """

    def __init__(self, *, root: Path, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be > 0, got {max_bytes}")
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._sizes: _OrderedDict[str, int] = _OrderedDict()
        self._scan_existing()

    @property
    def bytes_used(self) -> int:
        return sum(self._sizes.values())

    def get(self, key: str) -> bytes | None:
        self._validate_key(key)
        if key not in self._sizes:
            return None
        path = self._root / key
        if not path.exists():
            # underlying file vanished; drop from index
            del self._sizes[key]
            return None
        self._sizes.move_to_end(key)
        return path.read_bytes()

    def put(self, key: str, value: bytes) -> None:
        self._validate_key(key)
        new_size = len(value)
        if key in self._sizes:
            # in-place replace: don't double-count
            del self._sizes[key]
        self._evict_until_fits(new_size)
        path = self._root / key
        path.write_bytes(value)
        self._sizes[key] = new_size

    def _scan_existing(self) -> None:
        for path in sorted(self._root.iterdir()):
            if path.is_file():
                self._sizes[path.name] = path.stat().st_size

    def _evict_until_fits(self, incoming: int) -> None:
        while self._sizes and self.bytes_used + incoming > self._max_bytes:
            oldest_key, _ = self._sizes.popitem(last=False)
            (self._root / oldest_key).unlink(missing_ok=True)

    @staticmethod
    def _validate_key(key: str) -> None:
        if "/" in key or "\\" in key or key.startswith(".."):
            raise ValueError(
                f"key {key!r} contains path separators or escapes; "
                "TmpDiskLRU keys must be flat filenames."
            )
