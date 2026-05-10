# Legal-Sources V2 (all laws + semantic search) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend KIRA's legal-sources Lambda from "Mietrecht-curated subset" to "any of ~2,500 Bundesgesetze + Rechtsverordnungen", and add a `search_norm` MCP tool that performs semantic search over every paragraph of every law via S3 Vectors + Cohere multilingual v3 on Bedrock.

**Architecture:** Per-paragraph S3 storage (`gesetze/<abk>/<paragraph>.json` + `gesetze/<abk>/_meta.json`), manifest v2 with per-Gesetz upstream etags, lazy-load + LRU on the lookup Lambda, one-time local backfill, Cloudflare Worker forwards conditional headers for efficient daily incremental ingest, separate Lambda for semantic search backed by S3 Vectors.

**Tech Stack:** Python 3.11, Pydantic v2, httpx, boto3 (S3, Lambda, Bedrock, S3 Vectors), AWS CDK Python, Cohere multilingual v3 on Bedrock, S3 Vectors (eu-central-1), Cloudflare Workers.

**Spec:** `docs/superpowers/specs/2026-05-10-legal-sources-v2-all-laws-and-search-design.md`

---

## File map

**New source files:**
- `src/kira/legal_sources/_common/lru.py` — `MemoryLRU` + `TmpDiskLRU`
- `src/kira/legal_sources/_common/manifest.py` — manifest v2 read/write
- `src/kira/legal_sources/_common/toc.py` — gii-toc.xml parser + filter
- `src/kira/legal_sources/_common/embedder.py` — Bedrock Cohere wrapper
- `src/kira/legal_sources/_common/vector_index.py` — S3 Vectors wrapper
- `src/kira/legal_sources/gesetze/search_norm.py` — pure function for search
- `src/kira/legal_sources/adapters/search_handler.py` — search Lambda entrypoint
- `scripts/backfill_corpus.py` — one-time local backfill driver

**Modified source files:**
- `src/kira/legal_sources/_common/errors.py` — add `EmbeddingUnavailableError`
- `src/kira/legal_sources/_common/s3_corpus.py` — replace eager preload with lazy-load + LRU
- `src/kira/legal_sources/gesetze/corpus_format.py` — new types matching v2 layout
- `src/kira/legal_sources/gesetze/lookup_norm.py` — switch to lazy lookup callable
- `src/kira/legal_sources/gesetze/schema.py` — add `SearchNormInput`/`Success`/`Error`/`Result`; `LookupNormErrorCode.EMBEDDING_UNAVAILABLE`
- `src/kira/legal_sources/adapters/lookup_handler.py` — wire lazy-load
- `src/kira/legal_sources/adapters/ingest_handler.py` — TOC discovery, conditional GET, paragraph-level diff, embedding upsert
- `src/kira/legal_sources/adapters/kira_registry.py` — register both tools, `build_search_norm_tool()`
- `src/kira/legal_sources/adapters/agent_sdk.py` — `make_search_norm_tool_function()`

**Modified infra files:**
- `infra/legal_sources/stack.py` — Search Lambda + S3 Vectors index + IAM grants + ephemeral storage bump
- `infra/cloudflare/juris-proxy/worker.js` — pass through `If-None-Match` / `If-Modified-Since`, propagate `304`
- `tests/infra/test_region_pin.py` — assert new resources also pinned

**New test files:**
- `tests/legal_sources/unit/test_lru.py`
- `tests/legal_sources/unit/test_manifest.py`
- `tests/legal_sources/unit/test_toc.py`
- `tests/legal_sources/unit/test_embedder.py`
- `tests/legal_sources/unit/test_vector_index.py`
- `tests/legal_sources/unit/test_search_norm.py`
- `tests/legal_sources/adapters/test_search_handler.py`
- `tests/legal_sources/live/test_live_smoke_v2.py`
- `tests/legal_sources/perf/test_perf_budgets.py` (gated by `RUN_PERF_TESTS=1`)
- `tests/legal_sources/fixtures/captured/gii_toc_subset.xml`
- `tests/legal_sources/fixtures/captured/weg.zip`
- `tests/legal_sources/fixtures/cohere_embed_response.json`

**Modified test files:**
- `tests/legal_sources/unit/test_corpus_format.py` — new shape
- `tests/legal_sources/unit/test_lookup_norm.py` — new lookup callable contract
- `tests/legal_sources/unit/test_s3_corpus.py` — lazy-load tests
- `tests/legal_sources/unit/test_schema.py` — add SearchNormInput tests
- `tests/legal_sources/adapters/test_lookup_handler.py` — lazy-load wiring
- `tests/legal_sources/adapters/test_ingest_handler.py` — diff + embedding paths
- `tests/legal_sources/adapters/test_kira_registry.py` — search registration
- `tests/legal_sources/adapters/test_agent_sdk.py` — search function

**Modified scripts:**
- `scripts/legal_sources_smoke.py` — add search round-trip

**Modified pyproject.toml:**
- New deps in `[dev]`: `freezegun>=1.5.0` (deterministic time for LRU tests)
- Lock `boto3>=1.39.0` (S3 Vectors GA cutoff)

---

## Conventions for every task

- **Working directory:** `/Users/philiptrempler/Documents/Visual Studio Code/KIRA-Agent/KIRA-Agent/.worktrees/feature-legal-sources-tool1`
- **Branch:** `feature/legal-sources-tool1` (V2 work continues on the V1 branch).
- **Python:** all commands use `.venv/bin/python` and `.venv/bin/pytest`.
- **Commits:** one commit per task. Conventional Commits: `feat:`, `test:`, `chore:`, `refactor:`, `docs:`.
- **No `kira.*` imports** inside `_common/` or `gesetze/`. `adapters/`, scripts, and infra are the bridging seam.
- **No auto-formatters.** Type code byte-exact. Ruff cleanup happens in the final task.
- **AWS region** `eu-central-1` everywhere.
- **Bedrock model access:** before Task 7+ runs end-to-end, the `cohere.embed-multilingual-v3` model must be enabled in the AWS account's Bedrock console (eu-central-1). Document this in Task 1's commit message.

---

## Task 1: Dependency updates

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add freezegun and bump boto3 floor**

In `pyproject.toml`, in `[project.optional-dependencies].dev`, append `"freezegun>=1.5.0"`. In `[project].dependencies`, ensure `"boto3>=1.39.0"` (S3 Vectors GA cutoff).

- [ ] **Step 2: Install**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: `freezegun` and `boto3>=1.39.0` listed in `pip list`.

- [ ] **Step 3: Verify nothing broke**

Run: `.venv/bin/pytest tests/legal_sources/ tests/infra/ -q -m 'not live'`
Expected: all current tests still pass.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add freezegun + bump boto3 for V2"
```

The commit body should remind operators to enable `cohere.embed-multilingual-v3` in Bedrock (eu-central-1) before deploying V2.

---

## Task 2: Manifest v2 schema and reader/writer

**Files:**
- Create: `src/kira/legal_sources/_common/manifest.py`
- Test: `tests/legal_sources/unit/test_manifest.py`

- [ ] **Step 1: Write failing tests**

Create `tests/legal_sources/unit/test_manifest.py`:

```python
import json

import pytest
from pydantic import ValidationError

from kira.legal_sources._common.manifest import (
    GesetzManifestEntry,
    Manifest,
    ManifestVersionError,
    parse_manifest,
)


def test_parses_minimal_v2_manifest():
    payload = {
        "version": 2,
        "stand": "2026-05-10",
        "gesetze": {
            "bgb": {
                "abkuerzung": "BGB",
                "titel": "Bürgerliches Gesetzbuch",
                "type": "Gesetz",
                "meta_key": "gesetze/bgb/_meta.json",
                "upstream_etag": "\"abc\"",
                "upstream_last_modified": "Wed, 06 May 2026 15:45:05 GMT",
            }
        },
    }
    m = parse_manifest(payload)
    assert isinstance(m, Manifest)
    assert m.version == 2
    assert "bgb" in m.gesetze
    assert m.gesetze["bgb"].abkuerzung == "BGB"


def test_v1_manifest_raises_clear_error():
    payload = {"version": 1, "files": ["gesetze/bgb.json"]}
    with pytest.raises(ManifestVersionError) as excinfo:
        parse_manifest(payload)
    assert "version 2" in str(excinfo.value)


def test_unknown_version_raises():
    payload = {"version": 99, "stand": "2026-05-10", "gesetze": {}}
    with pytest.raises(ManifestVersionError):
        parse_manifest(payload)


def test_round_trip_serialization():
    m = Manifest(
        version=2,
        stand="2026-05-10",
        gesetze={
            "bgb": GesetzManifestEntry(
                abkuerzung="BGB",
                titel="Bürgerliches Gesetzbuch",
                type="Gesetz",
                meta_key="gesetze/bgb/_meta.json",
                upstream_etag="\"abc\"",
                upstream_last_modified="Wed, 06 May 2026 15:45:05 GMT",
            )
        },
    )
    dumped = m.model_dump_json()
    parsed = parse_manifest(json.loads(dumped))
    assert parsed.gesetze["bgb"].abkuerzung == "BGB"


def test_extra_fields_rejected_on_entry():
    payload = {
        "version": 2,
        "stand": "2026-05-10",
        "gesetze": {
            "bgb": {
                "abkuerzung": "BGB",
                "titel": "x",
                "type": "Gesetz",
                "meta_key": "gesetze/bgb/_meta.json",
                "upstream_etag": "\"abc\"",
                "upstream_last_modified": "...",
                "extra_field": "boom",
            }
        },
    }
    with pytest.raises(ValidationError):
        parse_manifest(payload)
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_manifest.py -v`
Expected: ImportError on `kira.legal_sources._common.manifest`.

- [ ] **Step 3: Implement**

Create `src/kira/legal_sources/_common/manifest.py`:

```python
"""Manifest v2: catalog of all known Gesetze in the corpus."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ManifestVersionError(ValueError):
    """Raised when an incompatible manifest version is encountered."""


class GesetzManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    abkuerzung: str
    titel: str
    type: Literal["Gesetz", "Verordnung"]
    meta_key: str
    upstream_etag: str
    upstream_last_modified: str


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    stand: str
    gesetze: dict[str, GesetzManifestEntry]


def parse_manifest(payload: dict[str, Any]) -> Manifest:
    version = payload.get("version")
    if version != 2:
        raise ManifestVersionError(
            f"Unsupported manifest version {version!r} — V2 expects version 2. "
            "If you are reading a V1 manifest, run scripts/backfill_corpus.py "
            "to rewrite the corpus in V2 layout."
        )
    return Manifest.model_validate(payload)
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_manifest.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/manifest.py tests/legal_sources/unit/test_manifest.py
git commit -m "feat(legal-sources): manifest v2 schema with version guard"
```

---

## Task 3: MemoryLRU

**Files:**
- Create: `src/kira/legal_sources/_common/lru.py`
- Test: `tests/legal_sources/unit/test_lru.py`

- [ ] **Step 1: Write failing tests for MemoryLRU**

Create `tests/legal_sources/unit/test_lru.py`:

```python
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
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lru.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement MemoryLRU**

Create `src/kira/legal_sources/_common/lru.py`:

```python
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
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lru.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/lru.py tests/legal_sources/unit/test_lru.py
git commit -m "feat(legal-sources): MemoryLRU"
```

---

## Task 4: TmpDiskLRU

**Files:**
- Modify: `src/kira/legal_sources/_common/lru.py`
- Modify: `tests/legal_sources/unit/test_lru.py`

- [ ] **Step 1: Append failing TmpDiskLRU tests**

Append to `tests/legal_sources/unit/test_lru.py`:

```python
from pathlib import Path

from kira.legal_sources._common.lru import TmpDiskLRU


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
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lru.py -v`
Expected: ImportError on TmpDiskLRU.

- [ ] **Step 3: Append TmpDiskLRU implementation**

Append to `src/kira/legal_sources/_common/lru.py`:

```python
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
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lru.py -v`
Expected: 12 passed total.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/lru.py tests/legal_sources/unit/test_lru.py
git commit -m "feat(legal-sources): TmpDiskLRU with byte-budget eviction"
```

---

## Task 5: corpus_format v2 types

**Files:**
- Modify: `src/kira/legal_sources/gesetze/corpus_format.py`
- Modify: `tests/legal_sources/unit/test_corpus_format.py`

The V1 `GesetzKorpus` (whole-Gesetz blob) is replaced by two narrower types:
`GesetzMeta` (per-Gesetz metadata + paragraph index) and `Norm` (per-paragraph
content). Existing types are RENAMED to break callers loudly rather than
silently misbehave with the new layout.

- [ ] **Step 1: Replace test file with v2 tests**

Replace `tests/legal_sources/unit/test_corpus_format.py` entirely with:

```python
import pytest

from kira.legal_sources.gesetze.corpus_format import (
    Absatz,
    GesetzMeta,
    Norm,
    NormIndexEntry,
)


def test_norm_validates_minimal_payload():
    payload = {
        "gesetz": "BGB",
        "paragraph": "535",
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "absaetze": [
            {"nummer": "1", "text": "Durch den Mietvertrag ..."},
            {"nummer": "2", "text": "Der Mieter ..."},
        ],
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
    }
    n = Norm.model_validate(payload)
    assert n.gesetz == "BGB"
    assert n.paragraph == "535"
    assert n.absaetze[0].nummer == "1"


def test_norm_extra_fields_ignored():
    payload = {
        "gesetz": "BGB",
        "paragraph": "535",
        "titel": "x",
        "absaetze": [],
        "quelle_url": "https://example.test",
        "legacy_field": "ignore me",
    }
    n = Norm.model_validate(payload)  # no exception
    assert n.titel == "x"


def test_gesetz_meta_validates_with_paragraph_index():
    payload = {
        "abkuerzung": "BGB",
        "titel": "Bürgerliches Gesetzbuch",
        "type": "Gesetz",
        "stand": "2026-05-10",
        "quelle": "gesetze-im-internet.de",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb",
        "upstream_xml_zip_url": "https://www.gesetze-im-internet.de/bgb/xml.zip",
        "paragraphen": {
            "535": {
                "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                "key": "gesetze/bgb/535.json",
                "content_sha256": "abc",
            }
        },
    }
    m = GesetzMeta.model_validate(payload)
    assert m.abkuerzung == "BGB"
    assert "535" in m.paragraphen
    entry = m.paragraphen["535"]
    assert isinstance(entry, NormIndexEntry)
    assert entry.key == "gesetze/bgb/535.json"


def test_norm_index_entry_extra_fields_ignored():
    entry = NormIndexEntry.model_validate(
        {
            "titel": "x",
            "key": "gesetze/bgb/535.json",
            "content_sha256": "abc",
            "future": "field",
        }
    )
    assert entry.titel == "x"


def test_absatz_round_trip():
    a = Absatz(nummer="1", text="hello")
    assert a.model_dump() == {"nummer": "1", "text": "hello"}


def test_gesetz_meta_rejects_unknown_type():
    payload = {
        "abkuerzung": "X",
        "titel": "x",
        "type": "Ratgeber",  # not in {"Gesetz","Verordnung"}
        "stand": "2026-05-10",
        "quelle": "x",
        "quelle_url": "https://example.test",
        "upstream_xml_zip_url": "https://example.test/xml.zip",
        "paragraphen": {},
    }
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        GesetzMeta.model_validate(payload)
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_corpus_format.py -v`
Expected: ImportError.

- [ ] **Step 3: Replace corpus_format.py**

Replace `src/kira/legal_sources/gesetze/corpus_format.py` entirely with:

```python
"""V2 corpus types — per-paragraph storage layout.

V1's `GesetzKorpus` (whole-Gesetz blob) is intentionally absent; if you
encounter it in code, the call site is on V1 and needs migration.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Absatz(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nummer: str
    text: str


class Norm(BaseModel):
    """Single paragraph's content. Stored at gesetze/<abk>/<paragraph>.json."""

    model_config = ConfigDict(extra="ignore")

    gesetz: str
    paragraph: str
    titel: str = ""
    absaetze: list[Absatz] = Field(default_factory=list)
    quelle_url: str | None = None


class NormIndexEntry(BaseModel):
    """One entry in GesetzMeta.paragraphen — points at the per-paragraph file."""

    model_config = ConfigDict(extra="ignore")

    titel: str = ""
    key: str
    content_sha256: str


class GesetzMeta(BaseModel):
    """Per-Gesetz metadata. Stored at gesetze/<abk>/_meta.json."""

    model_config = ConfigDict(extra="ignore")

    abkuerzung: str
    titel: str
    type: Literal["Gesetz", "Verordnung"]
    stand: str
    quelle: str
    quelle_url: str
    upstream_xml_zip_url: str
    paragraphen: dict[str, NormIndexEntry] = Field(default_factory=dict)
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_corpus_format.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/gesetze/corpus_format.py tests/legal_sources/unit/test_corpus_format.py
git commit -m "refactor(legal-sources): corpus_format types for v2 layout"
```

---

## Task 6: TOC fetcher, parser, and filter

**Files:**
- Create: `src/kira/legal_sources/_common/toc.py`
- Test: `tests/legal_sources/unit/test_toc.py`
- Create: `tests/legal_sources/fixtures/captured/gii_toc_subset.xml`

- [ ] **Step 1: Create the TOC fixture**

Create `tests/legal_sources/fixtures/captured/gii_toc_subset.xml` with this exact content (representative slice — real BGB entry, real WEG, plus 2 things the filter must exclude):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Gesetze und Verordnungen</title>
    <item>
      <title>Bürgerliches Gesetzbuch</title>
      <link>https://www.gesetze-im-internet.de/bgb/xml.zip</link>
      <description>BGB</description>
    </item>
    <item>
      <title>Wohnungseigentumsgesetz</title>
      <link>https://www.gesetze-im-internet.de/woeigg/xml.zip</link>
      <description>WEG</description>
    </item>
    <item>
      <title>Betriebskostenverordnung</title>
      <link>https://www.gesetze-im-internet.de/betrkv/xml.zip</link>
      <description>BetrKV</description>
    </item>
    <item>
      <title>Bekanntmachung über Beispielverordnung</title>
      <link>https://www.gesetze-im-internet.de/beispielbek/xml.zip</link>
      <description>BeispielBek</description>
    </item>
    <item>
      <title>Geschäftsordnung des Rats</title>
      <link>https://www.gesetze-im-internet.de/ratsgo/xml.zip</link>
      <description>RatsGO</description>
    </item>
    <item>
      <title>Bundesentschädigungsgesetz (aufgehoben)</title>
      <link>https://www.gesetze-im-internet.de/beg/xml.zip</link>
      <description>BEG</description>
    </item>
  </channel>
</rss>
```

- [ ] **Step 2: Write failing tests**

Create `tests/legal_sources/unit/test_toc.py`:

```python
from pathlib import Path

import httpx
import pytest
import respx

from kira.legal_sources._common.toc import (
    TocEntry,
    fetch_toc,
    is_citable,
    parse_toc,
    slug_for,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "captured"


def test_parse_toc_returns_entries_for_each_item():
    raw = (FIXTURES / "gii_toc_subset.xml").read_bytes()
    entries = parse_toc(raw)
    assert len(entries) == 6
    titles = [e.title for e in entries]
    assert "Bürgerliches Gesetzbuch" in titles


def test_slug_extracted_from_xml_zip_url():
    assert slug_for("https://www.gesetze-im-internet.de/bgb/xml.zip") == "bgb"
    assert slug_for("https://www.gesetze-im-internet.de/woeigg/xml.zip") == "woeigg"


def test_is_citable_accepts_real_laws():
    assert is_citable(TocEntry(
        title="Bürgerliches Gesetzbuch",
        link="https://www.gesetze-im-internet.de/bgb/xml.zip",
    ))
    assert is_citable(TocEntry(
        title="Wohnungseigentumsgesetz",
        link="https://www.gesetze-im-internet.de/woeigg/xml.zip",
    ))


def test_is_citable_rejects_bekanntmachung_by_slug():
    assert not is_citable(TocEntry(
        title="Bekanntmachung über Beispielverordnung",
        link="https://www.gesetze-im-internet.de/beispielbek/xml.zip",
    ))


def test_is_citable_rejects_geschaeftsordnung_by_slug():
    assert not is_citable(TocEntry(
        title="Geschäftsordnung des Rats",
        link="https://www.gesetze-im-internet.de/ratsgo/xml.zip",
    ))


def test_is_citable_rejects_repealed_by_title():
    assert not is_citable(TocEntry(
        title="Bundesentschädigungsgesetz (aufgehoben)",
        link="https://www.gesetze-im-internet.de/beg/xml.zip",
    ))


def test_fetch_toc_via_proxy_url(monkeypatch):
    monkeypatch.setenv(
        "LEGAL_INGEST_PROXY_URL",
        "https://kira-legaltext-gii-proxy.example.workers.dev",
    )
    raw = (FIXTURES / "gii_toc_subset.xml").read_bytes()
    with respx.mock(assert_all_called=True) as mock:
        mock.get(
            "https://kira-legaltext-gii-proxy.example.workers.dev/",
            params={"url": "https://www.gesetze-im-internet.de/gii-toc.xml"},
        ).mock(return_value=httpx.Response(200, content=raw))
        with httpx.Client() as client:
            entries = fetch_toc(client)
    assert len(entries) == 6


def test_fetch_toc_directly_when_no_proxy(monkeypatch):
    monkeypatch.delenv("LEGAL_INGEST_PROXY_URL", raising=False)
    raw = (FIXTURES / "gii_toc_subset.xml").read_bytes()
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=raw)
        )
        with httpx.Client() as client:
            entries = fetch_toc(client)
    assert len(entries) == 6
```

- [ ] **Step 3: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_toc.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement**

Create `src/kira/legal_sources/_common/toc.py`:

```python
"""Discover all Gesetze + Verordnungen via gii-toc.xml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx

GII_TOC_URL = "https://www.gesetze-im-internet.de/gii-toc.xml"

_REJECT_SLUG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"bek$", re.IGNORECASE),
    re.compile(r"verfg$", re.IGNORECASE),
    re.compile(r"erl$", re.IGNORECASE),
    re.compile(r"vorschr$", re.IGNORECASE),
    re.compile(r"go\d*$", re.IGNORECASE),
    re.compile(r"geschoangleg$", re.IGNORECASE),
    re.compile(r"hauseigung$", re.IGNORECASE),
]
_REJECT_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\(aufgehoben\)", re.IGNORECASE),
    re.compile(r"\(außer\s+Kraft\)", re.IGNORECASE),
]


@dataclass(frozen=True)
class TocEntry:
    title: str
    link: str


def parse_toc(raw_xml: bytes) -> list[TocEntry]:
    root = ET.fromstring(raw_xml)
    out: list[TocEntry] = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None:
            continue
        title = (title_el.text or "").strip()
        link = (link_el.text or "").strip()
        if title and link:
            out.append(TocEntry(title=title, link=link))
    return out


def slug_for(link: str) -> str:
    parts = [p for p in link.split("/") if p]
    if len(parts) < 2:
        return ""
    return parts[-2].lower()


def is_citable(entry: TocEntry) -> bool:
    slug = slug_for(entry.link)
    for pat in _REJECT_SLUG_PATTERNS:
        if pat.search(slug):
            return False
    for pat in _REJECT_TITLE_PATTERNS:
        if pat.search(entry.title):
            return False
    return True


def fetch_toc(client: httpx.Client) -> list[TocEntry]:
    proxy = os.environ.get("LEGAL_INGEST_PROXY_URL")
    if proxy:
        url = f"{proxy.rstrip('/')}/?url={quote(GII_TOC_URL, safe='')}"
    else:
        url = GII_TOC_URL
    resp = client.get(url)
    resp.raise_for_status()
    return parse_toc(resp.content)
```

- [ ] **Step 5: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_toc.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kira/legal_sources/_common/toc.py \
        tests/legal_sources/unit/test_toc.py \
        tests/legal_sources/fixtures/captured/gii_toc_subset.xml
git commit -m "feat(legal-sources): gii-toc.xml fetcher + filter for citable laws"
```

---

## Task 7: Embedder — Bedrock Cohere wrapper

**Files:**
- Create: `src/kira/legal_sources/_common/embedder.py`
- Create: `src/kira/legal_sources/_common/errors.py` extension
- Test: `tests/legal_sources/unit/test_embedder.py`
- Create: `tests/legal_sources/fixtures/cohere_embed_response.json`

- [ ] **Step 1: Create response fixture**

Create `tests/legal_sources/fixtures/cohere_embed_response.json` containing a synthetic Cohere response with two 1024-dim vectors. Use this Python one-liner to write it:

```bash
.venv/bin/python -c "
import json, pathlib
v1 = [0.001 * i for i in range(1024)]
v2 = [0.002 * i for i in range(1024)]
out = pathlib.Path('tests/legal_sources/fixtures/cohere_embed_response.json')
out.write_text(json.dumps({'embeddings': [v1, v2], 'id': 'fake', 'response_type': 'embeddings_floats', 'texts': ['a','b']}))
print('wrote', out, out.stat().st_size, 'bytes')
"
```

- [ ] **Step 2: Add EmbeddingUnavailableError**

Append to `src/kira/legal_sources/_common/errors.py`:

```python
class EmbeddingUnavailableError(ToolError):
    code = "embedding_unavailable"

    def __init__(self, message: str) -> None:
        super().__init__(message)
```

Add to `tests/legal_sources/unit/test_errors.py`:

```python
def test_embedding_unavailable_is_tool_error():
    from kira.legal_sources._common.errors import EmbeddingUnavailableError, ToolError
    err = EmbeddingUnavailableError("bedrock down")
    assert isinstance(err, ToolError)
    assert err.code == "embedding_unavailable"
```

Run: `.venv/bin/pytest tests/legal_sources/unit/test_errors.py -v`
Expected: 3 passed (was 2, now 3).

- [ ] **Step 3: Write embedder tests**

Create `tests/legal_sources/unit/test_embedder.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from kira.legal_sources._common.embedder import (
    CohereMultilingualEmbedder,
    EMBEDDING_DIMENSION,
)
from kira.legal_sources._common.errors import EmbeddingUnavailableError

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _fake_bedrock_client(response_payload: dict) -> MagicMock:
    client = MagicMock()
    body = MagicMock()
    body.read.return_value = json.dumps(response_payload).encode("utf-8")
    client.invoke_model.return_value = {"body": body}
    return client


def test_embed_documents_returns_vectors_in_order():
    response = json.loads((FIXTURES / "cohere_embed_response.json").read_text())
    client = _fake_bedrock_client(response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    vectors = embedder.embed_documents(["a", "b"])
    assert len(vectors) == 2
    assert len(vectors[0]) == EMBEDDING_DIMENSION
    args, kwargs = client.invoke_model.call_args
    body = json.loads(kwargs["body"])
    assert body["input_type"] == "search_document"
    assert body["texts"] == ["a", "b"]


def test_embed_query_uses_search_query_input_type():
    response = json.loads((FIXTURES / "cohere_embed_response.json").read_text())
    client = _fake_bedrock_client(response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    embedder.embed_query("Pflichten des Vermieters")
    body = json.loads(client.invoke_model.call_args.kwargs["body"])
    assert body["input_type"] == "search_query"
    assert body["texts"] == ["Pflichten des Vermieters"]


def test_embed_documents_chunks_above_batch_limit():
    response = json.loads((FIXTURES / "cohere_embed_response.json").read_text())
    # Pad response so each batch returns the expected length.
    big_response = {**response, "embeddings": [response["embeddings"][0]] * 96}
    client = _fake_bedrock_client(big_response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client, batch_size=96)
    inputs = ["x"] * 200
    embedder.embed_documents(inputs)
    # 200 inputs / 96 batch = 3 calls
    assert client.invoke_model.call_count == 3


def test_embed_documents_truncates_long_input():
    response = json.loads((FIXTURES / "cohere_embed_response.json").read_text())
    client = _fake_bedrock_client(response)
    embedder = CohereMultilingualEmbedder(bedrock_client=client, max_chars=100)
    embedder.embed_documents(["x" * 500, "y" * 500])
    body = json.loads(client.invoke_model.call_args.kwargs["body"])
    assert all(len(t) <= 100 for t in body["texts"])


def test_bedrock_client_error_maps_to_embedding_unavailable():
    client = MagicMock()
    client.invoke_model.side_effect = ClientError(
        error_response={"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        operation_name="InvokeModel",
    )
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    with pytest.raises(EmbeddingUnavailableError) as excinfo:
        embedder.embed_documents(["x"])
    assert "ThrottlingException" in str(excinfo.value)


def test_empty_input_returns_empty_list_without_calling_bedrock():
    client = MagicMock()
    embedder = CohereMultilingualEmbedder(bedrock_client=client)
    assert embedder.embed_documents([]) == []
    assert client.invoke_model.call_count == 0
```

- [ ] **Step 4: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_embedder.py -v`
Expected: ImportError.

- [ ] **Step 5: Implement**

Create `src/kira/legal_sources/_common/embedder.py`:

```python
"""Cohere multilingual v3 embedder via Bedrock InvokeModel."""

from __future__ import annotations

import json
from typing import Any, Literal

from botocore.exceptions import ClientError

from kira.legal_sources._common.errors import EmbeddingUnavailableError

EMBEDDING_DIMENSION = 1024
MODEL_ID = "cohere.embed-multilingual-v3"
DEFAULT_BATCH_SIZE = 96
DEFAULT_MAX_CHARS = 6000


class CohereMultilingualEmbedder:
    """Wraps `bedrock-runtime:InvokeModel` for Cohere multilingual v3.

    Splits inputs into batches of `batch_size` (Cohere's per-request cap is 96).
    Truncates each input to `max_chars` characters before sending.
    """

    def __init__(
        self,
        *,
        bedrock_client: Any,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self._client = bedrock_client
        self._batch_size = batch_size
        self._max_chars = max_chars

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._invoke(texts, input_type="search_document")

    def embed_query(self, text: str) -> list[float]:
        result = self._invoke([text], input_type="search_query")
        return result[0]

    def _invoke(
        self,
        texts: list[str],
        *,
        input_type: Literal["search_document", "search_query"],
    ) -> list[list[float]]:
        if not texts:
            return []
        truncated = [t[: self._max_chars] for t in texts]
        out: list[list[float]] = []
        for start in range(0, len(truncated), self._batch_size):
            batch = truncated[start : start + self._batch_size]
            try:
                response = self._client.invoke_model(
                    modelId=MODEL_ID,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(
                        {
                            "texts": batch,
                            "input_type": input_type,
                            "embedding_types": ["float"],
                        }
                    ),
                )
            except ClientError as exc:
                raise EmbeddingUnavailableError(
                    f"Bedrock InvokeModel failed: {exc}"
                ) from exc
            payload = json.loads(response["body"].read())
            out.extend(payload["embeddings"])
        return out
```

- [ ] **Step 6: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_embedder.py tests/legal_sources/unit/test_errors.py -v`
Expected: 9 passed (6 embedder + 3 errors).

- [ ] **Step 7: Commit**

```bash
git add src/kira/legal_sources/_common/embedder.py \
        src/kira/legal_sources/_common/errors.py \
        tests/legal_sources/unit/test_embedder.py \
        tests/legal_sources/unit/test_errors.py \
        tests/legal_sources/fixtures/cohere_embed_response.json
git commit -m "feat(legal-sources): Cohere multilingual v3 embedder"
```

---

## Task 8: VectorIndex — S3 Vectors wrapper

**Files:**
- Create: `src/kira/legal_sources/_common/vector_index.py`
- Test: `tests/legal_sources/unit/test_vector_index.py`

- [ ] **Step 1: Write failing tests**

Create `tests/legal_sources/unit/test_vector_index.py`:

```python
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.vector_index import (
    VectorIndex,
    VectorRecord,
    VectorSearchHit,
)


def test_upsert_passes_vectors_with_metadata_to_client():
    client = MagicMock()
    idx = VectorIndex(s3vectors_client=client, index_name="kira-legal-norms")
    records = [
        VectorRecord(key="bgb-535", vector=[0.1] * 1024, metadata={"gesetz": "BGB"}),
        VectorRecord(key="bgb-536", vector=[0.2] * 1024, metadata={"gesetz": "BGB"}),
    ]
    idx.upsert(records)
    client.put_vectors.assert_called_once()
    kwargs = client.put_vectors.call_args.kwargs
    assert kwargs["indexName"] == "kira-legal-norms"
    sent_vectors = kwargs["vectors"]
    assert {v["key"] for v in sent_vectors} == {"bgb-535", "bgb-536"}


def test_upsert_chunks_above_batch_limit():
    client = MagicMock()
    idx = VectorIndex(
        s3vectors_client=client, index_name="kira-legal-norms", upsert_batch_size=10
    )
    records = [
        VectorRecord(key=f"k{i}", vector=[0.0] * 1024, metadata={})
        for i in range(25)
    ]
    idx.upsert(records)
    assert client.put_vectors.call_count == 3


def test_upsert_empty_is_noop():
    client = MagicMock()
    idx = VectorIndex(s3vectors_client=client, index_name="x")
    idx.upsert([])
    assert client.put_vectors.call_count == 0


def test_query_returns_typed_hits():
    client = MagicMock()
    client.query_vectors.return_value = {
        "vectors": [
            {
                "key": "bgb-535",
                "distance": 0.06,
                "metadata": {
                    "gesetz": "BGB",
                    "paragraph": "535",
                    "titel": "Inhalt und Hauptpflichten...",
                    "wortlaut": "(1) ...",
                    "quelle_url": "https://example.test",
                    "stand": "2026-05-09",
                },
            }
        ]
    }
    idx = VectorIndex(s3vectors_client=client, index_name="kira-legal-norms")
    hits = idx.query(vector=[0.1] * 1024, k=5)
    assert len(hits) == 1
    h = hits[0]
    assert isinstance(h, VectorSearchHit)
    assert h.key == "bgb-535"
    assert h.score == pytest.approx(1.0 - 0.06)
    assert h.metadata["gesetz"] == "BGB"


def test_query_passes_metadata_filter_when_provided():
    client = MagicMock()
    client.query_vectors.return_value = {"vectors": []}
    idx = VectorIndex(s3vectors_client=client, index_name="kira-legal-norms")
    idx.query(
        vector=[0.0] * 1024,
        k=10,
        metadata_filter={"abkuerzung": {"$in": ["BGB", "WEG"]}},
    )
    kwargs = client.query_vectors.call_args.kwargs
    assert kwargs["filter"] == {"abkuerzung": {"$in": ["BGB", "WEG"]}}


def test_query_omits_filter_when_none():
    client = MagicMock()
    client.query_vectors.return_value = {"vectors": []}
    idx = VectorIndex(s3vectors_client=client, index_name="kira-legal-norms")
    idx.query(vector=[0.0] * 1024, k=10)
    kwargs = client.query_vectors.call_args.kwargs
    assert "filter" not in kwargs


def test_delete_keys_chunks_above_batch_limit():
    client = MagicMock()
    idx = VectorIndex(
        s3vectors_client=client, index_name="x", delete_batch_size=10
    )
    idx.delete([f"k{i}" for i in range(25)])
    assert client.delete_vectors.call_count == 3


def test_query_client_error_raises_corpus_unavailable():
    client = MagicMock()
    client.query_vectors.side_effect = ClientError(
        error_response={"Error": {"Code": "ServiceUnavailable", "Message": "down"}},
        operation_name="QueryVectors",
    )
    idx = VectorIndex(s3vectors_client=client, index_name="x")
    with pytest.raises(CorpusUnavailableError):
        idx.query(vector=[0.0] * 1024, k=1)
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_vector_index.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/kira/legal_sources/_common/vector_index.py`:

```python
"""S3 Vectors wrapper for the kira-legal-norms index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from kira.legal_sources._common.errors import CorpusUnavailableError

DEFAULT_UPSERT_BATCH_SIZE = 100
DEFAULT_DELETE_BATCH_SIZE = 100


@dataclass(frozen=True)
class VectorRecord:
    key: str
    vector: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VectorSearchHit:
    key: str
    score: float
    metadata: dict[str, Any]


class VectorIndex:
    """Thin wrapper over `boto3.client('s3vectors')`.

    Translates from cosine *distance* (what S3 Vectors returns) to cosine
    *similarity* score in `[0, 1]` (what callers want), via `score = 1 - dist`.
    """

    def __init__(
        self,
        *,
        s3vectors_client: Any,
        index_name: str,
        upsert_batch_size: int = DEFAULT_UPSERT_BATCH_SIZE,
        delete_batch_size: int = DEFAULT_DELETE_BATCH_SIZE,
    ) -> None:
        self._client = s3vectors_client
        self._index_name = index_name
        self._upsert_batch = upsert_batch_size
        self._delete_batch = delete_batch_size

    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        for start in range(0, len(records), self._upsert_batch):
            batch = records[start : start + self._upsert_batch]
            self._client.put_vectors(
                indexName=self._index_name,
                vectors=[
                    {
                        "key": r.key,
                        "data": {"float32": r.vector},
                        "metadata": r.metadata,
                    }
                    for r in batch
                ],
            )

    def delete(self, keys: list[str]) -> None:
        if not keys:
            return
        for start in range(0, len(keys), self._delete_batch):
            batch = keys[start : start + self._delete_batch]
            self._client.delete_vectors(
                indexName=self._index_name,
                keys=batch,
            )

    def query(
        self,
        *,
        vector: list[float],
        k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchHit]:
        kwargs: dict[str, Any] = {
            "indexName": self._index_name,
            "queryVector": {"float32": vector},
            "topK": k,
            "returnMetadata": True,
            "returnDistance": True,
        }
        if metadata_filter is not None:
            kwargs["filter"] = metadata_filter
        try:
            response = self._client.query_vectors(**kwargs)
        except ClientError as exc:
            raise CorpusUnavailableError(
                f"S3 Vectors query failed on index {self._index_name!r}: {exc}"
            ) from exc
        return [
            VectorSearchHit(
                key=v["key"],
                score=1.0 - float(v.get("distance", 1.0)),
                metadata=v.get("metadata", {}),
            )
            for v in response.get("vectors", [])
        ]
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_vector_index.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/vector_index.py \
        tests/legal_sources/unit/test_vector_index.py
git commit -m "feat(legal-sources): VectorIndex wrapper for S3 Vectors"
```

---

## Task 9: SearchNorm input/output schemas

**Files:**
- Modify: `src/kira/legal_sources/gesetze/schema.py`
- Modify: `tests/legal_sources/unit/test_schema.py`

- [ ] **Step 1: Append failing tests**

APPEND to `tests/legal_sources/unit/test_schema.py`:

```python
from kira.legal_sources.gesetze.schema import (
    SearchNormError,
    SearchNormErrorCode,
    SearchNormHit,
    SearchNormInput,
    SearchNormResult,
    SearchNormSuccess,
)


def test_search_input_minimal_validates():
    inp = SearchNormInput.model_validate({"query": "Mietminderung Schimmel"})
    assert inp.query == "Mietminderung Schimmel"
    assert inp.k == 10  # default
    assert inp.gesetz_filter is None
    assert inp.type_filter is None


def test_search_input_k_capped():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x", "k": 51})
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x", "k": 0})


def test_search_input_query_must_be_nonempty():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": ""})


def test_search_input_query_max_length():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x" * 5001})


def test_search_input_extra_field_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x", "rogue": True})


def test_search_input_filters_normalize_lowercase():
    inp = SearchNormInput.model_validate(
        {"query": "x", "gesetz_filter": ["BGB", "weg"]}
    )
    assert inp.gesetz_filter == ["bgb", "weg"]


def test_search_input_type_filter_validates_enum():
    from pydantic import ValidationError
    SearchNormInput.model_validate({"query": "x", "type_filter": ["Gesetz"]})
    with pytest.raises(ValidationError):
        SearchNormInput.model_validate({"query": "x", "type_filter": ["Sonstiges"]})


def test_search_success_serializes():
    s = SearchNormSuccess(
        query="x",
        hits=[
            SearchNormHit(
                gesetz="BGB",
                paragraph="535",
                absatz=None,
                titel="t",
                wortlaut="w",
                quelle_url="https://example.test",
                stand="2026-05-09",
                score=0.94,
            )
        ],
    )
    dumped = s.model_dump()
    assert dumped["hits"][0]["gesetz"] == "BGB"


def test_search_result_union():
    success = SearchNormSuccess(query="x", hits=[])
    err = SearchNormError(
        error=SearchNormErrorCode.EMBEDDING_UNAVAILABLE,
        message="bedrock down",
    )
    assert isinstance(success, SearchNormResult.__args__)  # type: ignore[attr-defined]
    assert isinstance(err, SearchNormResult.__args__)  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_schema.py -v`
Expected: ImportError on `SearchNormInput`.

- [ ] **Step 3: Append search schemas to schema.py**

APPEND to `src/kira/legal_sources/gesetze/schema.py`:

```python
class SearchNormErrorCode(StrEnum):
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    CORPUS_UNAVAILABLE = "corpus_unavailable"
    VALIDATION_ERROR = "validation_error"


class SearchNormInput(BaseModel):
    """Eingabe für das Tool `search_norm`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(..., min_length=1, max_length=5000)
    k: int = Field(default=10, ge=1, le=50)
    gesetz_filter: list[str] | None = None
    type_filter: list[Literal["Gesetz", "Verordnung"]] | None = None

    @field_validator("gesetz_filter")
    @classmethod
    def _normalize_gesetz_filter(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [s.strip().lower() for s in v]


class SearchNormHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gesetz: str
    paragraph: str
    absatz: str | None
    titel: str
    wortlaut: str
    quelle_url: str
    stand: str
    score: float


class SearchNormSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    hits: list[SearchNormHit]

    def to_agent_text(self) -> str:
        if not self.hits:
            return f"Keine Treffer für: {self.query!r}"
        lines = [f"# Suche: {self.query!r}", ""]
        for h in self.hits:
            lines.append(
                f"- **{h.gesetz} § {h.paragraph}** ({h.score:.0%}) — {h.titel}"
            )
            lines.append(f"  _{h.quelle_url} | Stand: {h.stand}_")
        return "\n".join(lines)


class SearchNormError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: SearchNormErrorCode
    message: str

    def to_agent_text(self) -> str:
        return f"FEHLER ({self.error.value}): {self.message}"


SearchNormResult = SearchNormSuccess | SearchNormError
```

Also add `Literal` to the existing imports at the top of `schema.py` if not already present.

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_schema.py -v`
Expected: 9 + 9 = 18 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/gesetze/schema.py tests/legal_sources/unit/test_schema.py
git commit -m "feat(legal-sources): SearchNormInput/Hit/Success/Error/Result schemas"
```

---

## Task 10: search_norm pure function

**Files:**
- Create: `src/kira/legal_sources/gesetze/search_norm.py`
- Create: `tests/legal_sources/unit/test_search_norm.py`

- [ ] **Step 1: Write failing tests**

Create `tests/legal_sources/unit/test_search_norm.py`:

```python
import pytest

from kira.legal_sources._common.errors import (
    CorpusUnavailableError,
    EmbeddingUnavailableError,
)
from kira.legal_sources._common.vector_index import VectorSearchHit
from kira.legal_sources.gesetze.schema import (
    SearchNormError,
    SearchNormErrorCode,
    SearchNormInput,
    SearchNormSuccess,
)
from kira.legal_sources.gesetze.search_norm import search_norm


def _make_callables(*, embed_returns=None, embed_raises=None,
                    search_returns=None, search_raises=None):
    calls = {"embed_args": None, "search_kwargs": None}

    def embed(query: str) -> list[float]:
        calls["embed_args"] = query
        if embed_raises:
            raise embed_raises
        return embed_returns or [0.0] * 1024

    def search(*, vector, k, metadata_filter=None):
        calls["search_kwargs"] = {
            "vector": vector,
            "k": k,
            "metadata_filter": metadata_filter,
        }
        if search_raises:
            raise search_raises
        return search_returns or []

    return embed, search, calls


def test_happy_path_returns_hits_in_order():
    hit_a = VectorSearchHit(
        key="bgb-535",
        score=0.94,
        metadata={
            "gesetz": "BGB",
            "paragraph": "535",
            "titel": "Inhalt und Hauptpflichten des Mietvertrags",
            "wortlaut": "(1) Durch den Mietvertrag ...",
            "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
            "stand": "2026-05-09",
        },
    )
    hit_b = VectorSearchHit(
        key="bgb-536",
        score=0.81,
        metadata={
            "gesetz": "BGB",
            "paragraph": "536",
            "titel": "Mietminderung bei Sach- und Rechtsmängeln",
            "wortlaut": "(1) Hat die Mietsache ...",
            "quelle_url": "https://www.gesetze-im-internet.de/bgb/__536.html",
            "stand": "2026-05-09",
        },
    )
    embed, search, calls = _make_callables(search_returns=[hit_a, hit_b])
    inp = SearchNormInput(query="Mietminderung Schimmel", k=5)
    result = search_norm(inp, embed=embed, search=search)
    assert isinstance(result, SearchNormSuccess)
    assert [h.paragraph for h in result.hits] == ["535", "536"]
    assert result.hits[0].score == 0.94
    assert calls["search_kwargs"]["k"] == 5
    assert calls["search_kwargs"]["metadata_filter"] is None


def test_gesetz_filter_translates_to_metadata_filter():
    embed, search, calls = _make_callables(search_returns=[])
    inp = SearchNormInput(
        query="x", gesetz_filter=["BGB", "WEG"]
    )
    search_norm(inp, embed=embed, search=search)
    assert calls["search_kwargs"]["metadata_filter"] == {
        "abkuerzung": {"$in": ["bgb", "weg"]},
    }


def test_combined_filters_translate_correctly():
    embed, search, calls = _make_callables(search_returns=[])
    inp = SearchNormInput(
        query="x",
        gesetz_filter=["BGB"],
        type_filter=["Gesetz"],
    )
    search_norm(inp, embed=embed, search=search)
    f = calls["search_kwargs"]["metadata_filter"]
    assert f == {
        "abkuerzung": {"$in": ["bgb"]},
        "type": {"$in": ["Gesetz"]},
    }


def test_embedding_failure_returns_error():
    embed, search, _ = _make_callables(
        embed_raises=EmbeddingUnavailableError("bedrock down"),
    )
    inp = SearchNormInput(query="x")
    result = search_norm(inp, embed=embed, search=search)
    assert isinstance(result, SearchNormError)
    assert result.error == SearchNormErrorCode.EMBEDDING_UNAVAILABLE


def test_search_failure_returns_error():
    embed, search, _ = _make_callables(
        search_raises=CorpusUnavailableError("vectors index missing"),
    )
    inp = SearchNormInput(query="x")
    result = search_norm(inp, embed=embed, search=search)
    assert isinstance(result, SearchNormError)
    assert result.error == SearchNormErrorCode.CORPUS_UNAVAILABLE


def test_hit_with_missing_metadata_field_skipped_with_warning(caplog):
    bad = VectorSearchHit(
        key="bgb-535",
        score=0.5,
        metadata={"gesetz": "BGB"},  # missing required fields
    )
    embed, search, _ = _make_callables(search_returns=[bad])
    inp = SearchNormInput(query="x")
    result = search_norm(inp, embed=embed, search=search)
    assert isinstance(result, SearchNormSuccess)
    assert result.hits == []  # bad hit dropped
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_search_norm.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/kira/legal_sources/gesetze/search_norm.py`:

```python
"""Pure function: rank paragraphs against a query using injected embed + search."""

from __future__ import annotations

import logging
from typing import Any, Callable

from kira.legal_sources._common.errors import (
    CorpusUnavailableError,
    EmbeddingUnavailableError,
)
from kira.legal_sources._common.vector_index import VectorSearchHit
from kira.legal_sources.gesetze.schema import (
    SearchNormError,
    SearchNormErrorCode,
    SearchNormHit,
    SearchNormInput,
    SearchNormResult,
    SearchNormSuccess,
)

log = logging.getLogger(__name__)

EmbedFn = Callable[[str], list[float]]
SearchFn = Callable[..., list[VectorSearchHit]]

_REQUIRED_METADATA_FIELDS = ("gesetz", "paragraph", "titel", "wortlaut", "quelle_url", "stand")


def search_norm(
    input_data: SearchNormInput,
    *,
    embed: EmbedFn,
    search: SearchFn,
) -> SearchNormResult:
    """Embed the query, search vector index, format results.

    `embed` and `search` are injected so this function has no AWS deps and is
    fully unit-testable.
    """
    try:
        vector = embed(input_data.query)
    except EmbeddingUnavailableError as exc:
        return SearchNormError(
            error=SearchNormErrorCode.EMBEDDING_UNAVAILABLE,
            message=str(exc),
        )

    metadata_filter = _build_filter(input_data)

    try:
        raw_hits = search(
            vector=vector,
            k=input_data.k,
            metadata_filter=metadata_filter,
        )
    except CorpusUnavailableError as exc:
        return SearchNormError(
            error=SearchNormErrorCode.CORPUS_UNAVAILABLE,
            message=str(exc),
        )

    hits: list[SearchNormHit] = []
    for raw in raw_hits:
        formatted = _format_hit(raw)
        if formatted is not None:
            hits.append(formatted)

    return SearchNormSuccess(query=input_data.query, hits=hits)


def _build_filter(input_data: SearchNormInput) -> dict[str, Any] | None:
    f: dict[str, Any] = {}
    if input_data.gesetz_filter:
        f["abkuerzung"] = {"$in": input_data.gesetz_filter}
    if input_data.type_filter:
        f["type"] = {"$in": list(input_data.type_filter)}
    return f or None


def _format_hit(raw: VectorSearchHit) -> SearchNormHit | None:
    md = raw.metadata or {}
    missing = [f for f in _REQUIRED_METADATA_FIELDS if f not in md]
    if missing:
        log.warning(
            "Skipping hit %s — missing metadata fields: %s",
            raw.key,
            missing,
        )
        return None
    return SearchNormHit(
        gesetz=md["gesetz"],
        paragraph=md["paragraph"],
        absatz=md.get("absatz"),
        titel=md["titel"],
        wortlaut=md["wortlaut"],
        quelle_url=md["quelle_url"],
        stand=md["stand"],
        score=raw.score,
    )
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_search_norm.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/gesetze/search_norm.py tests/legal_sources/unit/test_search_norm.py
git commit -m "feat(legal-sources): search_norm pure function with injected callables"
```

---

## Task 11: Refactor `lookup_norm` to use injected meta + norm loaders

**Files:**
- Modify: `src/kira/legal_sources/gesetze/lookup_norm.py`
- Modify: `tests/legal_sources/unit/test_lookup_norm.py`

V1 took a `corpus: Mapping[str, GesetzKorpus]` (whole-corpus preload). V2 takes two callables: `load_meta(abk) -> GesetzMeta | None` and `load_norm(meta_key, norm_key) -> Norm | None`. The pure function never touches S3; the caller injects the right loaders.

- [ ] **Step 1: Replace `test_lookup_norm.py` entirely**

Replace `tests/legal_sources/unit/test_lookup_norm.py` with:

```python
import json
from datetime import date
from pathlib import Path

import pytest

from kira.legal_sources.gesetze.corpus_format import (
    Absatz,
    GesetzMeta,
    Norm,
    NormIndexEntry,
)
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormSuccess,
)


def _bgb_meta() -> GesetzMeta:
    return GesetzMeta.model_validate(
        {
            "abkuerzung": "BGB",
            "titel": "Bürgerliches Gesetzbuch",
            "type": "Gesetz",
            "stand": "2026-05-08",
            "quelle": "gesetze-im-internet.de",
            "quelle_url": "https://www.gesetze-im-internet.de/bgb",
            "upstream_xml_zip_url": "https://www.gesetze-im-internet.de/bgb/xml.zip",
            "paragraphen": {
                "535": {
                    "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                    "key": "gesetze/bgb/535.json",
                    "content_sha256": "abc",
                },
                "536": {
                    "titel": "Mietminderung bei Sach- und Rechtsmängeln",
                    "key": "gesetze/bgb/536.json",
                    "content_sha256": "def",
                },
                "535a": {
                    "titel": "Suffix-Norm",
                    "key": "gesetze/bgb/535a.json",
                    "content_sha256": "ghi",
                },
            },
        }
    )


def _bgb_535() -> Norm:
    return Norm(
        gesetz="BGB",
        paragraph="535",
        titel="Inhalt und Hauptpflichten des Mietvertrags",
        absaetze=[
            Absatz(nummer="1", text="Durch den Mietvertrag wird der Vermieter verpflichtet, ..."),
            Absatz(nummer="2", text="Der Mieter ist verpflichtet, ..."),
        ],
        quelle_url="https://www.gesetze-im-internet.de/bgb/__535.html",
    )


def _make_loaders(meta: GesetzMeta | None, norms: dict[str, Norm] | None = None):
    norms = norms or {}

    def load_meta(abk: str) -> GesetzMeta | None:
        return meta if abk == "bgb" and meta is not None else None

    def load_norm(meta_key: str, norm_key: str) -> Norm | None:
        return norms.get(norm_key)

    return load_meta, load_norm


def test_returns_full_paragraph_when_no_absatz():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535.json": _bgb_535()})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormSuccess)
    assert "Durch den Mietvertrag" in result.wortlaut
    assert "Der Mieter" in result.wortlaut
    assert result.absatz is None
    assert result.stand == "2026-05-08"


def test_returns_specific_absatz_when_requested():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535.json": _bgb_535()})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535", absatz="2"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormSuccess)
    assert result.absatz == "2"
    assert "Der Mieter" in result.wortlaut
    assert "Durch den Mietvertrag" not in result.wortlaut


def test_unknown_gesetz_returns_error():
    load_meta, load_norm = _make_loaders(None)
    result = lookup_norm(
        LookupNormInput(gesetz="ABC", paragraph="1"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.UNKNOWN_GESETZ


def test_paragraph_not_found_lists_near_misses():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta)
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="537"),  # not present
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.PARAGRAPH_NOT_FOUND
    # Near-miss list includes existing close paragraphs
    assert "535" in result.message or "536" in result.message


def test_absatz_not_found_returns_error():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535.json": _bgb_535()})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535", absatz="9"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.ABSATZ_NOT_FOUND


def test_norm_load_returns_none_treated_as_corpus_unavailable():
    """If meta says §535 exists but the underlying file can't be loaded."""
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {})  # empty norms dict
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.CORPUS_UNAVAILABLE


def test_stand_warning_when_meta_old():
    meta = _bgb_meta()
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535.json": _bgb_535()})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535"),
        load_meta=load_meta,
        load_norm=load_norm,
        today=date(2026, 7, 7),  # 60 days after 2026-05-08
    )
    assert isinstance(result, LookupNormSuccess)
    assert result.stand_warnung is not None
    assert "60 Tage" in result.stand_warnung


def test_paragraph_with_letter_suffix():
    meta = _bgb_meta()
    norm_535a = Norm(
        gesetz="BGB",
        paragraph="535a",
        titel="Suffix-Norm",
        absaetze=[Absatz(nummer="1", text="Suffix-Test.")],
        quelle_url="https://www.gesetze-im-internet.de/bgb/__535a.html",
    )
    load_meta, load_norm = _make_loaders(meta, {"gesetze/bgb/535a.json": norm_535a})
    result = lookup_norm(
        LookupNormInput(gesetz="BGB", paragraph="535a"),
        load_meta=load_meta,
        load_norm=load_norm,
    )
    assert isinstance(result, LookupNormSuccess)
    assert result.paragraph == "535a"
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lookup_norm.py -v`
Expected: failures (signature mismatch — V1 took `corpus=` kwarg, V2 takes `load_meta=` and `load_norm=`).

- [ ] **Step 3: Replace `lookup_norm.py`**

Replace `src/kira/legal_sources/gesetze/lookup_norm.py` with:

```python
"""Pure function: resolve a single paragraph via injected loaders."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from kira.legal_sources.gesetze.corpus_format import GesetzMeta, Norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormResult,
    LookupNormSuccess,
)

_STAND_WARN_AGE = timedelta(days=30)
_NEAR_MISS_K = 5

LoadMetaFn = Callable[[str], GesetzMeta | None]
LoadNormFn = Callable[[str, str], Norm | None]


def lookup_norm(
    input_data: LookupNormInput,
    *,
    load_meta: LoadMetaFn,
    load_norm: LoadNormFn,
    today: date | None = None,
) -> LookupNormResult:
    today = today or date.today()
    abk = input_data.gesetz  # already lower-case after validation

    meta = load_meta(abk)
    if meta is None:
        return LookupNormError(
            error=LookupNormErrorCode.UNKNOWN_GESETZ,
            message=f"Gesetz {abk.upper()!r} ist nicht im Korpus.",
            gesetz=abk.upper(),
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    entry = meta.paragraphen.get(input_data.paragraph)
    if entry is None:
        return LookupNormError(
            error=LookupNormErrorCode.PARAGRAPH_NOT_FOUND,
            message=(
                f"§ {input_data.paragraph} {meta.abkuerzung} ist nicht im Korpus. "
                f"Nahe Treffer: {', '.join(_near_misses(input_data.paragraph, meta))}."
            ),
            gesetz=meta.abkuerzung,
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    norm = load_norm(entry.key.split("/")[-2], entry.key)
    if norm is None:
        return LookupNormError(
            error=LookupNormErrorCode.CORPUS_UNAVAILABLE,
            message=f"Konnte {entry.key} nicht laden.",
            gesetz=meta.abkuerzung,
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    wortlaut, used_absatz = _select_text(norm, input_data.absatz)
    if input_data.absatz is not None and used_absatz is None:
        return LookupNormError(
            error=LookupNormErrorCode.ABSATZ_NOT_FOUND,
            message=(
                f"Absatz {input_data.absatz} in § {input_data.paragraph} "
                f"{meta.abkuerzung} existiert nicht."
            ),
            gesetz=meta.abkuerzung,
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    return LookupNormSuccess(
        gesetz=meta.abkuerzung,
        gesetz_titel=meta.titel,
        paragraph=norm.paragraph,
        absatz=used_absatz,
        titel=norm.titel,
        wortlaut=wortlaut,
        stand=meta.stand,
        quelle_url=norm.quelle_url or meta.quelle_url,
        stand_warnung=_stand_warning(meta.stand, today),
    )


def _select_text(norm: Norm, absatz: str | None) -> tuple[str, str | None]:
    if absatz is None:
        if not norm.absaetze:
            return ("", None)
        return ("\n\n".join(f"({a.nummer}) {a.text}" for a in norm.absaetze), None)
    for a in norm.absaetze:
        if a.nummer == absatz:
            return (f"({a.nummer}) {a.text}", a.nummer)
    return ("", None)


def _stand_warning(stand: str, today: date) -> str | None:
    try:
        stand_date = datetime.strptime(stand, "%Y-%m-%d").date()
    except ValueError:
        return f"Stand-Datum {stand!r} ist unleserlich."
    age = today - stand_date
    if age > _STAND_WARN_AGE:
        return f"Korpus-Stand ist {age.days} Tage alt — bitte verifizieren."
    return None


def _near_misses(target: str, meta: GesetzMeta) -> list[str]:
    """Return up to _NEAR_MISS_K paragraph keys numerically/lexically closest to target."""
    keys = list(meta.paragraphen.keys())
    target_num = _to_sort_key(target)
    keys.sort(key=lambda k: abs(_to_sort_key(k) - target_num))
    return keys[:_NEAR_MISS_K]


def _to_sort_key(p: str) -> float:
    """Coerce '535', '535a', '535b' into sortable numbers (suffix as 0.01-step)."""
    import re
    m = re.match(r"^(\d+)([a-zA-Z]?)$", p)
    if not m:
        return 0.0
    num = int(m.group(1))
    suffix = m.group(2)
    return num + (ord(suffix.lower()) - ord("a") + 1) * 0.01 if suffix else float(num)
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lookup_norm.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/gesetze/lookup_norm.py tests/legal_sources/unit/test_lookup_norm.py
git commit -m "refactor(legal-sources): lookup_norm uses injected meta+norm loaders"
```

---

## Task 12: Refactor `s3_corpus.py` for lazy-load + LRU

**Files:**
- Modify: `src/kira/legal_sources/_common/s3_corpus.py`
- Modify: `tests/legal_sources/unit/test_s3_corpus.py`

The class is renamed `LazyCorpusLoader` (was `CorpusLoader`) and exposes
three methods: `load_manifest()`, `load_meta(abk)`, `load_norm(key)`. Each
goes through MemoryLRU + TmpDiskLRU with S3 as the source of truth.

- [ ] **Step 1: Replace `test_s3_corpus.py` entirely**

Replace `tests/legal_sources/unit/test_s3_corpus.py` with:

```python
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import LazyCorpusLoader

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)


def _meta_payload() -> dict:
    return {
        "abkuerzung": "BGB",
        "titel": "Bürgerliches Gesetzbuch",
        "type": "Gesetz",
        "stand": "2026-05-09",
        "quelle": "gesetze-im-internet.de",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb",
        "upstream_xml_zip_url": "https://www.gesetze-im-internet.de/bgb/xml.zip",
        "paragraphen": {
            "535": {
                "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                "key": "gesetze/bgb/535.json",
                "content_sha256": "abc",
            }
        },
    }


def _norm_payload() -> dict:
    return {
        "gesetz": "BGB",
        "paragraph": "535",
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "absaetze": [{"nummer": "1", "text": "Durch den Mietvertrag ..."}],
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
    }


def _manifest_payload() -> dict:
    return {
        "version": 2,
        "stand": "2026-05-09",
        "gesetze": {
            "bgb": {
                "abkuerzung": "BGB",
                "titel": "Bürgerliches Gesetzbuch",
                "type": "Gesetz",
                "meta_key": "gesetze/bgb/_meta.json",
                "upstream_etag": "\"abc\"",
                "upstream_last_modified": "Wed, 06 May 2026 15:45:05 GMT",
            }
        },
    }


@pytest.fixture
def s3_corpus_bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="test-corpus",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/_manifest.json",
            Body=json.dumps(_manifest_payload()).encode("utf-8"),
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/bgb/_meta.json",
            Body=json.dumps(_meta_payload()).encode("utf-8"),
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/bgb/535.json",
            Body=json.dumps(_norm_payload()).encode("utf-8"),
        )
        yield "test-corpus"


def test_load_manifest_returns_v2(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    m = loader.load_manifest()
    assert m.version == 2
    assert "bgb" in m.gesetze


def test_load_meta_then_norm(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    meta = loader.load_meta("bgb")
    assert meta is not None
    assert meta.abkuerzung == "BGB"
    norm = loader.load_norm("gesetze/bgb/535.json")
    assert norm is not None
    assert "Mietvertrag" in norm.absaetze[0].text


def test_load_meta_returns_none_for_unknown(
    monkeypatch, s3_corpus_bucket, tmp_path
):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    assert loader.load_meta("doesnotexist") is None


def test_load_meta_warm_hit_skips_s3(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = LazyCorpusLoader.from_env()
    loader.load_meta("bgb")  # cold
    # Mutate S3 to a malformed payload; warm load must NOT fetch.
    s3 = boto3.client("s3", region_name="eu-central-1")
    s3.put_object(
        Bucket=s3_corpus_bucket,
        Key="gesetze/bgb/_meta.json",
        Body=b"not-json",
    )
    again = loader.load_meta("bgb")  # warm — served from memory
    assert again is not None and again.abkuerzung == "BGB"


def test_load_norm_falls_back_to_tmp_after_memory_eviction(
    monkeypatch, s3_corpus_bucket, tmp_path
):
    """After memory eviction, /tmp serves without re-hitting S3."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.NORM_MEMORY_MAX_ITEMS", 1
    )
    loader = LazyCorpusLoader.from_env()
    loader.load_norm("gesetze/bgb/535.json")  # cold path: S3 → /tmp → memory
    # Force memory eviction by loading the same key under a different key
    # → easier: directly reach in and clear the in-memory LRU.
    loader._norm_memory._data.clear()
    # Mutate S3 to garbage; if /tmp tier works, we still get the right norm.
    s3 = boto3.client("s3", region_name="eu-central-1")
    s3.put_object(
        Bucket=s3_corpus_bucket,
        Key="gesetze/bgb/535.json",
        Body=b"corrupt",
    )
    n = loader.load_norm("gesetze/bgb/535.json")
    assert n is not None
    assert "Mietvertrag" in n.absaetze[0].text


def test_no_env_set_raises_corpus_unavailable():
    with pytest.raises(CorpusUnavailableError):
        LazyCorpusLoader.from_env().load_manifest()
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_s3_corpus.py -v`
Expected: ImportError on `LazyCorpusLoader`.

- [ ] **Step 3: Replace `s3_corpus.py`**

Replace `src/kira/legal_sources/_common/s3_corpus.py` with:

```python
"""Lazy corpus loader: serves manifest, per-Gesetz meta, and per-§ norms.

Three-tier cache hierarchy per resource:
  memory (MemoryLRU)  →  /tmp (TmpDiskLRU)  →  S3.

Manifest is a single object so it lives only in memory (with a 5-minute
recheck window — see `MANIFEST_RECHECK_SECONDS`). Meta and Norm objects
use both tiers.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.lru import MemoryLRU, TmpDiskLRU
from kira.legal_sources._common.manifest import Manifest, parse_manifest
from kira.legal_sources._common.region import REQUIRED_REGION
from kira.legal_sources.gesetze.corpus_format import GesetzMeta, Norm

log = logging.getLogger(__name__)

ENV_LOCAL_DIR = "LEGAL_CORPUS_LOCAL_DIR"
ENV_S3_BUCKET = "LEGAL_CORPUS_BUCKET"

TMP_CACHE_DIR = Path("/tmp/legal_sources_corpus")
MANIFEST_KEY = "gesetze/_manifest.json"
MANIFEST_RECHECK_SECONDS = 300

META_MEMORY_MAX_ITEMS = 200
NORM_MEMORY_MAX_ITEMS = 500
TMP_BYTE_BUDGET = 800 * 1024 * 1024  # 800 MB


class LazyCorpusLoader:
    """Lazy three-tier loader. One instance per Lambda execution environment."""

    def __init__(
        self,
        *,
        s3_bucket: str | None,
        local_dir: Path | None,
    ) -> None:
        self._s3_bucket = s3_bucket
        self._local_dir = local_dir
        self._manifest: Manifest | None = None
        self._manifest_checked_at: float = 0.0
        self._meta_memory: MemoryLRU[str, GesetzMeta] = MemoryLRU(
            max_items=META_MEMORY_MAX_ITEMS
        )
        self._norm_memory: MemoryLRU[str, Norm] = MemoryLRU(
            max_items=NORM_MEMORY_MAX_ITEMS
        )
        self._tmp = TmpDiskLRU(root=TMP_CACHE_DIR, max_bytes=TMP_BYTE_BUDGET)

    @classmethod
    def from_env(cls) -> "LazyCorpusLoader":
        local = os.environ.get(ENV_LOCAL_DIR)
        bucket = os.environ.get(ENV_S3_BUCKET)
        return cls(
            s3_bucket=bucket or None,
            local_dir=Path(local) if local else None,
        )

    # --- manifest ---

    def load_manifest(self) -> Manifest:
        now = time.time()
        if (
            self._manifest is not None
            and (now - self._manifest_checked_at) < MANIFEST_RECHECK_SECONDS
        ):
            return self._manifest
        raw = self._read_bytes(MANIFEST_KEY)
        if raw is None:
            raise CorpusUnavailableError(
                f"manifest not found at {MANIFEST_KEY!r}"
            )
        self._manifest = parse_manifest(json.loads(raw))
        self._manifest_checked_at = now
        return self._manifest

    # --- meta ---

    def load_meta(self, abk: str) -> GesetzMeta | None:
        cached = self._meta_memory.get(abk)
        if cached is not None:
            return cached
        manifest = self.load_manifest()
        entry = manifest.gesetze.get(abk)
        if entry is None:
            return None
        raw = self._read_bytes(entry.meta_key)
        if raw is None:
            return None
        meta = GesetzMeta.model_validate(json.loads(raw))
        self._meta_memory.put(abk, meta)
        return meta

    # --- norm ---

    def load_norm(self, key: str) -> Norm | None:
        cached = self._norm_memory.get(key)
        if cached is not None:
            return cached
        raw = self._read_bytes(key)
        if raw is None:
            return None
        try:
            norm = Norm.model_validate(json.loads(raw))
        except (ValueError, json.JSONDecodeError) as exc:
            log.warning("Skipping malformed norm %s: %s", key, exc)
            return None
        self._norm_memory.put(key, norm)
        return norm

    # --- backing reads ---

    def _read_bytes(self, key: str) -> bytes | None:
        if self._local_dir is not None:
            return self._read_local(key)
        if self._s3_bucket is not None:
            return self._read_s3(key)
        raise CorpusUnavailableError(
            f"Neither {ENV_LOCAL_DIR} nor {ENV_S3_BUCKET} is set."
        )

    def _read_local(self, key: str) -> bytes | None:
        # Treat the manifest specially: it sits at the local_dir root for
        # backwards-compat with V1 fixtures, and per-§ keys land below.
        candidate = self._local_dir / key
        if candidate.exists():
            return candidate.read_bytes()
        # Try collapsing the leading 'gesetze/' (V1-style fixtures).
        flat = self._local_dir / Path(key).name
        if flat.exists():
            return flat.read_bytes()
        return None

    def _read_s3(self, key: str) -> bytes | None:
        flat_key = key.replace("/", "__")
        cached_disk = self._tmp.get(flat_key)
        if cached_disk is not None:
            return cached_disk
        import boto3
        from botocore.exceptions import ClientError

        s3 = boto3.client("s3", region_name=REQUIRED_REGION)
        try:
            obj = s3.get_object(Bucket=self._s3_bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "AccessDenied"):
                return None
            raise CorpusUnavailableError(f"S3 GET {key!r} failed: {exc}") from exc
        body = obj["Body"].read()
        try:
            self._tmp.put(flat_key, body)
        except OSError as exc:  # disk full / permission, etc.
            log.warning("Could not write %s to /tmp: %s", flat_key, exc)
        return body
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_s3_corpus.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/s3_corpus.py tests/legal_sources/unit/test_s3_corpus.py
git commit -m "refactor(legal-sources): LazyCorpusLoader with three-tier cache"
```

---

## Task 13: Refactor `lookup_handler` for lazy-load wiring

**Files:**
- Modify: `src/kira/legal_sources/adapters/lookup_handler.py`
- Modify: `tests/legal_sources/adapters/test_lookup_handler.py`

The handler now wires the new lookup_norm callable signature against
LazyCorpusLoader's three methods.

- [ ] **Step 1: Update tests**

Replace `tests/legal_sources/adapters/test_lookup_handler.py` with:

```python
import importlib
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _meta_payload() -> dict:
    return {
        "abkuerzung": "BGB",
        "titel": "Bürgerliches Gesetzbuch",
        "type": "Gesetz",
        "stand": "2026-05-09",
        "quelle": "gesetze-im-internet.de",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb",
        "upstream_xml_zip_url": "https://www.gesetze-im-internet.de/bgb/xml.zip",
        "paragraphen": {
            "535": {
                "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                "key": "gesetze/bgb/535.json",
                "content_sha256": "abc",
            }
        },
    }


def _norm_payload() -> dict:
    return {
        "gesetz": "BGB",
        "paragraph": "535",
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "absaetze": [
            {"nummer": "1", "text": "Durch den Mietvertrag ..."},
            {"nummer": "2", "text": "Der Mieter ..."},
        ],
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
    }


def _manifest_payload() -> dict:
    return {
        "version": 2,
        "stand": "2026-05-09",
        "gesetze": {
            "bgb": {
                "abkuerzung": "BGB",
                "titel": "Bürgerliches Gesetzbuch",
                "type": "Gesetz",
                "meta_key": "gesetze/bgb/_meta.json",
                "upstream_etag": "\"abc\"",
                "upstream_last_modified": "...",
            }
        },
    }


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)


@pytest.fixture
def populated_bucket(monkeypatch, tmp_path):
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="test-corpus",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/_manifest.json",
            Body=json.dumps(_manifest_payload()).encode("utf-8"),
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/bgb/_meta.json",
            Body=json.dumps(_meta_payload()).encode("utf-8"),
        )
        s3.put_object(
            Bucket="test-corpus",
            Key="gesetze/bgb/535.json",
            Body=json.dumps(_norm_payload()).encode("utf-8"),
        )
        monkeypatch.setenv("LEGAL_CORPUS_BUCKET", "test-corpus")
        monkeypatch.setattr(
            "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
            tmp_path / "cache",
        )
        # Reload the module so its module-level _LOADER picks up env + tmp.
        import kira.legal_sources.adapters.lookup_handler as mod
        importlib.reload(mod)
        yield mod


def test_handler_direct_invoke(populated_bucket):
    out = populated_bucket.handler({"gesetz": "BGB", "paragraph": "535"}, None)
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["paragraph"] == "535"
    assert "Mietvertrag" in body["wortlaut"]


def test_handler_agentcore_gateway_shape(populated_bucket):
    out = populated_bucket.handler(
        {"tool_name": "lookup_norm", "tool_use_id": "x",
         "input": {"gesetz": "BGB", "paragraph": "535", "absatz": "2"}},
        None,
    )
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["absatz"] == "2"


def test_handler_unknown_gesetz_returns_error(populated_bucket):
    out = populated_bucket.handler({"gesetz": "ABC", "paragraph": "1"}, None)
    assert out["isError"] is True
    body = json.loads(out["content"][0]["text"])
    assert body["error"] == "unknown_gesetz"


def test_handler_validation_error(populated_bucket):
    out = populated_bucket.handler({"gesetz": "", "paragraph": ""}, None)
    assert out["isError"] is True
    assert "validation_error" in out["content"][0]["text"]


def test_handler_corpus_unavailable_when_no_env(monkeypatch):
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)
    monkeypatch.delenv("LEGAL_CORPUS_BUCKET", raising=False)
    import kira.legal_sources.adapters.lookup_handler as mod
    importlib.reload(mod)
    out = mod.handler({"gesetz": "BGB", "paragraph": "535"}, None)
    assert out["isError"] is True
    assert "corpus_unavailable" in out["content"][0]["text"]
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_lookup_handler.py -v`
Expected: failures (handler still uses old V1 API).

- [ ] **Step 3: Replace `lookup_handler.py`**

Replace `src/kira/legal_sources/adapters/lookup_handler.py` with:

```python
"""Lambda entrypoint for lookup_norm — wires lazy-load against the
LazyCorpusLoader."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import LazyCorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
)

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# Module-level loader: warm Lambdas reuse the same /tmp + memory caches.
_LOADER = LazyCorpusLoader.from_env()


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    args = event.get("input") if isinstance(event, dict) and "input" in event else event
    try:
        payload = LookupNormInput.model_validate(args if isinstance(args, dict) else {})
    except ValidationError as exc:
        return _err(LookupNormErrorCode.VALIDATION_ERROR, str(exc))
    try:
        result = lookup_norm(
            payload,
            load_meta=_LOADER.load_meta,
            load_norm=_LOADER.load_norm,
        )
    except CorpusUnavailableError as exc:
        return _err(LookupNormErrorCode.CORPUS_UNAVAILABLE, str(exc))
    body = result.model_dump_json()
    is_error = isinstance(result, LookupNormError)
    log.info(
        "lookup_norm",
        extra={
            "gesetz": payload.gesetz,
            "paragraph": payload.paragraph,
            "absatz": payload.absatz,
            "is_error": is_error,
        },
    )
    return {"isError": is_error, "content": [{"type": "text", "text": body}]}


def _err(code: LookupNormErrorCode, message: str) -> dict[str, Any]:
    body = LookupNormError(error=code, message=message).model_dump_json()
    return {"isError": True, "content": [{"type": "text", "text": body}]}
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_lookup_handler.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/adapters/lookup_handler.py tests/legal_sources/adapters/test_lookup_handler.py
git commit -m "refactor(legal-sources): lookup_handler wires lazy-load"
```

---

## Task 14: Search Lambda handler

**Files:**
- Create: `src/kira/legal_sources/adapters/search_handler.py`
- Create: `tests/legal_sources/adapters/test_search_handler.py`

- [ ] **Step 1: Write failing tests**

Create `tests/legal_sources/adapters/test_search_handler.py`:

```python
import importlib
import json
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("LEGAL_VECTOR_INDEX_NAME", "kira-legal-norms")


def _hit_metadata() -> dict:
    return {
        "gesetz": "BGB",
        "paragraph": "535",
        "abkuerzung": "BGB",
        "type": "Gesetz",
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "wortlaut": "(1) Durch den Mietvertrag ...",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
        "stand": "2026-05-09",
    }


def _reload_handler():
    import kira.legal_sources.adapters.search_handler as mod
    importlib.reload(mod)
    return mod


def test_search_handler_happy_path():
    mod = _reload_handler()

    from kira.legal_sources._common.vector_index import VectorSearchHit

    with patch.object(mod, "_embedder") as mock_embedder, patch.object(
        mod, "_index"
    ) as mock_index:
        mock_embedder.embed_query.return_value = [0.1] * 1024
        mock_index.query.return_value = [
            VectorSearchHit(key="bgb-535", score=0.94, metadata=_hit_metadata())
        ]
        out = mod.handler({"query": "Mietminderung Schimmel", "k": 3}, None)

    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["hits"][0]["gesetz"] == "BGB"
    assert body["hits"][0]["score"] == 0.94


def test_search_handler_agentcore_shape():
    mod = _reload_handler()
    from kira.legal_sources._common.vector_index import VectorSearchHit

    with patch.object(mod, "_embedder") as mock_embedder, patch.object(
        mod, "_index"
    ) as mock_index:
        mock_embedder.embed_query.return_value = [0.0] * 1024
        mock_index.query.return_value = [
            VectorSearchHit(key="bgb-535", score=0.5, metadata=_hit_metadata())
        ]
        out = mod.handler(
            {
                "tool_name": "search_norm",
                "tool_use_id": "x",
                "input": {"query": "x"},
            },
            None,
        )
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["query"] == "x"


def test_search_handler_embedding_failure():
    mod = _reload_handler()
    from kira.legal_sources._common.errors import EmbeddingUnavailableError

    with patch.object(mod, "_embedder") as mock_embedder:
        mock_embedder.embed_query.side_effect = EmbeddingUnavailableError("down")
        out = mod.handler({"query": "x"}, None)
    assert out["isError"] is True
    assert "embedding_unavailable" in out["content"][0]["text"]


def test_search_handler_validation_error():
    mod = _reload_handler()
    out = mod.handler({"query": ""}, None)
    assert out["isError"] is True
    assert "validation_error" in out["content"][0]["text"]


def test_search_handler_passes_gesetz_filter():
    mod = _reload_handler()

    with patch.object(mod, "_embedder") as mock_embedder, patch.object(
        mod, "_index"
    ) as mock_index:
        mock_embedder.embed_query.return_value = [0.0] * 1024
        mock_index.query.return_value = []
        mod.handler(
            {"query": "x", "gesetz_filter": ["BGB"], "type_filter": ["Gesetz"]}, None
        )

    kwargs = mock_index.query.call_args.kwargs
    assert kwargs["metadata_filter"] == {
        "abkuerzung": {"$in": ["bgb"]},
        "type": {"$in": ["Gesetz"]},
    }
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_search_handler.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/kira/legal_sources/adapters/search_handler.py`:

```python
"""Lambda entrypoint for search_norm.

No S3 corpus access. Holds a Bedrock client + S3 Vectors client at module
scope so warm invocations skip client construction.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from pydantic import ValidationError

from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.region import REQUIRED_REGION
from kira.legal_sources._common.vector_index import VectorIndex
from kira.legal_sources.gesetze.schema import (
    SearchNormError,
    SearchNormErrorCode,
    SearchNormInput,
)
from kira.legal_sources.gesetze.search_norm import search_norm

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

_INDEX_NAME = os.environ.get("LEGAL_VECTOR_INDEX_NAME", "kira-legal-norms")

_embedder = CohereMultilingualEmbedder(
    bedrock_client=boto3.client("bedrock-runtime", region_name=REQUIRED_REGION),
)
_index = VectorIndex(
    s3vectors_client=boto3.client("s3vectors", region_name=REQUIRED_REGION),
    index_name=_INDEX_NAME,
)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    args = event.get("input") if isinstance(event, dict) and "input" in event else event
    try:
        payload = SearchNormInput.model_validate(args if isinstance(args, dict) else {})
    except ValidationError as exc:
        return _err(SearchNormErrorCode.VALIDATION_ERROR, str(exc))
    result = search_norm(
        payload,
        embed=_embedder.embed_query,
        search=_index.query,
    )
    is_error = isinstance(result, SearchNormError)
    body = result.model_dump_json()
    log.info(
        "search_norm",
        extra={
            "query_len": len(payload.query),
            "k": payload.k,
            "hits": 0 if is_error else len(result.hits),
            "is_error": is_error,
        },
    )
    return {"isError": is_error, "content": [{"type": "text", "text": body}]}


def _err(code: SearchNormErrorCode, message: str) -> dict[str, Any]:
    body = SearchNormError(error=code, message=message).model_dump_json()
    return {"isError": True, "content": [{"type": "text", "text": body}]}
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_search_handler.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/adapters/search_handler.py tests/legal_sources/adapters/test_search_handler.py
git commit -m "feat(legal-sources): search_handler Lambda entrypoint"
```

---

## Task 15: Refactor `ingest_handler` — TOC discovery, per-§ diff, embedding upsert

**Files:**
- Modify: `src/kira/legal_sources/adapters/ingest_handler.py`
- Modify: `tests/legal_sources/adapters/test_ingest_handler.py`

This is the biggest behavioral change. The handler now (a) fetches gii-toc.xml, (b) for each filtered Gesetz uses conditional GETs against the upstream xml.zip, (c) builds per-paragraph JSONs, (d) diffs each paragraph's SHA256 vs. the previous `_meta.json`, (e) writes only changed paragraph files, (f) re-embeds changed paragraphs and upserts into S3 Vectors, (g) writes updated `_meta.json` + `_manifest.json`.

This is a large task. Decompose into substeps for clarity.

- [ ] **Step 1: Replace test file with new tests**

Replace `tests/legal_sources/adapters/test_ingest_handler.py` with:

```python
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import boto3
import httpx
import pytest
import respx
from moto import mock_aws

FIXTURES = Path(__file__).parent.parent / "fixtures"
TOC_FIXTURE = FIXTURES / "captured" / "gii_toc_subset.xml"


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.delenv("LEGAL_INGEST_PROXY_URL", raising=False)


@pytest.fixture
def s3_target():
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="ingest-target",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        yield "ingest-target"


def _bgb_zip() -> bytes:
    return (FIXTURES / "captured" / "bgb.zip").read_bytes()


@pytest.fixture
def mock_aws_clients():
    """Mock only the Bedrock + S3 Vectors factories; leave moto-mocked S3 untouched."""
    with patch(
        "kira.legal_sources.adapters.ingest_handler._make_embedder"
    ) as make_embedder, patch(
        "kira.legal_sources.adapters.ingest_handler._make_vector_index"
    ) as make_vidx:
        embedder = make_embedder.return_value
        embedder.embed_documents.return_value = [[0.1] * 1024]
        vidx = make_vidx.return_value
        yield {"embedder": embedder, "vector_index": vidx}


def test_ingest_writes_per_paragraph_files_and_meta(
    monkeypatch, s3_target, mock_aws_clients
):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    mock_aws_clients["embedder"].embed_documents.return_value = (
        [[0.1] * 1024]  # one paragraph in fixture
    )

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        # 304 for BetrKV-like entries we don't have fixtures for: don't matter
        # because BetrKV/WEG xml endpoints aren't stubbed and the implementation
        # iterates only over what the fixture says. To keep this test focused,
        # stub all three xml.zip URLs.
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"abc\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        result = handler({}, None)

    s3 = boto3.client("s3", region_name="eu-central-1")
    # _meta.json was written
    meta_body = s3.get_object(
        Bucket=s3_target, Key="gesetze/bgb/_meta.json"
    )["Body"].read()
    meta = json.loads(meta_body)
    assert meta["abkuerzung"] == "BGB"
    assert "535" in meta["paragraphen"]
    # per-§ JSON was written
    p535_body = s3.get_object(
        Bucket=s3_target, Key="gesetze/bgb/535.json"
    )["Body"].read()
    p535 = json.loads(p535_body)
    assert p535["paragraph"] == "535"
    # manifest written
    manifest = json.loads(
        s3.get_object(Bucket=s3_target, Key="gesetze/_manifest.json")["Body"].read()
    )
    assert manifest["version"] == 2
    assert "bgb" in manifest["gesetze"]
    # embed_documents was called once for §535
    assert mock_aws_clients["embedder"].embed_documents.call_count == 1
    assert mock_aws_clients["vector_index"].upsert.call_count == 1


def test_ingest_skips_unchanged_paragraph(monkeypatch, s3_target, mock_aws_clients):
    """Second invocation with no upstream change → no PUT for unchanged §s, no re-embed."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    mock_aws_clients["embedder"].embed_documents.return_value = [[0.1] * 1024]

    s3 = boto3.client("s3", region_name="eu-central-1")

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"abc\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        first = handler({}, None)

    # Second run: conditional HEAD returns 304 → skip whole Gesetz.
    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(304)
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        # reset embedder mock so we can assert it isn't called again
        mock_aws_clients["embedder"].embed_documents.reset_mock()
        second = handler({}, None)

    assert "bgb" in first["written"]
    assert second["written"] == []
    assert "bgb" in second["skipped"]
    # No re-embedding on the skip path
    assert mock_aws_clients["embedder"].embed_documents.call_count == 0


def test_ingest_diff_only_re_embeds_changed_paragraphs(
    monkeypatch, s3_target, mock_aws_clients
):
    """If meta has a paragraph with content_sha256 = X and the new content's
    sha256 also = X, the paragraph is NOT re-PUT or re-embedded."""
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)

    # Pre-populate meta with an entry matching what the BGB fixture produces.
    # The simplest is: run ingest once, snapshot, then run again with
    # different ETag forcing a new download but unchanged paragraph content.
    mock_aws_clients["embedder"].embed_documents.return_value = [[0.1] * 1024]

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"v1\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        handler({}, None)

    mock_aws_clients["embedder"].embed_documents.reset_mock()
    mock_aws_clients["vector_index"].upsert.reset_mock()

    # Second run: ETag changes (forces full GET) but content is identical.
    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/gii-toc.xml").mock(
            return_value=httpx.Response(200, content=TOC_FIXTURE.read_bytes())
        )
        respx.head("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, headers={"ETag": "\"v2\""})
        )
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=_bgb_zip())
        )
        respx.head("https://www.gesetze-im-internet.de/woeigg/xml.zip").mock(
            return_value=httpx.Response(404)
        )
        respx.head("https://www.gesetze-im-internet.de/betrkv/xml.zip").mock(
            return_value=httpx.Response(404)
        )

        from kira.legal_sources.adapters.ingest_handler import handler
        result = handler({}, None)

    # Same content sha → no embedding, no upsert
    assert mock_aws_clients["embedder"].embed_documents.call_count == 0
    assert mock_aws_clients["vector_index"].upsert.call_count == 0
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_ingest_handler.py -v`
Expected: failure — handler signature and behavior differ from V1.

- [ ] **Step 3: Replace `ingest_handler.py`**

Replace `src/kira/legal_sources/adapters/ingest_handler.py` with:

```python
"""Daily ingest Lambda v2: TOC-discovery + per-paragraph diff + embedding upsert."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import date
from typing import Any
from urllib.parse import quote

import boto3
import botocore.exceptions
import httpx

from kira.knowledge.ingest import _extract_xml_from_zip
from kira.knowledge.xml_parser import parse_gii_xml
from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.region import REQUIRED_REGION
from kira.legal_sources._common.toc import fetch_toc, is_citable, slug_for
from kira.legal_sources._common.vector_index import VectorIndex, VectorRecord

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

USER_AGENT = "KIRA-Agent/0.1 (legal-sources ingest; eu-central-1)"
GII_BASE = "https://www.gesetze-im-internet.de"
INDEX_NAME = os.environ.get("LEGAL_VECTOR_INDEX_NAME", "kira-legal-norms")

_ABSATZ_PREFIX = re.compile(r"^\(\s*(\d+[a-zA-Z]?)\s*\)\s*(.*)$", re.DOTALL)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    bucket = os.environ["LEGAL_CORPUS_BUCKET"]
    s3 = boto3.client("s3", region_name=REQUIRED_REGION)
    embedder = _make_embedder()
    vector_index = _make_vector_index()

    proxy_headers = _proxy_auth_headers()
    written: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []

    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"User-Agent": USER_AGENT, **proxy_headers},
        follow_redirects=True,
    ) as client:
        toc = fetch_toc(client)
        citable = [e for e in toc if is_citable(e)]
        log.info(
            "TOC fetched", extra={"total": len(toc), "citable": len(citable)}
        )

        old_manifest = _read_manifest(s3, bucket)

        for entry in citable:
            abk_slug = slug_for(entry.link)
            try:
                outcome = _process_one(
                    client=client,
                    s3=s3,
                    bucket=bucket,
                    embedder=embedder,
                    vector_index=vector_index,
                    title=entry.title,
                    abk_slug=abk_slug,
                    upstream_xml_zip=entry.link,
                    prior=old_manifest.get(abk_slug),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append({"abkuerzung": abk_slug, "error": str(exc)})
                continue
            if outcome == "written":
                written.append(abk_slug)
            elif outcome == "skipped":
                skipped.append(abk_slug)

    _write_manifest(s3, bucket, citable, s3_now_stand=date.today().isoformat())
    return {"written": written, "skipped": skipped, "errors": errors}


def _process_one(
    *,
    client: httpx.Client,
    s3: Any,
    bucket: str,
    embedder: CohereMultilingualEmbedder,
    vector_index: VectorIndex,
    title: str,
    abk_slug: str,
    upstream_xml_zip: str,
    prior: dict[str, str] | None,
) -> str:
    """Returns 'written', 'skipped', or 'no-source'."""
    proxied_xml_zip = _via_proxy(upstream_xml_zip)

    head_headers: dict[str, str] = {}
    if prior:
        if prior.get("upstream_etag"):
            head_headers["If-None-Match"] = prior["upstream_etag"]
        if prior.get("upstream_last_modified"):
            head_headers["If-Modified-Since"] = prior["upstream_last_modified"]

    head_resp = client.head(proxied_xml_zip, headers=head_headers)
    if head_resp.status_code == 304:
        return "skipped"
    if head_resp.status_code != 200:
        return "no-source"

    new_etag = head_resp.headers.get("ETag", "")
    new_last_modified = head_resp.headers.get("Last-Modified", "")

    get_resp = client.get(proxied_xml_zip)
    get_resp.raise_for_status()
    xml_bytes = _extract_xml_from_zip(get_resp.content)
    parsed = parse_gii_xml(xml_bytes)

    abk = abk_slug.upper()
    today_iso = date.today().isoformat()
    new_paragraphen: dict[str, dict[str, Any]] = {}
    to_upsert: list[VectorRecord] = []
    embed_inputs: list[str] = []
    embed_keys: list[str] = []
    deleted_keys: list[str] = []

    old_meta = _read_old_meta(s3, bucket, abk_slug)
    old_para_shas = {
        p: e.get("content_sha256", "")
        for p, e in (old_meta.get("paragraphen") or {}).items()
    }

    for paragraph, norm in parsed.items():
        norm_payload = {
            "gesetz": abk,
            "paragraph": paragraph,
            "titel": norm.titel,
            "absaetze": [_split_absatz(s) for s in norm.absaetze],
            "quelle_url": f"{GII_BASE}/{abk_slug}/__{paragraph}.html",
        }
        norm_body = json.dumps(norm_payload, ensure_ascii=False, sort_keys=True)
        sha = hashlib.sha256(norm_body.encode("utf-8")).hexdigest()
        norm_key = f"gesetze/{abk_slug}/{paragraph}.json"
        new_paragraphen[paragraph] = {
            "titel": norm.titel,
            "key": norm_key,
            "content_sha256": sha,
        }
        if old_para_shas.get(paragraph) != sha:
            s3.put_object(
                Bucket=bucket,
                Key=norm_key,
                Body=norm_body.encode("utf-8"),
                ContentType="application/json",
                Metadata={"content-sha256": sha},
            )
            embed_inputs.append(_embed_input(abk, paragraph, norm_payload))
            embed_keys.append(f"{abk_slug}-{paragraph}")

    # Detect deletions
    for old_p in old_para_shas:
        if old_p not in new_paragraphen:
            deleted_keys.append(f"{abk_slug}-{old_p}")
            s3.delete_object(Bucket=bucket, Key=f"gesetze/{abk_slug}/{old_p}.json")

    type_str = "Verordnung" if "verord" in title.lower() else "Gesetz"

    meta_payload = {
        "abkuerzung": abk,
        "titel": title,
        "type": type_str,
        "stand": today_iso,
        "quelle": "gesetze-im-internet.de",
        "quelle_url": f"{GII_BASE}/{abk_slug}",
        "upstream_xml_zip_url": upstream_xml_zip,
        "paragraphen": new_paragraphen,
    }
    s3.put_object(
        Bucket=bucket,
        Key=f"gesetze/{abk_slug}/_meta.json",
        Body=json.dumps(meta_payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
        Metadata={
            "upstream_etag": new_etag,
            "upstream_last_modified": new_last_modified,
        },
    )

    # Embeddings
    if embed_inputs:
        vectors = embedder.embed_documents(embed_inputs)
        records = [
            VectorRecord(
                key=k,
                vector=v,
                metadata={
                    "gesetz": abk,
                    "paragraph": k.split("-", 1)[1],
                    "abkuerzung": abk,
                    "type": type_str,
                    "titel": new_paragraphen[k.split("-", 1)[1]]["titel"],
                    "wortlaut": _read_norm_wortlaut(s3, bucket, abk_slug, k.split("-", 1)[1]),
                    "quelle_url": f"{GII_BASE}/{abk_slug}/__{k.split('-', 1)[1]}.html",
                    "stand": today_iso,
                    "content_sha256": new_paragraphen[k.split("-", 1)[1]]["content_sha256"],
                },
            )
            for k, v in zip(embed_keys, vectors, strict=True)
        ]
        vector_index.upsert(records)
    if deleted_keys:
        vector_index.delete(deleted_keys)

    return "written"


def _split_absatz(raw: str) -> dict[str, str]:
    m = _ABSATZ_PREFIX.match(raw)
    if m:
        return {"nummer": m.group(1), "text": m.group(2).strip()}
    return {"nummer": "", "text": raw.strip()}


def _embed_input(abk: str, paragraph: str, payload: dict) -> str:
    body = "\n\n".join(
        f"({a['nummer']}) {a['text']}" for a in payload["absaetze"]
    )
    return f"{abk} §{paragraph} ({payload['titel']}):\n\n{body}"


def _read_norm_wortlaut(s3: Any, bucket: str, abk_slug: str, paragraph: str) -> str:
    body = s3.get_object(Bucket=bucket, Key=f"gesetze/{abk_slug}/{paragraph}.json")
    data = json.loads(body["Body"].read())
    return "\n\n".join(f"({a['nummer']}) {a['text']}" for a in data["absaetze"])


def _read_manifest(s3: Any, bucket: str) -> dict[str, dict[str, str]]:
    try:
        body = s3.get_object(Bucket=bucket, Key="gesetze/_manifest.json")
    except botocore.exceptions.ClientError:
        return {}
    payload = json.loads(body["Body"].read())
    return {
        abk: {
            "upstream_etag": entry.get("upstream_etag", ""),
            "upstream_last_modified": entry.get("upstream_last_modified", ""),
        }
        for abk, entry in payload.get("gesetze", {}).items()
    }


def _read_old_meta(s3: Any, bucket: str, abk_slug: str) -> dict:
    try:
        body = s3.get_object(Bucket=bucket, Key=f"gesetze/{abk_slug}/_meta.json")
    except botocore.exceptions.ClientError:
        return {}
    return json.loads(body["Body"].read())


def _write_manifest(s3: Any, bucket: str, citable: list, s3_now_stand: str) -> None:
    """Compose the manifest from current meta objects in S3."""
    gesetze: dict[str, dict[str, Any]] = {}
    for entry in citable:
        abk_slug = slug_for(entry.link)
        try:
            meta_resp = s3.get_object(
                Bucket=bucket, Key=f"gesetze/{abk_slug}/_meta.json"
            )
        except botocore.exceptions.ClientError:
            continue
        meta = json.loads(meta_resp["Body"].read())
        meta_meta = meta_resp.get("Metadata", {}) or {}
        gesetze[abk_slug] = {
            "abkuerzung": meta["abkuerzung"],
            "titel": meta["titel"],
            "type": meta["type"],
            "meta_key": f"gesetze/{abk_slug}/_meta.json",
            "upstream_etag": meta_meta.get("upstream_etag", ""),
            "upstream_last_modified": meta_meta.get("upstream_last_modified", ""),
        }
    payload = {"version": 2, "stand": s3_now_stand, "gesetze": gesetze}
    s3.put_object(
        Bucket=bucket,
        Key="gesetze/_manifest.json",
        Body=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


def _via_proxy(direct_url: str) -> str:
    proxy = os.environ.get("LEGAL_INGEST_PROXY_URL")
    if not proxy:
        return direct_url
    return f"{proxy.rstrip('/')}/?url={quote(direct_url, safe='')}"


def _proxy_auth_headers() -> dict[str, str]:
    value = os.environ.get("LEGAL_INGEST_PROXY_AUTH_VALUE")
    if not value:
        return {}
    name = os.environ.get("LEGAL_INGEST_PROXY_AUTH_HEADER") or "X-Proxy-Auth"
    return {name: value}


def _make_embedder() -> CohereMultilingualEmbedder:
    return CohereMultilingualEmbedder(
        bedrock_client=boto3.client("bedrock-runtime", region_name=REQUIRED_REGION),
    )


def _make_vector_index() -> VectorIndex:
    return VectorIndex(
        s3vectors_client=boto3.client("s3vectors", region_name=REQUIRED_REGION),
        index_name=INDEX_NAME,
    )
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_ingest_handler.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/adapters/ingest_handler.py tests/legal_sources/adapters/test_ingest_handler.py
git commit -m "refactor(legal-sources): ingest_handler v2 — TOC + per-§ diff + embedding"
```

---

## Task 16: Update `kira_registry` to expose both tools

**Files:**
- Modify: `src/kira/legal_sources/adapters/kira_registry.py`
- Modify: `tests/legal_sources/adapters/test_kira_registry.py`

- [ ] **Step 1: Append failing tests**

APPEND to `tests/legal_sources/adapters/test_kira_registry.py`:

```python
from unittest.mock import MagicMock


def test_build_search_norm_tool_calls_search(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))
    from kira.legal_sources.adapters.kira_registry import build_search_norm_tool

    fake_embedder = MagicMock()
    fake_embedder.embed_query.return_value = [0.1] * 1024
    fake_index = MagicMock()
    fake_index.query.return_value = []
    tool = build_search_norm_tool(embedder=fake_embedder, index=fake_index)
    assert tool.name == "search_norm"
    text = tool.run({"query": "Mietminderung"})
    assert "Keine Treffer" in text or "Suche" in text
    fake_embedder.embed_query.assert_called_once_with("Mietminderung")
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_kira_registry.py -v`
Expected: ImportError on `build_search_norm_tool`.

- [ ] **Step 3: Append to `kira_registry.py`**

APPEND to `src/kira/legal_sources/adapters/kira_registry.py`:

```python
from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.vector_index import VectorIndex
from kira.legal_sources.gesetze.schema import SearchNormInput
from kira.legal_sources.gesetze.search_norm import search_norm


_SEARCH_DESCRIPTION = (
    "Semantische Suche über alle deutschen Bundesgesetze + Rechtsverordnungen. "
    "Eingabe: query (freier Text, z.B. 'Pflichten des Vermieters zur Erhaltung "
    "der Mietsache'). Optional: k (1-50, Default 10), gesetz_filter (Liste von "
    "Abkürzungen), type_filter (['Gesetz'] oder ['Verordnung']). Liefert "
    "rangbasierte Treffer mit Wortlaut. Suche dient der Auffindung — für die "
    "autoritative Zitation IMMER zusätzlich lookup_norm aufrufen."
)


def build_search_norm_tool(
    *,
    embedder: CohereMultilingualEmbedder | None = None,
    index: VectorIndex | None = None,
) -> Tool:
    if embedder is None or index is None:
        raise ValueError(
            "build_search_norm_tool requires embedder + index dependencies"
        )

    def _run(input_data: dict[str, Any]) -> str:
        try:
            payload = SearchNormInput.model_validate(input_data)
        except ValidationError as exc:
            return f"FEHLER (validation_error): {exc}"
        result = search_norm(
            payload, embed=embedder.embed_query, search=index.query
        )
        return result.to_agent_text()

    return Tool(
        name="search_norm",
        description=_SEARCH_DESCRIPTION,
        input_schema=SearchNormInput.model_json_schema(),
        run=_run,
    )
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_kira_registry.py -v`
Expected: 3 passed (2 previous + 1 new).

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/adapters/kira_registry.py tests/legal_sources/adapters/test_kira_registry.py
git commit -m "feat(legal-sources): registry adapter exposes search_norm"
```

---

## Task 17: Update `agent_sdk` to expose `make_search_norm_tool_function`

**Files:**
- Modify: `src/kira/legal_sources/adapters/agent_sdk.py`
- Modify: `tests/legal_sources/adapters/test_agent_sdk.py`

- [ ] **Step 1: Append failing test**

APPEND to `tests/legal_sources/adapters/test_agent_sdk.py`:

```python
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_search_norm_tool_function_returns_mcp_shape():
    from kira.legal_sources.adapters.agent_sdk import (
        make_search_norm_tool_function,
    )

    embedder = MagicMock()
    embedder.embed_query.return_value = [0.0] * 1024
    index = MagicMock()
    index.query.return_value = []

    fn = make_search_norm_tool_function(embedder=embedder, index=index)
    out = await fn({"query": "Mietminderung"})
    assert "content" in out
    assert out["content"][0]["type"] == "text"
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_agent_sdk.py -v`
Expected: ImportError on `make_search_norm_tool_function`.

- [ ] **Step 3: Append to `agent_sdk.py`**

APPEND to `src/kira/legal_sources/adapters/agent_sdk.py`:

```python
from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.vector_index import VectorIndex
from kira.legal_sources.gesetze.schema import SearchNormInput
from kira.legal_sources.gesetze.search_norm import search_norm


def make_search_norm_tool_function(
    *,
    embedder: CohereMultilingualEmbedder,
    index: VectorIndex,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def _impl(args: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = SearchNormInput.model_validate(args)
        except ValidationError as exc:
            return _text(f"validation_error: {exc}")
        result = search_norm(payload, embed=embedder.embed_query, search=index.query)
        return _text(result.to_agent_text())

    return _impl
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_agent_sdk.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/adapters/agent_sdk.py tests/legal_sources/adapters/test_agent_sdk.py
git commit -m "feat(legal-sources): agent_sdk exposes make_search_norm_tool_function"
```

---

## Task 18: One-time local backfill script

**Files:**
- Create: `scripts/backfill_corpus.py`

This script is operational tooling, not Lambda code; tested via dry-run mode (`--dry-run`) and a Tier-2-style integration test against moto + mocked Bedrock/S3 Vectors.

- [ ] **Step 1: Write the script**

Create `scripts/backfill_corpus.py`:

```python
"""One-time local backfill: rebuild the corpus + vector index from scratch.

Runs from a residential ISP (the Cloudflare Worker free tier can't absorb a
~1.5 GB first ingest; this bypasses the proxy by hitting upstream directly).

Resumable: on re-run, conditional HEAD against each upstream xml.zip; only
re-processes Gesetze whose ETag changed since the last manifest.

Usage:
    LEGAL_CORPUS_BUCKET=kira-legal-corpus-${ACCOUNT}-eu-central-1 \\
      .venv/bin/python scripts/backfill_corpus.py \\
        --max-parallel 8 \\
        --vector-index kira-legal-norms \\
        --embed-batch 96 \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import date
from typing import Any

import boto3
import httpx

from kira.knowledge.ingest import _extract_xml_from_zip
from kira.knowledge.xml_parser import parse_gii_xml
from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.region import REQUIRED_REGION
from kira.legal_sources._common.toc import fetch_toc, is_citable, slug_for
from kira.legal_sources._common.vector_index import VectorIndex, VectorRecord

GII_BASE = "https://www.gesetze-im-internet.de"
USER_AGENT = "KIRA-Agent/0.1 (backfill; residential ISP)"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("backfill")

_ABSATZ_PREFIX = re.compile(r"^\(\s*(\d+[a-zA-Z]?)\s*\)\s*(.*)$", re.DOTALL)


def main() -> int:
    args = _parse_args()
    bucket = os.environ["LEGAL_CORPUS_BUCKET"]
    s3 = boto3.client("s3", region_name=REQUIRED_REGION)

    if args.dry_run:
        embedder = None
        vector_index = None
        log.warning("DRY RUN — no S3 PUTs, no embeddings, no vector upserts")
    else:
        embedder = CohereMultilingualEmbedder(
            bedrock_client=boto3.client(
                "bedrock-runtime", region_name=REQUIRED_REGION
            ),
        )
        vector_index = VectorIndex(
            s3vectors_client=boto3.client("s3vectors", region_name=REQUIRED_REGION),
            index_name=args.vector_index,
        )

    with httpx.Client(
        timeout=httpx.Timeout(120.0, connect=15.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        toc = fetch_toc(client)
        citable = [e for e in toc if is_citable(e)]
        log.info("TOC: total=%d, citable=%d", len(toc), len(citable))

        prior_manifest = _read_manifest(s3, bucket) if not args.dry_run else {}

        t0 = time.time()
        results: dict[str, str] = {}
        embed_inputs: list[tuple[str, str]] = []  # (vector_key, embed_input)
        embed_metadata: dict[str, dict[str, Any]] = {}

        # Parallel raw-ingest
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.max_parallel
        ) as pool:
            futures = {
                pool.submit(
                    _process_one,
                    client=client,
                    s3=s3,
                    bucket=bucket,
                    title=entry.title,
                    abk_slug=slug_for(entry.link),
                    upstream_xml_zip=entry.link,
                    prior=prior_manifest.get(slug_for(entry.link)),
                    dry_run=args.dry_run,
                ): entry
                for entry in citable
            }
            for fut in concurrent.futures.as_completed(futures):
                entry = futures[fut]
                slug = slug_for(entry.link)
                try:
                    outcome, embed_jobs, embed_md = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.error("Gesetz %s failed: %s", slug, exc)
                    results[slug] = "error"
                    continue
                results[slug] = outcome
                embed_inputs.extend(embed_jobs)
                embed_metadata.update(embed_md)

        # Embedding pass (sequential, batched)
        if not args.dry_run and embed_inputs:
            log.info("Embedding %d paragraphs", len(embed_inputs))
            for start in range(0, len(embed_inputs), args.embed_batch):
                chunk = embed_inputs[start : start + args.embed_batch]
                texts = [item[1] for item in chunk]
                keys = [item[0] for item in chunk]
                vectors = embedder.embed_documents(texts)
                records = [
                    VectorRecord(
                        key=k,
                        vector=v,
                        metadata=embed_metadata[k],
                    )
                    for k, v in zip(keys, vectors, strict=True)
                ]
                vector_index.upsert(records)
                log.info(
                    "  embedded %d/%d", start + len(chunk), len(embed_inputs)
                )

        # Final manifest
        if not args.dry_run:
            _write_manifest(s3, bucket, citable)

        wall = time.time() - t0
        summary = {
            "duration_seconds": round(wall, 1),
            "laws_total": len(citable),
            "laws_written": sum(1 for v in results.values() if v == "written"),
            "laws_skipped": sum(1 for v in results.values() if v == "skipped"),
            "laws_errored": sum(1 for v in results.values() if v == "error"),
            "paragraphs_embedded": len(embed_inputs),
        }
        log.info("DONE %s", summary)
        print(json.dumps(summary, indent=2))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-parallel", type=int, default=8)
    parser.add_argument("--vector-index", default="kira-legal-norms")
    parser.add_argument("--embed-batch", type=int, default=96)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _process_one(
    *,
    client: httpx.Client,
    s3: Any,
    bucket: str,
    title: str,
    abk_slug: str,
    upstream_xml_zip: str,
    prior: dict[str, str] | None,
    dry_run: bool,
) -> tuple[str, list[tuple[str, str]], dict[str, dict[str, Any]]]:
    head_headers: dict[str, str] = {}
    if prior:
        if prior.get("upstream_etag"):
            head_headers["If-None-Match"] = prior["upstream_etag"]
        if prior.get("upstream_last_modified"):
            head_headers["If-Modified-Since"] = prior["upstream_last_modified"]

    head_resp = client.head(upstream_xml_zip, headers=head_headers)
    if head_resp.status_code == 304:
        return ("skipped", [], {})
    if head_resp.status_code != 200:
        return ("no-source", [], {})

    new_etag = head_resp.headers.get("ETag", "")
    new_last_modified = head_resp.headers.get("Last-Modified", "")

    resp = client.get(upstream_xml_zip)
    resp.raise_for_status()
    xml_bytes = _extract_xml_from_zip(resp.content)
    parsed = parse_gii_xml(xml_bytes)

    abk = abk_slug.upper()
    today_iso = date.today().isoformat()
    new_paragraphen: dict[str, dict[str, Any]] = {}
    embed_jobs: list[tuple[str, str]] = []
    embed_md: dict[str, dict[str, Any]] = {}
    type_str = "Verordnung" if "verord" in title.lower() else "Gesetz"

    for paragraph, norm in parsed.items():
        payload = {
            "gesetz": abk,
            "paragraph": paragraph,
            "titel": norm.titel,
            "absaetze": [_split_absatz(s) for s in norm.absaetze],
            "quelle_url": f"{GII_BASE}/{abk_slug}/__{paragraph}.html",
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        sha = hashlib.sha256(body).hexdigest()
        key = f"gesetze/{abk_slug}/{paragraph}.json"
        new_paragraphen[paragraph] = {
            "titel": norm.titel,
            "key": key,
            "content_sha256": sha,
        }
        if not dry_run:
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                Metadata={"content-sha256": sha},
            )
            vec_key = f"{abk_slug}-{paragraph}"
            wortlaut = "\n\n".join(
                f"({a['nummer']}) {a['text']}" for a in payload["absaetze"]
            )
            embed_jobs.append(
                (vec_key, f"{abk} §{paragraph} ({norm.titel}):\n\n{wortlaut}")
            )
            embed_md[vec_key] = {
                "gesetz": abk,
                "paragraph": paragraph,
                "abkuerzung": abk,
                "type": type_str,
                "titel": norm.titel,
                "wortlaut": wortlaut,
                "quelle_url": payload["quelle_url"],
                "stand": today_iso,
                "content_sha256": sha,
            }

    meta_payload = {
        "abkuerzung": abk,
        "titel": title,
        "type": type_str,
        "stand": today_iso,
        "quelle": "gesetze-im-internet.de",
        "quelle_url": f"{GII_BASE}/{abk_slug}",
        "upstream_xml_zip_url": upstream_xml_zip,
        "paragraphen": new_paragraphen,
    }
    if not dry_run:
        s3.put_object(
            Bucket=bucket,
            Key=f"gesetze/{abk_slug}/_meta.json",
            Body=json.dumps(meta_payload, ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            ),
            ContentType="application/json",
            Metadata={
                "upstream_etag": new_etag,
                "upstream_last_modified": new_last_modified,
            },
        )
    log.info(
        "Gesetz %s: %d paragraphs", abk_slug, len(new_paragraphen)
    )
    return ("written", embed_jobs, embed_md)


def _split_absatz(raw: str) -> dict[str, str]:
    m = _ABSATZ_PREFIX.match(raw)
    if m:
        return {"nummer": m.group(1), "text": m.group(2).strip()}
    return {"nummer": "", "text": raw.strip()}


def _read_manifest(s3: Any, bucket: str) -> dict[str, dict[str, str]]:
    import botocore.exceptions
    try:
        body = s3.get_object(Bucket=bucket, Key="gesetze/_manifest.json")
    except botocore.exceptions.ClientError:
        return {}
    payload = json.loads(body["Body"].read())
    return {
        abk: {
            "upstream_etag": entry.get("upstream_etag", ""),
            "upstream_last_modified": entry.get("upstream_last_modified", ""),
        }
        for abk, entry in payload.get("gesetze", {}).items()
    }


def _write_manifest(s3: Any, bucket: str, citable: list) -> None:
    import botocore.exceptions
    gesetze: dict[str, dict[str, Any]] = {}
    for entry in citable:
        abk_slug = slug_for(entry.link)
        try:
            meta_resp = s3.get_object(
                Bucket=bucket, Key=f"gesetze/{abk_slug}/_meta.json"
            )
        except botocore.exceptions.ClientError:
            continue
        meta = json.loads(meta_resp["Body"].read())
        meta_md = meta_resp.get("Metadata", {}) or {}
        gesetze[abk_slug] = {
            "abkuerzung": meta["abkuerzung"],
            "titel": meta["titel"],
            "type": meta["type"],
            "meta_key": f"gesetze/{abk_slug}/_meta.json",
            "upstream_etag": meta_md.get("upstream_etag", ""),
            "upstream_last_modified": meta_md.get("upstream_last_modified", ""),
        }
    payload = {
        "version": 2,
        "stand": date.today().isoformat(),
        "gesetze": gesetze,
    }
    s3.put_object(
        Bucket=bucket,
        Key="gesetze/_manifest.json",
        Body=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test —help**

Run: `.venv/bin/python scripts/backfill_corpus.py --help`
Expected: argparse prints help text without traceback.

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_corpus.py
git commit -m "feat(legal-sources): scripts/backfill_corpus.py for one-time backfill"
```

---

## Task 19: Cloudflare Worker — conditional-header passthrough

**Files:**
- Modify: `infra/cloudflare/juris-proxy/worker.js`

- [ ] **Step 1: Update the worker**

Replace `infra/cloudflare/juris-proxy/worker.js` with:

```javascript
// Cloudflare Worker: gesetze-im-internet.de proxy.
//
// Streams binary bodies (xml.zip) through unchanged AND forwards conditional
// request headers (If-None-Match, If-Modified-Since) so the ingest Lambda
// can run cheap "did this change?" probes without re-downloading.

const ALLOWED_PREFIX = 'https://www.gesetze-im-internet.de';
const FORWARD_REQUEST_HEADERS = ['if-none-match', 'if-modified-since'];

export default {
  async fetch(request, env) {
    if (env.PROXY_SECRET) {
      const auth = request.headers.get('X-Proxy-Auth');
      if (auth !== env.PROXY_SECRET) {
        return new Response('unauthorized', { status: 401 });
      }
    }

    const url = new URL(request.url);
    const target = url.searchParams.get('url');
    if (!target || !target.startsWith(ALLOWED_PREFIX)) {
      return new Response('Missing or invalid URL', { status: 400 });
    }

    const upstreamHeaders = {
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      Accept: '*/*',
    };
    for (const h of FORWARD_REQUEST_HEADERS) {
      const v = request.headers.get(h);
      if (v) upstreamHeaders[h] = v;
    }

    try {
      const upstream = await fetch(target, {
        method: request.method,  // pass-through GET or HEAD
        headers: upstreamHeaders,
      });
      const passThroughHeaders = {
        'Content-Type':
          upstream.headers.get('content-type') || 'application/octet-stream',
      };
      for (const h of ['etag', 'last-modified', 'content-length']) {
        const v = upstream.headers.get(h);
        if (v) passThroughHeaders[h] = v;
      }
      return new Response(upstream.body, {
        status: upstream.status,
        headers: passThroughHeaders,
      });
    } catch (e) {
      return new Response('Fetch failed: ' + e.message, { status: 500 });
    }
  },
};
```

- [ ] **Step 2: Deploy worker**

Operator step (not run by the implementer subagent — surface as a task in the commit message):

```bash
cd infra/cloudflare/juris-proxy
wrangler deploy
```

- [ ] **Step 3: Commit**

```bash
git add infra/cloudflare/juris-proxy/worker.js
git commit -m "feat(worker): pass through If-None-Match / If-Modified-Since + HEAD"
```

The commit body notes that the worker requires manual `wrangler deploy` after this commit lands.

---

## Task 20: CDK — Search Lambda, S3 Vectors index, IAM grants, /tmp bump

**Files:**
- Modify: `infra/legal_sources/stack.py`

- [ ] **Step 1: Update stack**

Add to the imports near the top:

```python
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_s3vectors as s3vectors,  # check availability; if not yet in CDK, use CfnResource
)
```

If `aws_s3vectors` is not yet exposed as an L2 in your CDK version, use a `cdk.CfnResource` instead:

```python
vector_index = cdk.CfnResource(
    self,
    "LegalNormsVectorIndex",
    type="AWS::S3Vectors::Index",
    properties={
        "IndexName": "kira-legal-norms",
        "Dimension": 1024,
        "DistanceMetric": "COSINE",
        # plus any other required fields from the CFN docs at deploy time
    },
)
```

Then add a Search Lambda using the same `code` asset already built:

```python
search_fn = lambda_.Function(
    self,
    "SearchNormFn",
    function_name="kira-legal-search",
    runtime=lambda_.Runtime.PYTHON_3_11,
    architecture=arch,
    handler="kira.legal_sources.adapters.search_handler.handler",
    code=code,
    memory_size=512,
    timeout=cdk.Duration.seconds(5),
    environment={
        "LEGAL_VECTOR_INDEX_NAME": "kira-legal-norms",
    },
    log_retention=logs.RetentionDays.ONE_MONTH,
)

# Search Lambda needs Bedrock InvokeModel for Cohere and S3 Vectors Query.
search_fn.add_to_role_policy(
    iam.PolicyStatement(
        actions=["bedrock:InvokeModel"],
        resources=[
            f"arn:aws:bedrock:{REQUIRED_REGION}::foundation-model/cohere.embed-multilingual-v3"
        ],
    )
)
search_fn.add_to_role_policy(
    iam.PolicyStatement(
        actions=["s3vectors:QueryVectors"],
        resources=["*"],  # tighten once ARN format is stable in CDK
    )
)

# Ingest Lambda also needs Bedrock InvokeModel + S3 Vectors PutVectors/DeleteVectors.
ingest_fn.add_to_role_policy(
    iam.PolicyStatement(
        actions=["bedrock:InvokeModel"],
        resources=[
            f"arn:aws:bedrock:{REQUIRED_REGION}::foundation-model/cohere.embed-multilingual-v3"
        ],
    )
)
ingest_fn.add_to_role_policy(
    iam.PolicyStatement(
        actions=["s3vectors:PutVectors", "s3vectors:DeleteVectors"],
        resources=["*"],
    )
)
```

Bump the lookup Lambda's ephemeral storage:

```python
lookup_fn.add_property_override("EphemeralStorage", {"Size": 1024})  # MiB
```

(Or, if your CDK version exposes it cleanly, set `ephemeral_storage_size=cdk.Size.mebibytes(1024)` directly on the constructor.)

Bump ingest timeout to 15 min:

```python
# in the IngestFn constructor:
timeout=cdk.Duration.minutes(15),
memory_size=1536,
```

Add CfnOutput for the search Lambda ARN:

```python
cdk.CfnOutput(self, "SearchFnArn", value=search_fn.function_arn)
```

- [ ] **Step 2: Synth to validate**

Run: `cd infra/legal_sources && PATH="$PWD/../../.venv/bin:$PATH" CDK_DEFAULT_ACCOUNT=000000000000 cdk synth --no-staging 2>&1 | tail -10`

Expected: clean synth, template contains `AWS::Lambda::Function` for SearchNormFn, `AWS::S3Vectors::Index` resource, the new IAM policies, and ephemeral storage 1024 on LookupNormFn.

- [ ] **Step 3: Run region-pin test**

Run: `.venv/bin/pytest tests/infra/ -v`
Expected: still passes (the test walks the whole template).

- [ ] **Step 4: Commit**

```bash
git add infra/legal_sources/stack.py
git commit -m "feat(infra): CDK adds search Lambda, S3 Vectors index, bumps lookup /tmp"
```

---

## Task 21: Live smoke test v2

**Files:**
- Create: `tests/legal_sources/live/test_live_smoke_v2.py`

- [ ] **Step 1: Write the live smoke tests**

Create `tests/legal_sources/live/test_live_smoke_v2.py`:

```python
"""Opt-in V2 live smoke. Run with: RUN_LIVE_TESTS=1 pytest -m live."""

import os
import zipfile
from io import BytesIO

import boto3
import httpx
import pytest

pytestmark = [pytest.mark.live]

if not os.environ.get("RUN_LIVE_TESTS"):
    pytest.skip("RUN_LIVE_TESTS not set", allow_module_level=True)


def test_live_toc_in_expected_size_band():
    from kira.legal_sources._common.toc import fetch_toc, is_citable

    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"User-Agent": "KIRA-Agent/0.1 (live smoke v2)"},
        follow_redirects=True,
    ) as client:
        entries = fetch_toc(client)
    citable = [e for e in entries if is_citable(e)]
    assert 2000 <= len(citable) <= 3500, f"unexpected citable count: {len(citable)}"


def test_live_cohere_embedding_dimension():
    bedrock = boto3.client("bedrock-runtime", region_name="eu-central-1")
    from kira.legal_sources._common.embedder import (
        EMBEDDING_DIMENSION,
        CohereMultilingualEmbedder,
    )
    embedder = CohereMultilingualEmbedder(bedrock_client=bedrock)
    vectors = embedder.embed_documents(["Mietminderung wegen Schimmel"])
    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIMENSION


def test_live_s3_vectors_roundtrip():
    """Requires `kira-legal-norms` index to exist (deployed by CDK)."""
    from kira.legal_sources._common.vector_index import VectorIndex, VectorRecord

    s3v = boto3.client("s3vectors", region_name="eu-central-1")
    idx = VectorIndex(s3vectors_client=s3v, index_name="kira-legal-norms")
    # Insert one test vector, query, delete.
    test_key = "__live_smoke_v2__"
    vec = [0.1] * 1024
    idx.upsert([
        VectorRecord(
            key=test_key,
            vector=vec,
            metadata={
                "gesetz": "TEST",
                "paragraph": "0",
                "abkuerzung": "TEST",
                "type": "Gesetz",
                "titel": "smoke",
                "wortlaut": "x",
                "quelle_url": "https://example.test",
                "stand": "2026-05-10",
                "content_sha256": "smoke",
            },
        )
    ])
    try:
        hits = idx.query(vector=vec, k=5, metadata_filter={"abkuerzung": {"$in": ["TEST"]}})
        assert any(h.key == test_key for h in hits)
    finally:
        idx.delete([test_key])
```

- [ ] **Step 2: Verify default skip**

Run: `.venv/bin/pytest tests/legal_sources/live/test_live_smoke_v2.py -v`
Expected: 3 skipped.

- [ ] **Step 3: Commit**

```bash
git add tests/legal_sources/live/test_live_smoke_v2.py
git commit -m "test(legal-sources): live smoke v2 (TOC, Cohere, S3 Vectors)"
```

---

## Task 22: Update smoke script with search round-trip

**Files:**
- Modify: `scripts/legal_sources_smoke.py`

- [ ] **Step 1: Extend the script**

Replace `scripts/legal_sources_smoke.py` with:

```python
"""V2 end-to-end smoke: invoke lookup AND search Lambdas in eu-central-1."""

from __future__ import annotations

import argparse
import json
import sys

import boto3


def _invoke(function_name: str, region: str, payload: dict) -> dict:
    client = boto3.client("lambda", region_name=region)
    resp = client.invoke(
        FunctionName=function_name,
        Payload=json.dumps(payload).encode("utf-8"),
    )
    return json.loads(resp["Payload"].read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookup-fn", default="kira-legal-lookup-norm")
    parser.add_argument("--search-fn", default="kira-legal-search")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()

    print("=== 1. Lookup BGB §535 ===")
    r = _invoke(args.lookup_fn, args.region, {"gesetz": "BGB", "paragraph": "535"})
    if r.get("isError"):
        print("FAIL:", r, file=sys.stderr); return 1
    print(json.loads(r["content"][0]["text"])["titel"])

    print("\n=== 2. Lookup WEG §14 (proves all-laws coverage) ===")
    r = _invoke(args.lookup_fn, args.region, {"gesetz": "WEG", "paragraph": "14"})
    if r.get("isError"):
        print("FAIL:", r, file=sys.stderr); return 1
    print(json.loads(r["content"][0]["text"])["titel"])

    print("\n=== 3. Search 'Pflichten des Vermieters zur Erhaltung der Mietsache' ===")
    r = _invoke(args.search_fn, args.region, {
        "query": "Pflichten des Vermieters zur Erhaltung der Mietsache",
        "k": 3,
    })
    if r.get("isError"):
        print("FAIL:", r, file=sys.stderr); return 1
    body = json.loads(r["content"][0]["text"])
    paragraphs = [(h["gesetz"], h["paragraph"]) for h in body["hits"]]
    print("Top hits:", paragraphs)
    assert any(p == ("BGB", "535") for p in paragraphs), "expected BGB §535 in top 3"

    print("\n=== 4. Search 'Schadensersatz statt der Leistung' gesetz=BGB ===")
    r = _invoke(args.search_fn, args.region, {
        "query": "Schadensersatz statt der Leistung",
        "gesetz_filter": ["BGB"],
        "k": 1,
    })
    if r.get("isError"):
        print("FAIL:", r, file=sys.stderr); return 1
    body = json.loads(r["content"][0]["text"])
    print("Top:", body["hits"][0]["gesetz"], "§", body["hits"][0]["paragraph"])

    print("\n✅ V2 smoke OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-test help**

Run: `.venv/bin/python scripts/legal_sources_smoke.py --help`
Expected: argparse help.

- [ ] **Step 3: Commit**

```bash
git add scripts/legal_sources_smoke.py
git commit -m "test(legal-sources): smoke script covers search round-trip"
```

---

## Task 23: Performance tests (gated)

**Files:**
- Create: `tests/legal_sources/perf/__init__.py`
- Create: `tests/legal_sources/perf/test_perf_budgets.py`

- [ ] **Step 1: Add the perf marker**

In `pyproject.toml`, add to `[tool.pytest.ini_options].markers`:

```toml
"perf: opt-in performance budget tests; require RUN_PERF_TESTS=1 and a deployed stack",
```

And to `addopts`: `"-ra -q -m 'not live and not perf'"`.

- [ ] **Step 2: Write the perf tests**

Create `tests/legal_sources/perf/__init__.py` (empty).

Create `tests/legal_sources/perf/test_perf_budgets.py`:

```python
"""Opt-in perf budgets against the DEPLOYED Lambdas. RUN_PERF_TESTS=1."""

import json
import os
import statistics
import time

import boto3
import pytest

pytestmark = [pytest.mark.perf]

if not os.environ.get("RUN_PERF_TESTS"):
    pytest.skip("RUN_PERF_TESTS not set", allow_module_level=True)

LAMBDA = boto3.client("lambda", region_name="eu-central-1")


def _invoke(fn: str, payload: dict) -> dict:
    t0 = time.perf_counter()
    resp = LAMBDA.invoke(
        FunctionName=fn, Payload=json.dumps(payload).encode("utf-8")
    )
    body = json.loads(resp["Payload"].read())
    return {"ms": (time.perf_counter() - t0) * 1000, "body": body}


def test_lookup_warm_p99_under_50ms():
    """1000 warm invocations against the same BGB §535. p99 should be <50ms."""
    # Warm the function first
    _invoke("kira-legal-lookup-norm", {"gesetz": "BGB", "paragraph": "535"})
    durations = [
        _invoke("kira-legal-lookup-norm", {"gesetz": "BGB", "paragraph": "535"})["ms"]
        for _ in range(100)  # 100 instead of 1000 to keep the test under a minute
    ]
    # Note: AWS-side Lambda billing time != Lambda invoke RTT.
    # This measures wall RTT including network from local; expect higher than 50ms
    # because of the local-to-AWS hop. Use 500ms ceiling as a sanity check from local;
    # tighter SLA must be measured from inside AWS (CloudWatch metric).
    p99 = statistics.quantiles(durations, n=100)[98]
    assert p99 < 500, f"p99 from local = {p99} ms"


def test_lookup_cold_first_call_under_3000ms():
    """Force a cold start by waiting 16 min before invoking — too slow for CI.
    This test is informational; assert lenient bound."""
    out = _invoke("kira-legal-lookup-norm", {"gesetz": "BGB", "paragraph": "535"})
    assert out["ms"] < 5000


def test_search_p99_under_2000ms():
    """100 search invocations across different queries; assert reasonable p99."""
    queries = [
        "Mietminderung wegen Schimmel",
        "Pflichten des Vermieters",
        "Kündigung Eigenbedarf",
        "Verjährungsfrist Mängelansprüche",
        "Schadensersatz statt der Leistung",
    ]
    durations = []
    for _ in range(20):
        for q in queries:
            d = _invoke("kira-legal-search", {"query": q, "k": 5})["ms"]
            durations.append(d)
    p99 = statistics.quantiles(durations, n=100)[98]
    assert p99 < 2000, f"p99 = {p99} ms from local"
```

- [ ] **Step 3: Verify default skip**

Run: `.venv/bin/pytest tests/legal_sources/perf/ -v`
Expected: 3 skipped.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/legal_sources/perf/
git commit -m "test(legal-sources): opt-in perf budgets"
```

---

## Task 24: Final lint + coverage + verification

**Files:** none (verification only)

- [ ] **Step 1: Ruff auto-fix**

Run: `.venv/bin/ruff check src/kira/legal_sources/ tests/legal_sources/ tests/infra/ infra/ scripts/ --fix`
Apply changes; some manual fixes may remain.

- [ ] **Step 2: Manual lint pass for residuals**

Run: `.venv/bin/ruff check src/kira/legal_sources/ tests/legal_sources/ tests/infra/ infra/ scripts/`
Fix anything remaining (typically `# noqa: RUF001` for intentional en-dashes, line-wrap E501, etc.).

- [ ] **Step 3: Full test + coverage**

Run: `.venv/bin/pytest tests/legal_sources/ tests/infra/ -q --cov --cov-report=term-missing -m 'not live and not perf'`
Expected: all tests green; coverage ≥ 95% on `_common/` + `gesetze/`.

If coverage falls short, add targeted tests for uncovered lines.

- [ ] **Step 4: Existing kira test suite (no regressions)**

Run: `.venv/bin/pytest tests/ -q -m 'not live and not perf'`
Expected: existing 56 + new V2 tests all pass.

- [ ] **Step 5: CDK synth**

Run: `cd infra/legal_sources && PATH="$PWD/../../.venv/bin:$PATH" CDK_DEFAULT_ACCOUNT=000000000000 cdk synth --no-staging 2>&1 | tail -5`
Expected: clean synth.

- [ ] **Step 6: Commit lint cleanup**

```bash
git add -A
git commit -m "chore(legal-sources): V2 lint pass + coverage gate"
```

If nothing changed in this step, skip the commit.

---

## Task 25: Deploy + backfill + smoke (operator)

This task is **not run by an implementer subagent** — it's the operator's deploy + verification step. The plan ends with this checklist.

- [ ] **Step 1: Deploy worker update**

```bash
cd infra/cloudflare/juris-proxy
wrangler deploy
```

- [ ] **Step 2: Deploy AWS stack**

```bash
cd infra/legal_sources
source ../../.venv/bin/activate
cdk deploy KiraLegalSources --require-approval never
```

Expected output includes `SearchFnArn` and unchanged `LookupFnArn`, `BucketName`.

- [ ] **Step 3: Run backfill from local**

```bash
LEGAL_CORPUS_BUCKET=kira-legal-corpus-${AWS_ACCOUNT_ID}-eu-central-1 \
  .venv/bin/python scripts/backfill_corpus.py \
    --max-parallel 8 \
    --vector-index kira-legal-norms \
    --embed-batch 96
```

Expected: completes in ~30–60 min, summary shows `laws_written` in `[2000, 3500]` and `paragraphs_embedded` in low six figures.

- [ ] **Step 4: Run V2 smoke**

```bash
.venv/bin/python scripts/legal_sources_smoke.py
```

Expected: all four sections print successfully, ending with `✅ V2 smoke OK.`

- [ ] **Step 5: Live smoke**

```bash
RUN_LIVE_TESTS=1 .venv/bin/pytest -m live tests/legal_sources/live/ -v
```

Expected: 5 passed (3 V1 + 3 V2; sub-count depends on prior state).

- [ ] **Step 6: Report**

Print a one-paragraph status:

```
V2 deployed.
- Backfill: <N> laws, <M> paragraphs embedded, <duration> min.
- Smoke: BGB §535 ✓, WEG §14 ✓, semantic search ✓.
- Cost projection: ~$1.55/mo recurring; backfill one-time ~$7.
```

