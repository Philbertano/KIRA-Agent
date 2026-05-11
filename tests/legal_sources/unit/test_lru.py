import pytest

from kira.legal_sources._common.lru import MemoryLRU


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
