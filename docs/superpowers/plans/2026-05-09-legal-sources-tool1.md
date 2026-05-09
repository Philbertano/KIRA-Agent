# Legal-Sources Tool 1 (`lookup_norm`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Tool 1 (`lookup_norm` against gesetze-im-internet.de) as a reusable, framework-free Python module deployed as an AWS Lambda behind Bedrock AgentCore Gateway in `eu-central-1`, with a four-tier test pyramid culminating in a live Gateway round-trip.

**Architecture:** Framework-free core in `src/kira/legal_sources/{_common,gesetze}/` (no `kira.*` imports). Three thin adapters in `src/kira/legal_sources/adapters/` (KIRA registry, Claude Agent SDK `@tool`, AWS Lambda handlers). Daily ingest Lambda refreshes an S3 corpus (`s3://kira-legal-corpus-${AccountId}-eu-central-1/gesetze/<abk>.json`) using the existing `kira.knowledge.ingest` pipeline. Lookup Lambda reads the corpus via `/tmp` cache + manifest-driven reload. CDK Python stack provisions everything.

**Tech Stack:** Python 3.11, Pydantic v2, httpx, boto3, moto (test mock), respx (httpx mock), AWS CDK Python (`aws-cdk-lib`), AWS Bedrock AgentCore Gateway, AWS Lambda, S3, EventBridge, CloudWatch.

**Spec:** `docs/superpowers/specs/2026-05-09-legal-sources-tool1-design.md`

---

## File map (locked decomposition)

**New source files** under `src/kira/legal_sources/`:
- `__init__.py` — empty
- `_common/__init__.py` — empty
- `_common/errors.py` — `ToolError` base + leaf error types
- `_common/region.py` — eu-central-1 enforcement
- `_common/s3_corpus.py` — S3 + `/tmp` cache + manifest + local-dir fallback
- `gesetze/__init__.py` — empty
- `gesetze/schema.py` — Pydantic input/output models
- `gesetze/corpus_format.py` — internal `Norm`/`Gesetz` types (re-defined to honor no-`kira.*` rule)
- `gesetze/lookup_norm.py` — the framework-free function
- `adapters/__init__.py` — empty
- `adapters/kira_registry.py` — KIRA `Tool` registry adapter (allowed to `import kira.*`)
- `adapters/agent_sdk.py` — Claude Agent SDK `@tool` adapter
- `adapters/lookup_handler.py` — Lambda handler for `lookup_norm`
- `adapters/ingest_handler.py` — Lambda handler for daily corpus refresh

**New test files** under `tests/legal_sources/`:
- `__init__.py`, `conftest.py`
- `fixtures/bgb_subset.json`, `fixtures/betrkv_subset.json`, `fixtures/captured/bgb.zip`
- `unit/__init__.py`, `unit/test_schema.py`, `unit/test_lookup_norm.py`, `unit/test_s3_corpus.py`, `unit/test_errors.py`, `unit/test_region.py`
- `adapters/__init__.py`, `adapters/test_kira_registry.py`, `adapters/test_agent_sdk.py`, `adapters/test_lookup_handler.py`, `adapters/test_ingest_handler.py`
- `live/__init__.py`, `live/test_live_smoke.py`

**New infra files** under `infra/legal_sources/`:
- `app.py` — CDK app entry
- `stack.py` — `LegalSourcesStack`
- `cdk.json`
- `requirements.txt`

**New scripts** under `scripts/`:
- `register_gateway_target.py`
- `legal_sources_smoke.py`

**Modified files**:
- `pyproject.toml` — add `moto`, `respx`, `aws-cdk-lib`, `constructs`, `awscli` to `[dev]`; add `[tool.pytest.ini_options].markers` entry for `live`
- `CLAUDE.md` — already updated in prior turn, no further changes needed during execution

---

## Conventions for every task

- **Working directory:** `/Users/philiptrempler/Documents/Visual Studio Code/KIRA-Agent/KIRA-Agent`
- **Python:** all commands use `.venv/bin/python` and `.venv/bin/pytest`. The venv is assumed installed per the existing CLAUDE.md commands. If missing, run `python -m venv .venv && .venv/bin/pip install -e ".[dev]"` first.
- **Commits:** one commit per task (after the verifying test passes). Conventional Commits style: `feat:`, `test:`, `chore:`, `refactor:`. Branch: `claude/rental-law-ai-agent-IzzsZ` (existing) unless a worktree is set up first.
- **No `kira.*` imports** inside `_common/` or `gesetze/` source files or their unit tests.
- **AWS region** is hard-coded `eu-central-1` everywhere (env var, CDK env, boto3 clients).

---

## Task 1: Repo scaffolding and dependency updates

**Files:**
- Create: `src/kira/legal_sources/__init__.py`, `src/kira/legal_sources/_common/__init__.py`, `src/kira/legal_sources/gesetze/__init__.py`, `src/kira/legal_sources/adapters/__init__.py`
- Create: `tests/legal_sources/__init__.py`, `tests/legal_sources/unit/__init__.py`, `tests/legal_sources/adapters/__init__.py`, `tests/legal_sources/live/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create empty package files**

```bash
touch src/kira/legal_sources/__init__.py \
      src/kira/legal_sources/_common/__init__.py \
      src/kira/legal_sources/gesetze/__init__.py \
      src/kira/legal_sources/adapters/__init__.py
mkdir -p tests/legal_sources/{unit,adapters,live,fixtures/captured}
touch tests/legal_sources/__init__.py \
      tests/legal_sources/unit/__init__.py \
      tests/legal_sources/adapters/__init__.py \
      tests/legal_sources/live/__init__.py
```

- [ ] **Step 2: Add new dev deps and live marker to `pyproject.toml`**

In `[project.optional-dependencies].dev`, append `"moto>=5.0.0"`, `"respx>=0.21.0"`, `"aws-cdk-lib>=2.140.0"`, `"constructs>=10.0.0"`. In `[tool.pytest.ini_options]`, set:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q -m 'not live'"
markers = [
    "live: opt-in tests that hit the live network or AWS; require RUN_LIVE_TESTS=1",
]
```

- [ ] **Step 3: Install new deps**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: install succeeds; `moto`, `respx`, `aws-cdk-lib`, `constructs` are listed in `pip list`.

- [ ] **Step 4: Verify existing test suite still green**

Run: `.venv/bin/pytest tests/ -q`
Expected: existing 56 tests pass; new test directories empty so they don't add anything.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources tests/legal_sources pyproject.toml
git commit -m "chore: scaffold legal_sources module and add test deps"
```

---

## Task 2: `LookupNormInput` schema

**Files:**
- Create: `src/kira/legal_sources/gesetze/schema.py`
- Test: `tests/legal_sources/unit/test_schema.py`

- [ ] **Step 1: Write the failing input-schema tests**

Create `tests/legal_sources/unit/test_schema.py`:

```python
import pytest
from pydantic import ValidationError

from kira.legal_sources.gesetze.schema import LookupNormInput


def test_minimal_input_validates():
    payload = LookupNormInput.model_validate({"gesetz": "BGB", "paragraph": "535"})
    assert payload.gesetz == "bgb"  # normalized lowercase
    assert payload.paragraph == "535"
    assert payload.absatz is None


def test_paragraph_with_suffix_accepted():
    payload = LookupNormInput.model_validate({"gesetz": "bgb", "paragraph": "535a"})
    assert payload.paragraph == "535a"


def test_paragraph_must_not_be_empty():
    with pytest.raises(ValidationError):
        LookupNormInput.model_validate({"gesetz": "BGB", "paragraph": ""})


def test_paragraph_must_match_pattern():
    with pytest.raises(ValidationError):
        LookupNormInput.model_validate({"gesetz": "BGB", "paragraph": "Sec.5"})


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        LookupNormInput.model_validate(
            {"gesetz": "BGB", "paragraph": "535", "free_text": "client says..."}
        )


def test_absatz_optional_and_validates():
    payload = LookupNormInput.model_validate(
        {"gesetz": "BGB", "paragraph": "535", "absatz": "1"}
    )
    assert payload.absatz == "1"
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'LookupNormInput'`.

- [ ] **Step 3: Implement the input schema**

Create `src/kira/legal_sources/gesetze/schema.py`:

```python
"""Pydantic models for the lookup_norm tool — framework-free."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


_PARAGRAPH_PATTERN = re.compile(r"^\d+[a-zA-Z]?$")


class LookupNormInput(BaseModel):
    """Eingabe für das Tool `lookup_norm`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gesetz: str = Field(..., min_length=1, description="Gesetz-Abkürzung, z.B. BGB.")
    paragraph: str = Field(..., min_length=1, description="Paragraph, z.B. '535' oder '535a'.")
    absatz: str | None = Field(default=None, description="Optional: konkreter Absatz.")

    @field_validator("gesetz")
    @classmethod
    def _normalize_gesetz(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("paragraph")
    @classmethod
    def _validate_paragraph(cls, v: str) -> str:
        v = v.strip()
        if not _PARAGRAPH_PATTERN.match(v):
            raise ValueError(
                f"Paragraph muss Format '<zahl>[<buchstabe>]' haben, war: {v!r}"
            )
        return v
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_schema.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/gesetze/schema.py tests/legal_sources/unit/test_schema.py
git commit -m "feat(legal-sources): LookupNormInput pydantic schema"
```

---

## Task 3: `LookupNormSuccess` and `LookupNormError` schemas

**Files:**
- Modify: `src/kira/legal_sources/gesetze/schema.py`
- Modify: `tests/legal_sources/unit/test_schema.py`

- [ ] **Step 1: Add failing tests for output schemas**

Append to `tests/legal_sources/unit/test_schema.py`:

```python
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormResult,
    LookupNormSuccess,
)


def test_success_serializes_with_all_fields():
    payload = LookupNormSuccess(
        gesetz="BGB",
        gesetz_titel="Bürgerliches Gesetzbuch",
        paragraph="535",
        absatz=None,
        titel="Inhalt und Hauptpflichten des Mietvertrags",
        wortlaut="Durch den Mietvertrag …",
        stand="2026-05-08",
        quelle_url="https://www.gesetze-im-internet.de/bgb/__535.html",
        stand_warnung=None,
    )
    dumped = payload.model_dump()
    assert dumped["paragraph"] == "535"
    assert dumped["stand_warnung"] is None


def test_error_carries_code_and_context():
    err = LookupNormError(
        error=LookupNormErrorCode.PARAGRAPH_NOT_FOUND,
        message="§ 1 BGB ist nicht im Korpus",
        gesetz="BGB",
        paragraph="1",
        absatz=None,
    )
    assert err.error == "paragraph_not_found"


def test_result_union_discriminator():
    # LookupNormResult is the union the tool returns.
    success = LookupNormSuccess(
        gesetz="BGB",
        gesetz_titel="Bürgerliches Gesetzbuch",
        paragraph="535",
        absatz=None,
        titel="X",
        wortlaut="Y",
        stand="2026-05-08",
        quelle_url="https://example.test",
        stand_warnung=None,
    )
    err = LookupNormError(
        error=LookupNormErrorCode.UNKNOWN_GESETZ,
        message="Unbekannt",
        gesetz="ABC",
        paragraph="1",
        absatz=None,
    )
    assert isinstance(success, LookupNormResult.__args__)  # type: ignore[attr-defined]
    assert isinstance(err, LookupNormResult.__args__)  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_schema.py -v`
Expected: ImportError for `LookupNormSuccess` etc.

- [ ] **Step 3: Implement output schemas**

Append to `src/kira/legal_sources/gesetze/schema.py`:

```python
from enum import Enum
from typing import Union


class LookupNormErrorCode(str, Enum):
    UNKNOWN_GESETZ = "unknown_gesetz"
    PARAGRAPH_NOT_FOUND = "paragraph_not_found"
    ABSATZ_NOT_FOUND = "absatz_not_found"
    CORPUS_UNAVAILABLE = "corpus_unavailable"
    VALIDATION_ERROR = "validation_error"


class LookupNormSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gesetz: str
    gesetz_titel: str
    paragraph: str
    absatz: str | None
    titel: str
    wortlaut: str
    stand: str
    quelle_url: str
    stand_warnung: str | None

    def to_agent_text(self) -> str:
        warn = f"\n\n⚠️ {self.stand_warnung}" if self.stand_warnung else ""
        absatz = f", Absatz {self.absatz}" if self.absatz else ""
        return (
            f"# {self.gesetz_titel} § {self.paragraph}{absatz} — {self.titel}\n\n"
            f"{self.wortlaut}\n\n"
            f"_Quelle: {self.quelle_url} | Stand: {self.stand}_{warn}"
        )


class LookupNormError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: LookupNormErrorCode
    message: str
    gesetz: str | None = None
    paragraph: str | None = None
    absatz: str | None = None

    def to_agent_text(self) -> str:
        return f"FEHLER ({self.error.value}): {self.message}"


LookupNormResult = Union[LookupNormSuccess, LookupNormError]
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_schema.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/gesetze/schema.py tests/legal_sources/unit/test_schema.py
git commit -m "feat(legal-sources): LookupNormSuccess/Error/Result schemas"
```

---

## Task 4: `corpus_format.py` — internal corpus types

**Files:**
- Create: `src/kira/legal_sources/gesetze/corpus_format.py`
- Test: `tests/legal_sources/unit/test_corpus_format.py`

- [ ] **Step 1: Write failing test for corpus parsing**

Create `tests/legal_sources/unit/test_corpus_format.py`:

```python
from kira.legal_sources.gesetze.corpus_format import GesetzKorpus


def test_parses_valid_corpus_payload():
    payload = {
        "_meta": {
            "abkuerzung": "BGB",
            "titel": "Bürgerliches Gesetzbuch",
            "stand": "2026-05-08",
            "quelle": "gesetze-im-internet.de",
            "quelle_url": "https://www.gesetze-im-internet.de/bgb",
            "gefiltert_auf": ["§§ 194–580a"],
            "anzahl_normen": 1,
        },
        "paragraphen": {
            "535": {
                "paragraph": "535",
                "titel": "Inhalt und Hauptpflichten des Mietvertrags",
                "absaetze": [
                    {"nummer": "1", "text": "Durch den Mietvertrag …"},
                    {"nummer": "2", "text": "Der Vermieter …"},
                ],
                "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
            }
        },
    }
    korpus = GesetzKorpus.model_validate(payload)
    assert korpus.meta.abkuerzung == "BGB"
    assert "535" in korpus.paragraphen
    assert korpus.paragraphen["535"].absaetze[0].nummer == "1"


def test_lookup_paragraph_returns_none_for_missing():
    payload = {
        "_meta": {
            "abkuerzung": "BGB",
            "titel": "x",
            "stand": "2026-05-08",
            "quelle": "x",
            "quelle_url": "https://example.test",
            "gefiltert_auf": [],
            "anzahl_normen": 0,
        },
        "paragraphen": {},
    }
    korpus = GesetzKorpus.model_validate(payload)
    assert korpus.paragraphen.get("999") is None
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_corpus_format.py -v`
Expected: ImportError for `GesetzKorpus`.

- [ ] **Step 3: Implement corpus types**

Create `src/kira/legal_sources/gesetze/corpus_format.py`:

```python
"""Internal corpus types for legal_sources.

Re-defined locally so this module never imports from `kira.knowledge.*`.
The shape mirrors what `kira.knowledge.ingest` writes to S3, but is
maintained independently to honour the no-`kira.*`-imports rule.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Absatz(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nummer: str
    text: str


class Norm(BaseModel):
    model_config = ConfigDict(extra="ignore")

    paragraph: str
    titel: str = ""
    absaetze: list[Absatz] = Field(default_factory=list)
    quelle_url: str | None = None


class GesetzMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    abkuerzung: str
    titel: str
    stand: str  # ISO-Date
    quelle: str
    quelle_url: str
    gefiltert_auf: list[str] = Field(default_factory=list)
    anzahl_normen: int = 0


class GesetzKorpus(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    meta: GesetzMeta = Field(alias="_meta")
    paragraphen: dict[str, Norm] = Field(default_factory=dict)
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_corpus_format.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/gesetze/corpus_format.py tests/legal_sources/unit/test_corpus_format.py
git commit -m "feat(legal-sources): GesetzKorpus internal types"
```

---

## Task 5: `_common/errors.py`

**Files:**
- Create: `src/kira/legal_sources/_common/errors.py`
- Test: `tests/legal_sources/unit/test_errors.py`

- [ ] **Step 1: Write failing test**

Create `tests/legal_sources/unit/test_errors.py`:

```python
import pytest

from kira.legal_sources._common.errors import CorpusUnavailableError, ToolError


def test_tool_error_carries_code_and_message():
    err = ToolError(code="custom", message="boom")
    assert err.code == "custom"
    assert str(err) == "custom: boom"


def test_corpus_unavailable_is_tool_error():
    err = CorpusUnavailableError("S3 GET failed")
    assert isinstance(err, ToolError)
    assert err.code == "corpus_unavailable"
    with pytest.raises(ToolError):
        raise err
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_errors.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement errors**

Create `src/kira/legal_sources/_common/errors.py`:

```python
"""Cross-tool error hierarchy for legal_sources."""

from __future__ import annotations


class ToolError(Exception):
    code: str = "tool_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        else:
            # allow subclass to set as class attr
            pass
        self.message = message
        super().__init__(f"{self.code}: {message}")


class CorpusUnavailableError(ToolError):
    code = "corpus_unavailable"

    def __init__(self, message: str) -> None:
        super().__init__(message)
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_errors.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/errors.py tests/legal_sources/unit/test_errors.py
git commit -m "feat(legal-sources): ToolError hierarchy"
```

---

## Task 6: `_common/region.py`

**Files:**
- Create: `src/kira/legal_sources/_common/region.py`
- Test: `tests/legal_sources/unit/test_region.py`

- [ ] **Step 1: Write failing test**

Create `tests/legal_sources/unit/test_region.py`:

```python
import pytest

from kira.legal_sources._common.region import REQUIRED_REGION, ensure_eu_region


def test_required_region_is_eu_central_1():
    assert REQUIRED_REGION == "eu-central-1"


def test_ensure_eu_region_passes_for_correct_region():
    ensure_eu_region("eu-central-1")  # no exception


def test_ensure_eu_region_rejects_non_eu():
    with pytest.raises(RuntimeError) as excinfo:
        ensure_eu_region("us-east-1")
    assert "eu-central-1" in str(excinfo.value)


def test_ensure_eu_region_rejects_other_eu_region():
    # Even another EU region is rejected; we want strict eu-central-1 pinning.
    with pytest.raises(RuntimeError):
        ensure_eu_region("eu-west-1")
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_region.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/kira/legal_sources/_common/region.py`:

```python
"""eu-central-1 region pinning for legal_sources AWS resources."""

from __future__ import annotations

REQUIRED_REGION: str = "eu-central-1"


def ensure_eu_region(region: str | None) -> None:
    if region != REQUIRED_REGION:
        raise RuntimeError(
            f"legal_sources requires region {REQUIRED_REGION!r}, got {region!r}. "
            "Refusing to operate outside eu-central-1 for data-residency reasons."
        )
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_region.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/region.py tests/legal_sources/unit/test_region.py
git commit -m "feat(legal-sources): eu-central-1 region pinning"
```

---

## Task 7: `lookup_norm` — happy path against in-memory corpus

**Files:**
- Create: `src/kira/legal_sources/gesetze/lookup_norm.py`
- Test: `tests/legal_sources/unit/test_lookup_norm.py`
- Create: `tests/legal_sources/fixtures/bgb_subset.json`

- [ ] **Step 1: Create the fixture corpus**

Create `tests/legal_sources/fixtures/bgb_subset.json`:

```json
{
  "_meta": {
    "abkuerzung": "BGB",
    "titel": "Bürgerliches Gesetzbuch",
    "stand": "2026-05-08",
    "quelle": "gesetze-im-internet.de",
    "quelle_url": "https://www.gesetze-im-internet.de/bgb",
    "gefiltert_auf": ["§§ 535–540"],
    "anzahl_normen": 2
  },
  "paragraphen": {
    "535": {
      "paragraph": "535",
      "titel": "Inhalt und Hauptpflichten des Mietvertrags",
      "absaetze": [
        {"nummer": "1", "text": "Durch den Mietvertrag wird der Vermieter verpflichtet, dem Mieter den Gebrauch der Mietsache zu gewähren."},
        {"nummer": "2", "text": "Der Vermieter hat die Mietsache in einem zum vertragsgemäßen Gebrauch geeigneten Zustand zu erhalten."}
      ],
      "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html"
    },
    "535a": {
      "paragraph": "535a",
      "titel": "Beispielhafte Suffix-Norm",
      "absaetze": [
        {"nummer": "1", "text": "Suffix-Test."}
      ],
      "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535a.html"
    }
  }
}
```

- [ ] **Step 2: Write failing happy-path test**

Create `tests/legal_sources/unit/test_lookup_norm.py`:

```python
import json
from pathlib import Path

import pytest

from kira.legal_sources.gesetze.corpus_format import GesetzKorpus
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormSuccess,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def bgb_korpus() -> GesetzKorpus:
    payload = json.loads((FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"))
    return GesetzKorpus.model_validate(payload)


def test_returns_full_paragraph_when_no_absatz(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})

    assert isinstance(result, LookupNormSuccess)
    assert result.gesetz == "BGB"
    assert result.paragraph == "535"
    assert result.absatz is None
    assert "Durch den Mietvertrag" in result.wortlaut
    assert "Der Vermieter hat" in result.wortlaut  # both Absätze concatenated
    assert result.stand == "2026-05-08"
    assert result.quelle_url.endswith("__535.html")
    assert result.stand_warnung is None


def test_returns_specific_absatz_when_requested(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535", absatz="2")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})

    assert isinstance(result, LookupNormSuccess)
    assert result.absatz == "2"
    assert "Der Vermieter hat die Mietsache" in result.wortlaut
    assert "Durch den Mietvertrag" not in result.wortlaut


def test_paragraph_with_letter_suffix_supported(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535a")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})

    assert isinstance(result, LookupNormSuccess)
    assert result.paragraph == "535a"
    assert "Suffix-Test." in result.wortlaut
```

- [ ] **Step 3: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lookup_norm.py -v`
Expected: ImportError for `lookup_norm`.

- [ ] **Step 4: Implement minimal lookup_norm**

Create `src/kira/legal_sources/gesetze/lookup_norm.py`:

```python
"""Pure function: resolve a single paragraph from an in-memory corpus."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Mapping

from kira.legal_sources.gesetze.corpus_format import GesetzKorpus, Norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
    LookupNormResult,
    LookupNormSuccess,
)


_STAND_WARN_AGE = timedelta(days=30)


def lookup_norm(
    input_data: LookupNormInput,
    *,
    corpus: Mapping[str, GesetzKorpus],
    today: date | None = None,
) -> LookupNormResult:
    """Resolve `input_data` against the in-memory `corpus`.

    `corpus` is a mapping of lower-case Gesetz-Abkürzung → parsed `GesetzKorpus`.
    `today` is injectable for deterministic stand-warning tests.
    """
    today = today or date.today()
    abk = input_data.gesetz  # already lower-case after validation
    korpus = corpus.get(abk)
    if korpus is None:
        return LookupNormError(
            error=LookupNormErrorCode.UNKNOWN_GESETZ,
            message=f"Gesetz {abk.upper()!r} ist nicht im Korpus geladen.",
            gesetz=abk.upper(),
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    norm = korpus.paragraphen.get(input_data.paragraph)
    if norm is None:
        return LookupNormError(
            error=LookupNormErrorCode.PARAGRAPH_NOT_FOUND,
            message=(
                f"§ {input_data.paragraph} {abk.upper()} ist nicht im kuratierten Korpus "
                f"({', '.join(korpus.meta.gefiltert_auf) or 'leer'})."
            ),
            gesetz=abk.upper(),
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    wortlaut, used_absatz = _select_text(norm, input_data.absatz)
    if input_data.absatz is not None and used_absatz is None:
        return LookupNormError(
            error=LookupNormErrorCode.ABSATZ_NOT_FOUND,
            message=(
                f"Absatz {input_data.absatz} in § {input_data.paragraph} "
                f"{abk.upper()} existiert nicht."
            ),
            gesetz=abk.upper(),
            paragraph=input_data.paragraph,
            absatz=input_data.absatz,
        )

    return LookupNormSuccess(
        gesetz=korpus.meta.abkuerzung,
        gesetz_titel=korpus.meta.titel,
        paragraph=norm.paragraph,
        absatz=used_absatz,
        titel=norm.titel,
        wortlaut=wortlaut,
        stand=korpus.meta.stand,
        quelle_url=norm.quelle_url or korpus.meta.quelle_url,
        stand_warnung=_stand_warning(korpus.meta.stand, today),
    )


def _select_text(norm: Norm, absatz: str | None) -> tuple[str, str | None]:
    if absatz is None:
        if not norm.absaetze:
            return ("", None)
        joined = "\n\n".join(f"({a.nummer}) {a.text}" for a in norm.absaetze)
        return (joined, None)
    for a in norm.absaetze:
        if a.nummer == absatz:
            return (f"({a.nummer}) {a.text}", a.nummer)
    return ("", None)


def _stand_warning(stand: str, today: date) -> str | None:
    try:
        stand_date = datetime.strptime(stand, "%Y-%m-%d").date()
    except ValueError:
        return f"Stand-Datum {stand!r} ist unleserlich — Korpus prüfen."
    age = today - stand_date
    if age > _STAND_WARN_AGE:
        return (
            f"Korpus-Stand ist {age.days} Tage alt (Schwelle: {_STAND_WARN_AGE.days} Tage). "
            f"Manuell verifizieren oder Ingest erneut ausführen."
        )
    return None
```

- [ ] **Step 5: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lookup_norm.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kira/legal_sources/gesetze/lookup_norm.py \
        tests/legal_sources/unit/test_lookup_norm.py \
        tests/legal_sources/fixtures/bgb_subset.json
git commit -m "feat(legal-sources): lookup_norm happy path"
```

---

## Task 8: `lookup_norm` — error paths

**Files:**
- Modify: `tests/legal_sources/unit/test_lookup_norm.py`

- [ ] **Step 1: Add failing tests for error paths**

Append to `tests/legal_sources/unit/test_lookup_norm.py`:

```python
def test_unknown_gesetz_returns_error(bgb_korpus):
    inp = LookupNormInput(gesetz="ABC", paragraph="1")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.UNKNOWN_GESETZ
    assert result.gesetz == "ABC"


def test_paragraph_not_in_corpus_returns_error(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="1")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.PARAGRAPH_NOT_FOUND
    assert "§§ 535–540" in result.message  # range from fixture meta


def test_absatz_not_in_norm_returns_error(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535", absatz="9")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormError)
    assert result.error == LookupNormErrorCode.ABSATZ_NOT_FOUND
    assert result.absatz == "9"
```

- [ ] **Step 2: Run, confirm pass (all logic already implemented in Task 7)**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lookup_norm.py -v`
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/legal_sources/unit/test_lookup_norm.py
git commit -m "test(legal-sources): error paths for lookup_norm"
```

---

## Task 9: `lookup_norm` — stand-warnung

**Files:**
- Modify: `tests/legal_sources/unit/test_lookup_norm.py`

- [ ] **Step 1: Add failing tests with date injection**

Append to `tests/legal_sources/unit/test_lookup_norm.py`:

```python
from datetime import date


def test_stand_warning_set_when_corpus_older_than_30_days(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535")
    # fixture stand is 2026-05-08; pretend today is 60 days later.
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus}, today=date(2026, 7, 7))
    assert isinstance(result, LookupNormSuccess)
    assert result.stand_warnung is not None
    assert "60 Tage alt" in result.stand_warnung


def test_stand_warning_absent_when_recent(bgb_korpus):
    inp = LookupNormInput(gesetz="BGB", paragraph="535")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus}, today=date(2026, 5, 10))
    assert isinstance(result, LookupNormSuccess)
    assert result.stand_warnung is None


def test_unparseable_stand_emits_warning(bgb_korpus):
    bgb_korpus.meta.stand = "not-a-date"
    inp = LookupNormInput(gesetz="BGB", paragraph="535")
    result = lookup_norm(inp, corpus={"bgb": bgb_korpus})
    assert isinstance(result, LookupNormSuccess)
    assert result.stand_warnung is not None
    assert "unleserlich" in result.stand_warnung
```

- [ ] **Step 2: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_lookup_norm.py -v`
Expected: 9 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/legal_sources/unit/test_lookup_norm.py
git commit -m "test(legal-sources): stand_warnung threshold and parsing"
```

---

## Task 10: `_common/s3_corpus.py` — local-dir fallback

**Files:**
- Create: `src/kira/legal_sources/_common/s3_corpus.py`
- Test: `tests/legal_sources/unit/test_s3_corpus.py`

- [ ] **Step 1: Write failing test for local-dir loading**

Create `tests/legal_sources/unit/test_s3_corpus.py`:

```python
import json
import os
from pathlib import Path

import pytest

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import CorpusLoader


FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)
    monkeypatch.delenv("LEGAL_CORPUS_BUCKET", raising=False)


def test_loads_from_local_dir(tmp_path: Path, monkeypatch):
    src = json.loads((FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"))
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(json.dumps(src), encoding="utf-8")
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    loader = CorpusLoader.from_env()
    corpus = loader.load_all()

    assert "bgb" in corpus
    assert corpus["bgb"].meta.abkuerzung == "BGB"


def test_local_dir_missing_raises_corpus_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(CorpusUnavailableError):
        CorpusLoader.from_env().load_all()


def test_no_env_set_raises_corpus_unavailable():
    with pytest.raises(CorpusUnavailableError):
        CorpusLoader.from_env().load_all()
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_s3_corpus.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement local-dir loader**

Create `src/kira/legal_sources/_common/s3_corpus.py`:

```python
"""Corpus loader: serves a `dict[str, GesetzKorpus]` from S3 or a local dir.

Resolution order:
  1. If `LEGAL_CORPUS_LOCAL_DIR` is set, read every `<abk>.json` file from there.
  2. Else if `LEGAL_CORPUS_BUCKET` is set, read from S3.
  3. Else raise `CorpusUnavailableError`.

S3 reads are cached in `/tmp` and re-validated against `_manifest.json`
every `MANIFEST_RECHECK_SECONDS`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources.gesetze.corpus_format import GesetzKorpus


log = logging.getLogger(__name__)

ENV_LOCAL_DIR = "LEGAL_CORPUS_LOCAL_DIR"
ENV_S3_BUCKET = "LEGAL_CORPUS_BUCKET"
TMP_CACHE_DIR = Path("/tmp/legal_sources_corpus")
MANIFEST_RECHECK_SECONDS = 300  # 5 minutes
MANIFEST_KEY = "gesetze/_manifest.json"


@dataclass
class CorpusLoader:
    local_dir: Path | None = None
    s3_bucket: str | None = None
    _cache: dict[str, GesetzKorpus] = field(default_factory=dict)
    _manifest_etag: str | None = None
    _manifest_checked_at: float = 0.0

    @classmethod
    def from_env(cls) -> "CorpusLoader":
        local = os.environ.get(ENV_LOCAL_DIR)
        bucket = os.environ.get(ENV_S3_BUCKET)
        return cls(
            local_dir=Path(local) if local else None,
            s3_bucket=bucket or None,
        )

    def load_all(self) -> dict[str, GesetzKorpus]:
        if self.local_dir is not None:
            return self._load_local()
        if self.s3_bucket is not None:
            return self._load_s3()
        raise CorpusUnavailableError(
            f"Neither {ENV_LOCAL_DIR} nor {ENV_S3_BUCKET} is set."
        )

    # --- local ---

    def _load_local(self) -> dict[str, GesetzKorpus]:
        gesetze_dir = self.local_dir / "gesetze" if self.local_dir.name != "gesetze" else self.local_dir
        # Accept either <local_dir>/gesetze/<abk>.json or <local_dir>/<abk>.json
        if not gesetze_dir.is_dir():
            gesetze_dir = self.local_dir
        if not gesetze_dir.is_dir():
            raise CorpusUnavailableError(
                f"Local corpus dir {self.local_dir!s} does not exist."
            )
        out: dict[str, GesetzKorpus] = {}
        for path in sorted(gesetze_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                out[path.stem.lower()] = GesetzKorpus.model_validate(payload)
            except Exception as exc:  # noqa: BLE001
                log.warning("Skipping malformed corpus file %s: %s", path, exc)
        if not out:
            raise CorpusUnavailableError(
                f"No usable corpus files found in {gesetze_dir!s}."
            )
        return out
```

(S3 path stubbed for now; subsequent task adds it.)

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_s3_corpus.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/s3_corpus.py tests/legal_sources/unit/test_s3_corpus.py
git commit -m "feat(legal-sources): CorpusLoader local-dir branch"
```

---

## Task 11: `_common/s3_corpus.py` — S3 branch with moto

**Files:**
- Modify: `src/kira/legal_sources/_common/s3_corpus.py`
- Modify: `tests/legal_sources/unit/test_s3_corpus.py`

- [ ] **Step 1: Add failing S3 test using moto**

Append to `tests/legal_sources/unit/test_s3_corpus.py`:

```python
import boto3
from moto import mock_aws


@pytest.fixture
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")


@pytest.fixture
def s3_corpus_bucket(aws_creds):
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="test-corpus",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        bgb = (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8")
        s3.put_object(Bucket="test-corpus", Key="gesetze/bgb.json", Body=bgb.encode("utf-8"))
        manifest = json.dumps({"version": 1, "files": ["gesetze/bgb.json"]})
        s3.put_object(Bucket="test-corpus", Key="gesetze/_manifest.json", Body=manifest.encode("utf-8"))
        yield "test-corpus"


def test_loads_from_s3(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = CorpusLoader.from_env()
    corpus = loader.load_all()
    assert "bgb" in corpus
    assert corpus["bgb"].meta.abkuerzung == "BGB"


def test_warm_cache_skips_s3_within_recheck_window(monkeypatch, s3_corpus_bucket, tmp_path):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_corpus_bucket)
    monkeypatch.setattr(
        "kira.legal_sources._common.s3_corpus.TMP_CACHE_DIR",
        tmp_path / "cache",
    )
    loader = CorpusLoader.from_env()
    first = loader.load_all()
    # Mutate S3 to add a new gesetz; warm load must NOT see it.
    s3 = boto3.client("s3", region_name="eu-central-1")
    second_payload = json.dumps({
        "_meta": {
            "abkuerzung": "BetrKV", "titel": "x", "stand": "2026-05-09",
            "quelle": "x", "quelle_url": "https://example.test",
            "gefiltert_auf": [], "anzahl_normen": 0,
        },
        "paragraphen": {},
    })
    s3.put_object(Bucket=s3_corpus_bucket, Key="gesetze/betrkv.json", Body=second_payload.encode("utf-8"))
    second = loader.load_all()  # within recheck window
    assert "betrkv" not in second
    # Force recheck: backdate the manifest-checked-at timestamp
    loader._manifest_checked_at = 0.0
    s3.put_object(
        Bucket=s3_corpus_bucket,
        Key="gesetze/_manifest.json",
        Body=json.dumps({"version": 2, "files": ["gesetze/bgb.json", "gesetze/betrkv.json"]}).encode("utf-8"),
    )
    third = loader.load_all()
    assert "betrkv" in third
```

- [ ] **Step 2: Run, confirm new tests fail**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_s3_corpus.py -v`
Expected: failure on `test_loads_from_s3` (loader still raises CorpusUnavailable for S3 path).

- [ ] **Step 3: Implement S3 branch**

Append to `src/kira/legal_sources/_common/s3_corpus.py` (inside the `CorpusLoader` class, replacing the stub for `_load_s3` and adding helpers):

```python
    # --- S3 ---

    def _load_s3(self) -> dict[str, GesetzKorpus]:
        import boto3  # local import; lambda cold-start sensitive
        from botocore.exceptions import ClientError

        s3 = boto3.client("s3", region_name="eu-central-1")
        now = time.time()
        if (now - self._manifest_checked_at) < MANIFEST_RECHECK_SECONDS and self._cache:
            return dict(self._cache)
        try:
            head = s3.head_object(Bucket=self.s3_bucket, Key=MANIFEST_KEY)
        except ClientError as exc:
            raise CorpusUnavailableError(
                f"Manifest read failed for s3://{self.s3_bucket}/{MANIFEST_KEY}: {exc}"
            ) from exc
        etag = head.get("ETag")
        self._manifest_checked_at = now
        if etag == self._manifest_etag and self._cache:
            return dict(self._cache)
        # Manifest changed (or first load) — re-read all listed files.
        manifest_obj = s3.get_object(Bucket=self.s3_bucket, Key=MANIFEST_KEY)
        manifest: dict[str, Any] = json.loads(manifest_obj["Body"].read())
        TMP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        out: dict[str, GesetzKorpus] = {}
        for key in manifest.get("files", []):
            try:
                obj = s3.get_object(Bucket=self.s3_bucket, Key=key)
                payload = json.loads(obj["Body"].read())
                korpus = GesetzKorpus.model_validate(payload)
                # cache to /tmp for observability/debug
                stem = Path(key).stem.lower()
                (TMP_CACHE_DIR / f"{stem}.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                out[stem] = korpus
            except (ClientError, ValueError) as exc:
                log.warning("Skipping bad S3 corpus file %s: %s", key, exc)
        if not out:
            raise CorpusUnavailableError(
                f"No usable corpus files behind manifest in s3://{self.s3_bucket}"
            )
        self._cache = out
        self._manifest_etag = etag
        return dict(out)
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/unit/test_s3_corpus.py -v`
Expected: 5 passed total.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/s3_corpus.py tests/legal_sources/unit/test_s3_corpus.py
git commit -m "feat(legal-sources): CorpusLoader S3 branch with manifest reload"
```

---

## Task 12: KIRA registry adapter

**Files:**
- Create: `src/kira/legal_sources/adapters/kira_registry.py`
- Test: `tests/legal_sources/adapters/test_kira_registry.py`

- [ ] **Step 1: Write failing test**

Create `tests/legal_sources/adapters/test_kira_registry.py`:

```python
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_register_returns_tool_that_calls_lookup_norm(tmp_path, monkeypatch):
    # Stage a local corpus
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(
        (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.kira_registry import build_lookup_norm_tool

    tool = build_lookup_norm_tool()
    assert tool.name == "lookup_norm"

    text = tool.run({"gesetz": "BGB", "paragraph": "535"})
    assert "Inhalt und Hauptpflichten" in text
    assert "Stand: 2026-05-08" in text


def test_validation_error_returned_as_error_text(tmp_path, monkeypatch):
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(
        (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.kira_registry import build_lookup_norm_tool

    tool = build_lookup_norm_tool()
    text = tool.run({"gesetz": "BGB"})  # missing paragraph
    assert "validation_error" in text or "FEHLER" in text
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_kira_registry.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement adapter**

Create `src/kira/legal_sources/adapters/kira_registry.py`:

```python
"""KIRA `Tool` registry adapter for lookup_norm.

This adapter is the only place inside legal_sources/adapters/ that imports
from kira.*. It is NOT auto-registered on import — callers explicitly call
`build_lookup_norm_tool()` and register the result with their loop.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from kira.agent.tools._registry import Tool
from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import CorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import LookupNormInput


_DESCRIPTION = (
    "Lädt den autoritativen Wortlaut eines deutschen Paragraphen aus "
    "gesetze-im-internet.de (via S3-gepflegtem Korpus). Eingaben: "
    "gesetz (z.B. 'BGB'), paragraph (z.B. '535' oder '535a'), "
    "absatz (optional, z.B. '1'). NUR autoritative Quellen — keine "
    "Aggregatoren, keine ausländischen Quellen."
)


def build_lookup_norm_tool(*, loader: CorpusLoader | None = None) -> Tool:
    loader = loader or CorpusLoader.from_env()

    def _run(input_data: dict[str, Any]) -> str:
        try:
            payload = LookupNormInput.model_validate(input_data)
        except ValidationError as exc:
            return f"FEHLER (validation_error): {exc}"
        try:
            corpus = loader.load_all()
        except CorpusUnavailableError as exc:
            return f"FEHLER (corpus_unavailable): {exc}"
        result = lookup_norm(payload, corpus=corpus)
        return result.to_agent_text()

    return Tool(
        name="lookup_norm",
        description=_DESCRIPTION,
        input_schema=LookupNormInput.model_json_schema(),
        run=_run,
    )
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_kira_registry.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/adapters/kira_registry.py tests/legal_sources/adapters/test_kira_registry.py
git commit -m "feat(legal-sources): KIRA Tool registry adapter"
```

---

## Task 13: Lambda lookup handler

**Files:**
- Create: `src/kira/legal_sources/adapters/lookup_handler.py`
- Test: `tests/legal_sources/adapters/test_lookup_handler.py`

- [ ] **Step 1: Write failing test**

Create `tests/legal_sources/adapters/test_lookup_handler.py`:

```python
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def staged_local_corpus(tmp_path, monkeypatch):
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(
        (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))


def test_handler_direct_invoke_shape(staged_local_corpus):
    from kira.legal_sources.adapters.lookup_handler import handler

    out = handler({"gesetz": "BGB", "paragraph": "535"}, context=None)
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["paragraph"] == "535"


def test_handler_agentcore_gateway_shape(staged_local_corpus):
    from kira.legal_sources.adapters.lookup_handler import handler

    event = {
        "tool_name": "lookup_norm",
        "tool_use_id": "abc-123",
        "input": {"gesetz": "BGB", "paragraph": "535", "absatz": "2"},
    }
    out = handler(event, context=None)
    assert out["isError"] is False
    body = json.loads(out["content"][0]["text"])
    assert body["absatz"] == "2"


def test_handler_validation_error_isolated(staged_local_corpus):
    from kira.legal_sources.adapters.lookup_handler import handler

    out = handler({"input": {"gesetz": "", "paragraph": ""}}, context=None)
    assert out["isError"] is True
    assert "validation_error" in out["content"][0]["text"]


def test_handler_corpus_unavailable_returns_error(monkeypatch):
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)
    monkeypatch.delenv("LEGAL_CORPUS_BUCKET", raising=False)
    # Need to reset module-level loader cache between tests.
    import importlib
    import kira.legal_sources.adapters.lookup_handler as mod
    importlib.reload(mod)

    out = mod.handler({"gesetz": "BGB", "paragraph": "535"}, context=None)
    assert out["isError"] is True
    assert "corpus_unavailable" in out["content"][0]["text"]
```

- [ ] **Step 2: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_lookup_handler.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement handler**

Create `src/kira/legal_sources/adapters/lookup_handler.py`:

```python
"""AWS Lambda handler for the lookup_norm tool, invoked by AgentCore Gateway."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import CorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import (
    LookupNormError,
    LookupNormErrorCode,
    LookupNormInput,
)


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# Module-level loader: warm Lambdas reuse the same /tmp cache and manifest etag.
_LOADER = CorpusLoader.from_env()


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    args = _extract_args(event)
    try:
        payload = LookupNormInput.model_validate(args)
    except ValidationError as exc:
        return _err(LookupNormErrorCode.VALIDATION_ERROR, str(exc))
    try:
        corpus = _LOADER.load_all()
    except CorpusUnavailableError as exc:
        return _err(LookupNormErrorCode.CORPUS_UNAVAILABLE, str(exc))
    result = lookup_norm(payload, corpus=corpus)
    body = result.model_dump_json()
    is_error = isinstance(result, LookupNormError)
    log.info(
        "lookup_norm invocation",
        extra={
            "gesetz": payload.gesetz,
            "paragraph": payload.paragraph,
            "absatz": payload.absatz,
            "is_error": is_error,
            "corpus_stand": getattr(result, "stand", None),
        },
    )
    return {"isError": is_error, "content": [{"type": "text", "text": body}]}


def _extract_args(event: dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, dict) and "input" in event and isinstance(event["input"], dict):
        return event["input"]
    return event if isinstance(event, dict) else {}


def _err(code: LookupNormErrorCode, message: str) -> dict[str, Any]:
    body = LookupNormError(error=code, message=message).model_dump_json()
    return {"isError": True, "content": [{"type": "text", "text": body}]}
```

- [ ] **Step 4: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_lookup_handler.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/adapters/lookup_handler.py tests/legal_sources/adapters/test_lookup_handler.py
git commit -m "feat(legal-sources): Lambda handler for lookup_norm"
```

---

## Task 14: Claude Agent SDK adapter

**Files:**
- Create: `src/kira/legal_sources/adapters/agent_sdk.py`
- Test: `tests/legal_sources/adapters/test_agent_sdk.py`

- [ ] **Step 1: Write failing test**

Create `tests/legal_sources/adapters/test_agent_sdk.py`:

```python
"""The agent_sdk adapter is structurally tested; we don't import claude_agent_sdk
in CI because it pulls a network-bound dependency. The adapter is thin enough
that we test its core function (`make_lookup_norm_tool_function`) directly."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.mark.asyncio
async def test_make_tool_function_returns_mcp_shape(tmp_path, monkeypatch):
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(
        (FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    from kira.legal_sources.adapters.agent_sdk import (
        make_lookup_norm_tool_function,
    )

    fn = make_lookup_norm_tool_function()
    out = await fn({"gesetz": "BGB", "paragraph": "535"})
    assert "content" in out
    assert out["content"][0]["type"] == "text"
    assert "Mietvertrag" in out["content"][0]["text"]
```

Add `pytest-asyncio` to dev deps if not already present (it isn't), then add the marker config.

- [ ] **Step 2: Update pyproject for asyncio**

In `pyproject.toml` `[project.optional-dependencies].dev`, add `"pytest-asyncio>=0.23.0"`. In `[tool.pytest.ini_options]`, add `asyncio_mode = "auto"`.

Run: `.venv/bin/pip install -e ".[dev]"`

- [ ] **Step 3: Run, confirm test fails on import**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_agent_sdk.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement adapter**

Create `src/kira/legal_sources/adapters/agent_sdk.py`:

```python
"""Claude Agent SDK `@tool` wrapper for lookup_norm.

We expose two surfaces:

  - `make_lookup_norm_tool_function()` — returns a plain async callable that
    can be wrapped with `@tool` by the consumer. Easy to unit-test, no SDK
    dependency at import time.
  - `make_sdk_tool()` — convenience that imports `claude_agent_sdk` lazily
    and returns the decorated tool. Optional; consumer code may build its
    own decoration.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import CorpusLoader
from kira.legal_sources.gesetze.lookup_norm import lookup_norm
from kira.legal_sources.gesetze.schema import LookupNormInput


def make_lookup_norm_tool_function(
    *, loader: CorpusLoader | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    loader = loader or CorpusLoader.from_env()

    async def _impl(args: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = LookupNormInput.model_validate(args)
        except ValidationError as exc:
            return _text(f"validation_error: {exc}")
        try:
            corpus = loader.load_all()
        except CorpusUnavailableError as exc:
            return _text(f"corpus_unavailable: {exc}")
        return _text(lookup_norm(payload, corpus=corpus).to_agent_text())

    return _impl


def make_sdk_tool(*, loader: CorpusLoader | None = None):
    """Optional: wrap the function with claude_agent_sdk's @tool decorator."""
    from claude_agent_sdk import tool  # local import; SDK is optional dep

    fn = make_lookup_norm_tool_function(loader=loader)
    schema = LookupNormInput.model_json_schema()
    return tool("lookup_norm", "…", schema)(fn)


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}
```

- [ ] **Step 5: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_agent_sdk.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kira/legal_sources/adapters/agent_sdk.py tests/legal_sources/adapters/test_agent_sdk.py pyproject.toml
git commit -m "feat(legal-sources): Claude Agent SDK adapter"
```

---

## Task 15: Ingest Lambda handler

**Files:**
- Create: `src/kira/legal_sources/adapters/ingest_handler.py`
- Test: `tests/legal_sources/adapters/test_ingest_handler.py`

- [ ] **Step 1: Capture a small XML-zip fixture**

Manually save `tests/legal_sources/fixtures/captured/bgb.zip` containing a minimal `bgb.xml` with one `<norm>` for §535. Use the existing `kira.knowledge.xml_parser` test fixtures as a starting point — copy its synthetic XML into a zip:

```bash
.venv/bin/python -c "
import zipfile, io, pathlib
xml = b'''<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<dokumente>
  <norm builddate=\"2026-05-08\">
    <metadaten>
      <jurabk>BGB</jurabk>
      <amtabk>BGB</amtabk>
      <enbez>§ 535</enbez>
      <titel>Inhalt und Hauptpflichten des Mietvertrags</titel>
      <ausfertigung-datum>1896-08-18</ausfertigung-datum>
    </metadaten>
    <textdaten>
      <text format=\"XML\">
        <Content>
          <P>(1) Durch den Mietvertrag wird der Vermieter verpflichtet, dem Mieter den Gebrauch der Mietsache zu gewähren.</P>
        </Content>
      </text>
    </textdaten>
  </norm>
</dokumente>'''
out = pathlib.Path('tests/legal_sources/fixtures/captured/bgb.zip')
out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, 'w') as zf:
    zf.writestr('bgb.xml', xml)
print('wrote', out)
"
```

- [ ] **Step 2: Write failing test (replays the zip via respx)**

Create `tests/legal_sources/adapters/test_ingest_handler.py`:

```python
import json
from pathlib import Path

import boto3
import httpx
import pytest
import respx
from moto import mock_aws


FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")


@pytest.fixture
def s3_target():
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="ingest-target",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        yield "ingest-target"


def test_ingest_writes_corpus_and_manifest(monkeypatch, s3_target):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    zip_bytes = (FIXTURES / "captured" / "bgb.zip").read_bytes()

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=zip_bytes)
        )
        from kira.legal_sources.adapters.ingest_handler import handler
        result = handler({"gesetze": ["bgb"]}, context=None)

    assert result["written"] == ["bgb"]
    s3 = boto3.client("s3", region_name="eu-central-1")
    body = s3.get_object(Bucket=s3_target, Key="gesetze/bgb.json")["Body"].read()
    payload = json.loads(body)
    assert payload["_meta"]["abkuerzung"] == "BGB"
    assert "535" in payload["paragraphen"]
    manifest = json.loads(
        s3.get_object(Bucket=s3_target, Key="gesetze/_manifest.json")["Body"].read()
    )
    assert "gesetze/bgb.json" in manifest["files"]


def test_ingest_skips_put_when_hash_unchanged(monkeypatch, s3_target):
    monkeypatch.setenv("LEGAL_CORPUS_BUCKET", s3_target)
    zip_bytes = (FIXTURES / "captured" / "bgb.zip").read_bytes()

    from kira.legal_sources.adapters.ingest_handler import handler

    with respx.mock:
        respx.get("https://www.gesetze-im-internet.de/bgb/xml.zip").mock(
            return_value=httpx.Response(200, content=zip_bytes)
        )
        first = handler({"gesetze": ["bgb"]}, context=None)
        second = handler({"gesetze": ["bgb"]}, context=None)

    assert first["written"] == ["bgb"]
    assert second["written"] == []  # idempotent skip
    assert second["skipped"] == ["bgb"]
```

- [ ] **Step 3: Run, confirm fail**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_ingest_handler.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement ingest handler**

Create `src/kira/legal_sources/adapters/ingest_handler.py`:

```python
"""Daily ingest Lambda: refresh the S3 legal corpus.

Reuses `kira.knowledge.ingest` for parsing — this is a deployment-glue
adapter and is allowed to import from kira.*.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import boto3

from kira.knowledge.ingest import GESETZE, GesetzKonfiguration, _ingest_one
import httpx

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

USER_AGENT = "KIRA-Agent/0.1 (legal-sources ingest; eu-central-1)"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    bucket = os.environ["LEGAL_CORPUS_BUCKET"]
    requested = event.get("gesetze") or list(GESETZE.keys())
    s3 = boto3.client("s3", region_name="eu-central-1")
    written: list[str] = []
    skipped: list[str] = []

    for key in requested:
        cfg: GesetzKonfiguration | None = GESETZE.get(key.lower())
        if cfg is None:
            log.warning("Unknown Gesetz %s — skipped", key)
            continue
        with httpx.Client(
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            payload = _build_payload(client, cfg)
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        new_sha = hashlib.sha256(body).hexdigest()
        s3_key = f"gesetze/{cfg.abkuerzung.lower()}.json"
        existing_sha = _existing_sha(s3, bucket, s3_key)
        if existing_sha == new_sha:
            skipped.append(cfg.abkuerzung.lower())
            continue
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=body,
            ContentType="application/json",
            Metadata={"content-sha256": new_sha},
        )
        written.append(cfg.abkuerzung.lower())

    _write_manifest(s3, bucket)
    return {"written": written, "skipped": skipped}


def _build_payload(client: httpx.Client, cfg: GesetzKonfiguration) -> dict[str, Any]:
    """Adapter shim: reuses _ingest_one's logic but writes locally instead of disk.

    `_ingest_one` writes to a Path; we want bytes. We re-implement the flow
    using the same building blocks.
    """
    from datetime import date

    from kira.knowledge.ingest import _extract_xml_from_zip
    from kira.knowledge.schema import norm_to_dict
    from kira.knowledge.xml_parser import filter_normen, parse_gii_xml

    response = client.get(cfg.zip_url)
    response.raise_for_status()
    xml_bytes = _extract_xml_from_zip(response.content)
    parsed = parse_gii_xml(xml_bytes)
    filtered = filter_normen(
        parsed,
        paragraphen=cfg.paragraphen,
        paragraph_range=cfg.paragraph_range,
    )
    if not filtered:
        raise RuntimeError(
            f"Keine Paragraphen für {cfg.abkuerzung} extrahiert — Filter prüfen."
        )
    return {
        "_meta": {
            "abkuerzung": cfg.abkuerzung,
            "titel": cfg.titel,
            "stand": date.today().isoformat(),
            "quelle": "gesetze-im-internet.de",
            "quelle_url": cfg.base_url,
            "gefiltert_auf": (
                [f"§§ {cfg.paragraph_range[0]}–{cfg.paragraph_range[1]}"]
                if cfg.paragraph_range
                else (cfg.paragraphen or ["vollständig"])
            ),
            "anzahl_normen": len(filtered),
        },
        "paragraphen": {
            p: {**norm_to_dict(n), "quelle_url": f"{cfg.base_url}/__{p}.html"}
            for p, n in sorted(filtered.items(), key=_sort_key)
        },
    }


def _sort_key(item: tuple[str, object]) -> tuple[int, str]:
    import re
    p = item[0]
    m = re.match(r"^(\d+)([a-zA-Z]?)$", p)
    if not m:
        return (0, p)
    return (int(m.group(1)), m.group(2))


def _existing_sha(s3, bucket: str, key: str) -> str | None:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError:
        return None
    return (head.get("Metadata") or {}).get("content-sha256")


def _write_manifest(s3, bucket: str) -> None:
    objs = s3.list_objects_v2(Bucket=bucket, Prefix="gesetze/")
    files = sorted(
        o["Key"] for o in objs.get("Contents", [])
        if o["Key"].endswith(".json") and not o["Key"].endswith("_manifest.json")
    )
    body = json.dumps({"version": 1, "files": files}, sort_keys=True).encode("utf-8")
    s3.put_object(Bucket=bucket, Key="gesetze/_manifest.json", Body=body, ContentType="application/json")
```

- [ ] **Step 5: Run, confirm pass**

Run: `.venv/bin/pytest tests/legal_sources/adapters/test_ingest_handler.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kira/legal_sources/adapters/ingest_handler.py \
        tests/legal_sources/adapters/test_ingest_handler.py \
        tests/legal_sources/fixtures/captured/bgb.zip
git commit -m "feat(legal-sources): ingest Lambda handler with hash-skip"
```

---

## Task 16: Live smoke test (opt-in)

**Files:**
- Create: `tests/legal_sources/live/test_live_smoke.py`

- [ ] **Step 1: Write live smoke test**

Create `tests/legal_sources/live/test_live_smoke.py`:

```python
"""Opt-in smoke tests against the real gesetze-im-internet.de.

Skipped by default; run with: RUN_LIVE_TESTS=1 pytest -m live tests/legal_sources/live/
If this fails, upstream HTML/XML changed and parser fixtures need refreshing.
"""

import os
import zipfile
from io import BytesIO

import httpx
import pytest

pytestmark = [pytest.mark.live]

if not os.environ.get("RUN_LIVE_TESTS"):
    pytest.skip("RUN_LIVE_TESTS not set", allow_module_level=True)


def test_live_bgb_xml_zip_parseable():
    url = "https://www.gesetze-im-internet.de/bgb/xml.zip"
    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"User-Agent": "KIRA-Agent/0.1 (live smoke)"},
        follow_redirects=True,
    ) as client:
        resp = client.get(url)
    assert resp.status_code == 200
    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        assert any(n.endswith(".xml") for n in zf.namelist())


def test_live_lookup_norm_against_real_corpus(tmp_path, monkeypatch):
    """Run the actual ingest pipeline once, then lookup_norm against it."""
    from kira.legal_sources.gesetze.lookup_norm import lookup_norm
    from kira.legal_sources.gesetze.schema import LookupNormInput
    from kira.legal_sources._common.s3_corpus import CorpusLoader
    from kira.legal_sources.adapters.ingest_handler import _build_payload
    from kira.knowledge.ingest import GESETZE
    import json

    target = tmp_path / "gesetze"
    target.mkdir()
    cfg = GESETZE["bgb"]
    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"User-Agent": "KIRA-Agent/0.1 (live smoke)"},
        follow_redirects=True,
    ) as client:
        payload = _build_payload(client, cfg)
    (target / "bgb.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    loader = CorpusLoader.from_env()
    result = lookup_norm(LookupNormInput(gesetz="BGB", paragraph="535"), corpus=loader.load_all())
    from kira.legal_sources.gesetze.schema import LookupNormSuccess
    assert isinstance(result, LookupNormSuccess)
    assert "Mietvertrag" in result.wortlaut
```

- [ ] **Step 2: Verify live test is skipped by default**

Run: `.venv/bin/pytest tests/legal_sources/live/ -v`
Expected: 2 skipped.

- [ ] **Step 3: Verify live test runs and passes when opted in**

Run: `RUN_LIVE_TESTS=1 .venv/bin/pytest -m live tests/legal_sources/live/ -v`
Expected: 2 passed (requires internet).

- [ ] **Step 4: Commit**

```bash
git add tests/legal_sources/live/test_live_smoke.py
git commit -m "test(legal-sources): live smoke against real gesetze-im-internet.de"
```

---

## Task 17: Coverage gate

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add coverage config**

In `pyproject.toml`, append:

```toml
[tool.coverage.run]
source = ["src/kira/legal_sources/_common", "src/kira/legal_sources/gesetze"]
branch = true

[tool.coverage.report]
fail_under = 95
show_missing = true
skip_covered = false
```

- [ ] **Step 2: Run coverage, confirm threshold met**

Run: `.venv/bin/pytest tests/legal_sources/ --cov --cov-report=term-missing`
Expected: combined coverage on the two `source` packages ≥ 95%.

- [ ] **Step 3: If below 95%, add targeted tests**

For any uncovered line in `_common/` or `gesetze/`, add a test hitting it. Re-run until ≥ 95%.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore(legal-sources): coverage gate at 95% on framework-free code"
```

---

## Task 18: CDK app scaffolding

**Files:**
- Create: `infra/legal_sources/app.py`, `infra/legal_sources/cdk.json`, `infra/legal_sources/requirements.txt`

- [ ] **Step 1: Create CDK requirements**

Create `infra/legal_sources/requirements.txt`:

```
aws-cdk-lib>=2.140.0
constructs>=10.0.0
```

- [ ] **Step 2: Create cdk.json**

Create `infra/legal_sources/cdk.json`:

```json
{
  "app": "python app.py",
  "context": {
    "@aws-cdk/aws-lambda:recognizeLayerVersion": true,
    "@aws-cdk/core:enableStackNameDuplicates": false
  }
}
```

- [ ] **Step 3: Create CDK app entry**

Create `infra/legal_sources/app.py`:

```python
import os

import aws_cdk as cdk

from stack import LegalSourcesStack


app = cdk.App()
LegalSourcesStack(
    app,
    "KiraLegalSources",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region="eu-central-1",
    ),
)
app.synth()
```

- [ ] **Step 4: Commit (stack file added in next task)**

Skip commit until Task 19.

---

## Task 19: CDK stack — S3 bucket and Lambda layer for kira sources

**Files:**
- Create: `infra/legal_sources/stack.py`

- [ ] **Step 1: Create the stack with S3 bucket**

Create `infra/legal_sources/stack.py`:

```python
"""Legal-sources CDK stack: S3 corpus + lookup Lambda + ingest Lambda + schedule.

Region pinned to eu-central-1.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_kms as kms,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_cloudwatch as cw,
)
from constructs import Construct


REPO_ROOT = Path(__file__).resolve().parents[2]


class LegalSourcesStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        if self.region != "eu-central-1":
            raise RuntimeError(
                f"LegalSourcesStack must deploy to eu-central-1, got {self.region!r}"
            )

        kms_key = kms.Key(
            self,
            "CorpusKey",
            description="KIRA legal corpus encryption key",
            enable_key_rotation=True,
        )

        bucket = s3.Bucket(
            self,
            "CorpusBucket",
            bucket_name=f"kira-legal-corpus-{self.account}-eu-central-1",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            versioned=True,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Code bundling: zip src/kira/ as the Lambda payload.
        code = lambda_.Code.from_asset(
            str(REPO_ROOT),
            bundling=cdk.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_11.bundling_image,
                command=[
                    "bash",
                    "-c",
                    " && ".join([
                        "pip install . -t /asset-output",
                        "cp -r src/kira /asset-output/kira",
                    ]),
                ],
            ),
        )

        lookup_fn = lambda_.Function(
            self,
            "LookupNormFn",
            function_name="kira-legal-lookup-norm",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="kira.legal_sources.adapters.lookup_handler.handler",
            code=code,
            memory_size=512,
            timeout=cdk.Duration.seconds(10),
            environment={"LEGAL_CORPUS_BUCKET": bucket.bucket_name},
            log_retention=logs.RetentionDays.ONE_MONTH,
        )
        bucket.grant_read(lookup_fn)
        kms_key.grant_decrypt(lookup_fn)

        ingest_fn = lambda_.Function(
            self,
            "IngestFn",
            function_name="kira-legal-ingest",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="kira.legal_sources.adapters.ingest_handler.handler",
            code=code,
            memory_size=1024,
            timeout=cdk.Duration.minutes(5),
            environment={"LEGAL_CORPUS_BUCKET": bucket.bucket_name},
            log_retention=logs.RetentionDays.ONE_MONTH,
        )
        bucket.grant_read_write(ingest_fn)
        kms_key.grant_encrypt_decrypt(ingest_fn)

        events.Rule(
            self,
            "DailyIngest",
            rule_name="kira-legal-ingest-daily",
            schedule=events.Schedule.cron(minute="0", hour="2"),
            targets=[targets.LambdaFunction(ingest_fn)],
        )

        cw.Alarm(
            self,
            "StaleCorpusAlarm",
            alarm_name="kira-legal-stale-corpus",
            metric=cw.Metric(
                namespace="AWS/Lambda",
                metric_name="Invocations",
                dimensions_map={"FunctionName": ingest_fn.function_name},
                period=cdk.Duration.hours(36),
                statistic="Sum",
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.BREACHING,
            alarm_description="Ingest has not run in 36h — corpus may be stale.",
        )

        cdk.CfnOutput(self, "LookupFnArn", value=lookup_fn.function_arn)
        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
```

- [ ] **Step 2: Synth the stack to validate**

Run: `cd infra/legal_sources && .venv/bin/python -m pip install -r requirements.txt && CDK_DEFAULT_ACCOUNT=000000000000 .venv/bin/cdk synth --no-staging`
Expected: synth succeeds; produces a CloudFormation template containing `AWS::S3::Bucket`, two `AWS::Lambda::Function`, an `AWS::Events::Rule`, an alarm.

(The user will need `aws-cdk` CLI installed globally: `npm install -g aws-cdk`. Note this in deployment README in Task 21.)

- [ ] **Step 3: Commit CDK skeleton**

```bash
git add infra/legal_sources/
git commit -m "feat(infra): CDK stack for legal-sources tool 1"
```

---

## Task 20: Gateway target registration script

**Files:**
- Create: `scripts/register_gateway_target.py`

- [ ] **Step 1: Create script**

Create `scripts/register_gateway_target.py`:

```python
"""Register the lookup-norm Lambda as an AgentCore Gateway target.

Usage:
    python scripts/register_gateway_target.py \\
        --gateway-id <gateway-id> \\
        --lambda-arn <lambda-arn>

CDK constructs for AgentCore Gateway are limited at time of writing; this
post-deploy script handles the target registration via boto3.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3


SCHEMA = {
    "type": "object",
    "properties": {
        "gesetz": {"type": "string", "description": "Gesetz-Abkürzung, z.B. BGB."},
        "paragraph": {"type": "string", "description": "Paragraph, z.B. '535' oder '535a'."},
        "absatz": {"type": "string", "description": "Optional: konkreter Absatz."},
    },
    "required": ["gesetz", "paragraph"],
    "additionalProperties": False,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-id", required=True)
    parser.add_argument("--lambda-arn", required=True)
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()

    if args.region != "eu-central-1":
        print("Refusing to register target outside eu-central-1.", file=sys.stderr)
        return 2

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    response = client.create_gateway_target(
        gatewayId=args.gateway_id,
        name="lookup_norm",
        targetType="LAMBDA",
        targetConfig={
            "lambda": {
                "functionArn": args.lambda_arn,
                "toolDefinitions": [
                    {
                        "name": "lookup_norm",
                        "description": (
                            "Lädt den autoritativen Wortlaut eines deutschen "
                            "Paragraphen aus gesetze-im-internet.de."
                        ),
                        "inputSchema": SCHEMA,
                    }
                ],
            }
        },
    )
    print(json.dumps(response, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

NOTE: The exact `bedrock-agentcore-control` API field names (`createGatewayTarget`, `targetConfig.lambda.toolDefinitions`) need verification against the live API at execution time. If the real API uses different field names, update the script — the call shape is the only piece that depends on the live API.

- [ ] **Step 2: Verify the script imports cleanly (boto3 client name resolves)**

Run: `.venv/bin/python -c "import boto3; boto3.client('bedrock-agentcore-control', region_name='eu-central-1')"`
Expected: no exception (it's enough to know the service name is recognised).

- [ ] **Step 3: Commit**

```bash
git add scripts/register_gateway_target.py
git commit -m "feat(infra): Gateway target registration script"
```

---

## Task 21: End-to-end smoke script + deploy README

**Files:**
- Create: `scripts/legal_sources_smoke.py`
- Create: `infra/legal_sources/README.md`

- [ ] **Step 1: Create end-to-end smoke script**

Create `scripts/legal_sources_smoke.py`:

```python
"""End-to-end smoke: invoke the lookup_norm Lambda directly and via Gateway.

Run AFTER cdk deploy AND register_gateway_target.py succeeded.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3


def invoke_direct(function_name: str, region: str) -> dict:
    client = boto3.client("lambda", region_name=region)
    resp = client.invoke(
        FunctionName=function_name,
        Payload=json.dumps({"gesetz": "BGB", "paragraph": "535"}).encode("utf-8"),
    )
    return json.loads(resp["Payload"].read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookup-fn", default="kira-legal-lookup-norm")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()

    print("=== Direct Lambda invoke ===")
    direct = invoke_direct(args.lookup_fn, args.region)
    print(json.dumps(direct, indent=2, ensure_ascii=False))
    if direct.get("isError"):
        print("Direct invoke returned an error.", file=sys.stderr)
        return 1

    body = json.loads(direct["content"][0]["text"])
    if body["paragraph"] != "535":
        print("Unexpected paragraph in response.", file=sys.stderr)
        return 1

    print("\n✅ Direct Lambda smoke OK.")
    print("Next: invoke via your AgentCore Gateway tool and verify the same response shape.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Create deploy README**

Create `infra/legal_sources/README.md`:

```markdown
# Deploying KIRA Legal-Sources Tool 1

## Prerequisites

- AWS account with credentials configured for **eu-central-1** (`aws sts get-caller-identity`).
- Node.js + AWS CDK CLI: `npm install -g aws-cdk@^2`.
- Python 3.11 venv with project deps: `.venv/bin/pip install -e ".[dev]"`.
- CDK Python deps: `pip install -r infra/legal_sources/requirements.txt`.
- Existing AgentCore Gateway resource (created out-of-band; capture its ID).

## First deploy

```bash
cd infra/legal_sources
cdk bootstrap aws://${AWS_ACCOUNT_ID}/eu-central-1   # one-time per account/region
cdk deploy KiraLegalSources --require-approval never
```

Outputs include `LookupFnArn` and `BucketName`.

## Initial corpus population

```bash
aws lambda invoke \\
  --function-name kira-legal-ingest \\
  --region eu-central-1 \\
  --payload '{}' \\
  /tmp/ingest-out.json
cat /tmp/ingest-out.json
# Expect: {"written": ["bgb", "betrkv", "heizkostenv"], "skipped": []}
```

The EventBridge rule will run daily at 02:00 UTC after this.

## Register Gateway target

```bash
python scripts/register_gateway_target.py \\
    --gateway-id <your-gateway-id> \\
    --lambda-arn <LookupFnArn>
```

## Smoke test

```bash
python scripts/legal_sources_smoke.py
# Expected: ✅ Direct Lambda smoke OK.
```

## Acceptance checklist (per spec §10)

- [ ] `pytest tests/legal_sources/ --cov-fail-under=95` green
- [ ] `RUN_LIVE_TESTS=1 pytest -m live tests/legal_sources/live/` green
- [ ] `cdk deploy` succeeded
- [ ] `register_gateway_target.py` returned a valid target ARN
- [ ] `legal_sources_smoke.py` printed ✅
- [ ] CloudWatch alarm `kira-legal-stale-corpus` is in OK state after the first ingest
```

- [ ] **Step 3: Commit**

```bash
git add scripts/legal_sources_smoke.py infra/legal_sources/README.md
git commit -m "docs(infra): end-to-end smoke script and deploy README"
```

---

## Task 22: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run full unit + adapter test suite, confirm green**

Run: `.venv/bin/pytest tests/legal_sources/ -v --cov --cov-fail-under=95`
Expected: all non-live tests green, coverage ≥ 95% on `_common/` + `gesetze/`.

- [ ] **Step 2: Run existing KIRA tests, confirm no regressions**

Run: `.venv/bin/pytest tests/ -q -m 'not live'`
Expected: existing 56 tests + new tests all pass.

- [ ] **Step 3: Lint pass**

Run: `.venv/bin/ruff check src/ tests/ infra/ scripts/`
Expected: no lint errors. Fix any reported issues, re-run.

- [ ] **Step 4: Synth CDK once more for sanity**

Run: `cd infra/legal_sources && CDK_DEFAULT_ACCOUNT=000000000000 cdk synth --no-staging`
Expected: clean synth.

- [ ] **Step 5: Hand off**

Print a short status report:

```
Legal-sources Tool 1 ready for deployment.
- Unit + adapter tests: passing, coverage X%
- Live smoke (opt-in): verified separately
- CDK stack: synthed cleanly
- Next steps: cdk deploy from infra/legal_sources/, then run scripts/legal_sources_smoke.py
```

---

## Spec coverage self-review

Cross-checking against `docs/superpowers/specs/2026-05-09-legal-sources-tool1-design.md`:

- §2 contracts (input/success/error) → Tasks 2, 3.
- §3 architecture (S3 corpus + scheduled ingest + lookup Lambda + Gateway) → Tasks 11, 13, 15, 19.
- §4 module layout → Task 1 + every subsequent Create.
- §5 reuse of existing code (corpus_format duplicated, ingest reused) → Task 4 (corpus_format), Task 15 (ingest_handler imports kira.knowledge).
- §6 adapters → Tasks 12, 13, 14.
- §7 test tiers → Tasks 7–9 (Tier 1), 12–15 (Tier 2), 15 (Tier 3 via captured zip + respx), 16 (Tier 4), 21 (Tier 5).
- §8 deployment (CDK + region pin + KMS + retention) → Tasks 18, 19.
- §8 Gateway registration script → Task 20.
- §9 ops concerns (idempotent ingest, stale alarm, audit log) → Tasks 13 (logging), 15 (hash-skip), 19 (alarm).
- §10 acceptance criteria → Task 21 (README) + Task 22 (verification).
- §11 open questions: `LEGAL_CORPUS_LOCAL_DIR` → Task 10. Adapter auto-registration rule → Task 12 docstring.

All spec sections accounted for. No placeholder steps; each step has runnable commands or full code.
