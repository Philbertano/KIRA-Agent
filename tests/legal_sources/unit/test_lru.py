from pathlib import Path

import pytest

from kira.legal_sources._common.lru import MemoryLRU, TmpDiskLRU


def test_memory_lru_get_returns_none_on_miss():
    cache = MemoryLRU[str, int](max_items=3)
    assert cache.get("missing") is None


def test_memory_lru_put_and_get():
    cache = MemoryLRU[str, int](max_items=3)
    cache.put("a", 1)
    assert cache.get("a") == 1


def test_memory_lru_evicts_least_recently_used():
    cache = MemoryLRU[str, int](max_items=2)
    cache.put("a", 1)
    cache.put("b", 2)
    # Access "a" so "b" becomes the LRU.
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("b") is None  # evicted
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_memory_lru_put_promotes_existing_key():
    cache = MemoryLRU[str, int](max_items=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 99)  # update + promote
    cache.put("c", 3)   # should evict "b", not "a"
    assert cache.get("a") == 99
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_memory_lru_size_reflects_entries():
    cache = MemoryLRU[str, int](max_items=5)
    assert cache.size == 0
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.size == 2


def test_memory_lru_rejects_zero_max_items():
    with pytest.raises(ValueError):
        MemoryLRU[str, int](max_items=0)


def test_disk_lru_put_and_get(tmp_path: Path):
    cache = TmpDiskLRU(root=tmp_path, max_bytes=1024)
    cache.put("a.json", b'{"x": 1}')
    assert cache.get("a.json") == b'{"x": 1}'


def test_disk_lru_get_returns_none_on_miss(tmp_path: Path):
    cache = TmpDiskLRU(root=tmp_path, max_bytes=1024)
    assert cache.get("missing.json") is None


def test_disk_lru_evicts_when_over_byte_budget(tmp_path: Path):
    # Budget 100 bytes; each entry is 50 bytes; third entry forces eviction.
    cache = TmpDiskLRU(root=tmp_path, max_bytes=100)
    cache.put("a.json", b"x" * 50)
    cache.put("b.json", b"y" * 50)
    # Access "a" so "b" is LRU.
    cache.get("a.json")
    cache.put("c.json", b"z" * 50)
    assert cache.get("b.json") is None
    assert cache.get("a.json") == b"x" * 50
    assert cache.get("c.json") == b"z" * 50


def test_disk_lru_overwrites_existing_key_without_double_counting(tmp_path: Path):
    cache = TmpDiskLRU(root=tmp_path, max_bytes=100)
    cache.put("a.json", b"x" * 50)
    cache.put("a.json", b"y" * 80)  # in-place replace
    assert cache.bytes_used == 80
    assert cache.get("a.json") == b"y" * 80


def test_disk_lru_creates_root_if_missing(tmp_path: Path):
    target = tmp_path / "nested" / "cache"
    cache = TmpDiskLRU(root=target, max_bytes=100)
    cache.put("a.json", b"x")
    assert target.is_dir()
    assert (target / "a.json").exists()


def test_disk_lru_rejects_keys_with_path_separators(tmp_path: Path):
    cache = TmpDiskLRU(root=tmp_path, max_bytes=100)
    with pytest.raises(ValueError):
        cache.put("../escape.json", b"x")
    with pytest.raises(ValueError):
        cache.put("a/b.json", b"x")
