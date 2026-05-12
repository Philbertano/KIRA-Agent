# Wire legal-sources Lambdas into KIRA's agent loop — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace V0's bundled-JSON-backed `norm_lookup`/`norm_search`/`norm_list` tools with calls to the deployed AWS Lambdas (`kira-legal-lookup-norm`, `kira-legal-search`). Delete the bundled corpus and `kira ingest` CLI in the same PR (hard cutover).

**Architecture:** Thin boto3 client (`src/kira/agent/legal_client.py`) encapsulates all "talk to AWS" concerns (region, function names, retry, timeout, MCP-envelope unwrapping, structured logging). Agent tools become trivial wrappers over that client. The `src/kira/knowledge/` directory is deleted; the gii-norm XML parser moves into `src/kira/legal_sources/_common/` so the Lambda code no longer depends on `kira.knowledge`.

**Tech Stack:** Python 3.11, boto3 (already in deps), pytest, AWS Lambda invoke (eu-central-1), the existing Bedrock credential chain.

**Spec:** `docs/superpowers/specs/2026-05-12-wire-legal-sources-into-agent-loop-design.md`

**Branch:** `feat/wire-legal-sources-into-agent-loop` (already created off `main`).

**Coverage gate:** 95% on new agent code, matching the existing `tests/legal_sources/` bar.

---

## File map

| File | Action |
|---|---|
| `src/kira/legal_sources/_common/xml_parser.py` | **Create** (moved from `kira/knowledge/xml_parser.py`, inlines `Norm` + `_normalize_paragraph`; drops `filter_normen`, `to_display`) |
| `src/kira/legal_sources/_common/zip_extract.py` | **Create** (moved from `kira/knowledge/ingest._extract_xml_from_zip`, renamed `extract_xml_from_zip` and public) |
| `tests/legal_sources/unit/test_xml_parser.py` | **Create** (moved from `tests/test_xml_parser.py`, updated imports, drops `filter_normen` tests) |
| `src/kira/agent/legal_client.py` | **Create** (`LegalSourcesClient` + `LegalSourceUnavailable`) |
| `tests/agent/__init__.py` | **Create** (empty, marks package) |
| `tests/agent/test_legal_client.py` | **Create** |
| `tests/agent/tools/__init__.py` | **Create** (empty) |
| `tests/agent/tools/test_norm_lookup.py` | **Create** |
| `tests/agent/tools/test_norm_search.py` | **Create** |
| `tests/agent/test_system_prompts.py` | **Create** |
| `tests/agent/test_legal_client_live.py` | **Create** (opt-in via `RUN_LIVE_TESTS=1`) |
| `tests/agent/test_end_to_end.py` | **Create** (opt-in via `RUN_LIVE_TESTS=1`) |
| `tests/test_cli.py` | **Create** (asserts `kira ingest` removed) |
| `src/kira/legal_sources/adapters/ingest_handler.py` | **Modify** (re-import xml_parser + zip_extract from new locations) |
| `scripts/backfill_corpus.py` | **Modify** (same imports) |
| `src/kira/agent/tools/norm_lookup.py` | **Replace contents** (thin wrapper over `LegalSourcesClient`) |
| `src/kira/agent/tools/norm_search.py` | **Replace contents** (thin wrapper, gains `gesetz_filter` + `type_filter`) |
| `src/kira/agent/tools/__init__.py` | **Modify** (drop `norm_list` import) |
| `src/kira/agent/system_prompts.py` | **Modify** (`JUNIOR_ASSOCIATE_DE` rewrite per spec section 3) |
| `src/kira/cli.py` | **Modify** (remove `ingest` subcommand, remove `kira.knowledge.ingest` import) |
| `CLAUDE.md` | **Modify** (update "Common commands", "Knowledge / law corpus", "Tools" sections) |
| `src/kira/agent/tools/norm_list.py` | **Delete** |
| `src/kira/knowledge/` (entire directory) | **Delete** |
| `tests/test_xml_parser.py` | **Delete** (moved) |
| `tests/test_tools.py` | **Delete** (superseded by new agent tests) |

---

## Phase 1 — Move shared XML parser into `legal_sources/_common/`

After this phase, the Lambda code no longer imports from `kira.knowledge`. The old `kira/knowledge/xml_parser.py` continues to exist (used by V0 `kira/knowledge/ingest.py` and the V0 `tests/test_xml_parser.py`); both are deleted in Phase 6.

### Task 1: Create new `xml_parser.py` in `legal_sources/_common/`

**Files:**
- Create: `src/kira/legal_sources/_common/xml_parser.py`
- Test (move): `tests/legal_sources/unit/test_xml_parser.py`

- [ ] **Step 1: Create the new parser file**

Write `src/kira/legal_sources/_common/xml_parser.py` exactly as below. It merges the `Norm` dataclass and the `_normalize_paragraph` helper inline so it has zero `kira.*` imports. It drops `filter_normen`, `to_display`, `volltext`, `zitation`, and the unused `_normalize_paragraph_helper_for_export` (those were V0-only).

```python
"""Parser for the gesetze-im-internet.de gii-norm XML format.

Self-contained: no kira.* imports. Used by the legal-sources ingest
Lambda and (transitively) by the backfill script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

_PARAGRAPH_RE = re.compile(r"§\s*(\d+[a-zA-Z]?)")


@dataclass(frozen=True)
class Norm:
    """A single paragraph extracted from a gii-norm XML document."""

    gesetz: str
    paragraph: str
    titel: str
    absaetze: list[str] = field(default_factory=list)
    abschnitt: str | None = None
    fundstelle: str | None = None
    quelle_url: str | None = None


@dataclass
class ParseResult:
    abkuerzung: str
    titel: str
    normen: dict[str, Norm]


def parse_gii_xml(xml_bytes: bytes) -> ParseResult:
    """Parse a gii-norm XML document into a ParseResult."""
    root = ET.fromstring(xml_bytes)
    abkuerzung: str | None = None
    titel: str | None = None
    normen: dict[str, Norm] = {}
    aktueller_abschnitt: str | None = None

    for norm_el in root.iter("norm"):
        meta = norm_el.find("metadaten")
        if meta is None:
            continue

        if abkuerzung is None:
            jurabk = meta.findtext("jurabk")
            if jurabk:
                abkuerzung = jurabk.strip()

        gliederung = meta.find("gliederungseinheit")
        if gliederung is not None:
            gtitel = gliederung.findtext("gliederungstitel")
            if gtitel:
                aktueller_abschnitt = _clean_text(gtitel)

        enbez = meta.findtext("enbez")
        if not enbez:
            if titel is None:
                kurzue = meta.findtext("kurzue") or meta.findtext("langue")
                if kurzue:
                    titel = _clean_text(kurzue)
            continue

        paragraph = _extract_paragraph_number(enbez)
        if not paragraph:
            continue

        norm_titel = _clean_text(meta.findtext("titel") or "")
        absaetze = _extract_absaetze(norm_el)
        fundstelle = _extract_fundstelle(meta)

        normen[paragraph] = Norm(
            gesetz=abkuerzung or "?",
            paragraph=paragraph,
            titel=norm_titel,
            absaetze=absaetze,
            abschnitt=aktueller_abschnitt,
            fundstelle=fundstelle,
            quelle_url=None,
        )

    return ParseResult(
        abkuerzung=abkuerzung or "?",
        titel=titel or abkuerzung or "?",
        normen=normen,
    )


def normalize_paragraph(query: str) -> str:
    """'§ 535', '§535', '535', '536a', '§ 536 BGB' → '535' / '536a' / '536'."""
    cleaned = re.sub(
        r"\b(BGB|BetrKV|HeizkostenV|EGBGB|ZPO|StGB|GG|HGB|StPO|VwGO|SGB)\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[§\s]", "", cleaned)
    match = re.match(r"^(\d+)([a-z]?)$", cleaned, flags=re.IGNORECASE)
    if match:
        return match.group(1) + match.group(2).lower()
    digits = re.match(r"^(\d+)", cleaned)
    return digits.group(1) if digits else cleaned


def _extract_paragraph_number(enbez: str) -> str | None:
    match = _PARAGRAPH_RE.search(enbez)
    if match:
        return match.group(1)
    art_match = re.search(r"Art\.?\s*(\d+[a-zA-Z]?)", enbez)
    if art_match:
        return art_match.group(1)
    digits = re.search(r"(\d+[a-zA-Z]?)", enbez)
    return digits.group(1) if digits else None


def _extract_absaetze(norm_el: ET.Element) -> list[str]:
    absaetze: list[str] = []
    for content_el in norm_el.iter("Content"):
        for p_el in content_el.iter("P"):
            text = _flatten_text(p_el)
            if text:
                absaetze.append(text)
        if absaetze:
            return absaetze
    textdaten = norm_el.find("textdaten")
    if textdaten is not None:
        text = _flatten_text(textdaten).strip()
        if text:
            return [text]
    return []


def _extract_fundstelle(meta: ET.Element) -> str | None:
    fst = meta.find("fundstelle")
    if fst is None:
        return None
    periodikum = fst.findtext("periodikum") or ""
    zitstelle = fst.findtext("zitstelle") or ""
    out = " ".join(part for part in [periodikum, zitstelle] if part).strip()
    return out or None


def _flatten_text(el: ET.Element) -> str:
    parts: list[str] = []

    def _walk(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = child.tag.lower()
            if tag in {"br"}:
                parts.append("\n")
            else:
                _walk(child)
            if child.tail:
                parts.append(child.tail)

    _walk(el)
    return _clean_text("".join(parts))


def _clean_text(text: str) -> str:
    return re.sub(r"[ \t\r]+", " ", text).strip()
```

- [ ] **Step 2: Move the test file**

Create `tests/legal_sources/unit/test_xml_parser.py` by copying `tests/test_xml_parser.py` and editing two things:

1. Change the import line `from kira.knowledge.xml_parser import filter_normen, parse_gii_xml` to:

```python
from kira.legal_sources._common.xml_parser import parse_gii_xml
```

2. Remove any tests that exercise `filter_normen` (it no longer exists). If you're unsure which ones, run the file's tests after the edit; the ones referencing `filter_normen` will be `NameError`.

Leave `tests/test_xml_parser.py` in place for now — it still tests the old module, which still exists. It will be deleted in Phase 6.

- [ ] **Step 3: Run the moved tests**

Run: `.venv/bin/python -m pytest tests/legal_sources/unit/test_xml_parser.py -v`
Expected: all pass (the parser logic is identical, only the import path changed).

- [ ] **Step 4: Run the original tests too**

Run: `.venv/bin/python -m pytest tests/test_xml_parser.py -v`
Expected: all pass (the old module is untouched).

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/xml_parser.py tests/legal_sources/unit/test_xml_parser.py
git commit -m "refactor(legal-sources): copy xml_parser into _common/ (no kira.* imports)"
```

### Task 2: Create `zip_extract.py` in `legal_sources/_common/`

**Files:**
- Create: `src/kira/legal_sources/_common/zip_extract.py`
- Test: `tests/legal_sources/unit/test_zip_extract.py`

- [ ] **Step 1: Write the failing test**

Create `tests/legal_sources/unit/test_zip_extract.py`:

```python
"""Tests for the gii-zip helper."""

from __future__ import annotations

import io
import zipfile

import pytest

from kira.legal_sources._common.zip_extract import extract_xml_from_zip


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_extract_xml_returns_xml_bytes() -> None:
    zip_bytes = _make_zip({"bgb.xml": b"<dokumente/>"})
    assert extract_xml_from_zip(zip_bytes) == b"<dokumente/>"


def test_extract_xml_picks_first_xml_when_multiple() -> None:
    zip_bytes = _make_zip({"a.txt": b"hi", "b.xml": b"<a/>", "c.xml": b"<b/>"})
    assert extract_xml_from_zip(zip_bytes) == b"<a/>"


def test_extract_xml_raises_when_no_xml() -> None:
    zip_bytes = _make_zip({"readme.txt": b"hi"})
    with pytest.raises(RuntimeError, match="Keine XML-Datei"):
        extract_xml_from_zip(zip_bytes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/legal_sources/unit/test_zip_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kira.legal_sources._common.zip_extract'`

- [ ] **Step 3: Write the module**

Create `src/kira/legal_sources/_common/zip_extract.py`:

```python
"""Extract the gii-norm XML payload from a gesetze-im-internet.de xml.zip."""

from __future__ import annotations

import io
import zipfile


def extract_xml_from_zip(zip_bytes: bytes) -> bytes:
    """Return the bytes of the first .xml file inside ``zip_bytes``.

    Raises RuntimeError if the zip contains no .xml file.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_names:
            raise RuntimeError("Keine XML-Datei im ZIP gefunden.")
        with zf.open(xml_names[0]) as f:
            return f.read()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/legal_sources/unit/test_zip_extract.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/_common/zip_extract.py tests/legal_sources/unit/test_zip_extract.py
git commit -m "refactor(legal-sources): add zip_extract helper in _common/"
```

### Task 3: Switch legal-sources consumers to the new imports

**Files:**
- Modify: `src/kira/legal_sources/adapters/ingest_handler.py`
- Modify: `scripts/backfill_corpus.py`

- [ ] **Step 1: Update `ingest_handler.py` imports**

In `src/kira/legal_sources/adapters/ingest_handler.py`, replace:

```python
from kira.knowledge.ingest import _extract_xml_from_zip
from kira.knowledge.xml_parser import parse_gii_xml
```

with:

```python
from kira.legal_sources._common.xml_parser import parse_gii_xml
from kira.legal_sources._common.zip_extract import extract_xml_from_zip
```

Then change the single call site (currently `xml_bytes = _extract_xml_from_zip(get_resp.content)`) to drop the leading underscore:

```python
xml_bytes = extract_xml_from_zip(get_resp.content)
```

- [ ] **Step 2: Update `backfill_corpus.py` imports**

In `scripts/backfill_corpus.py`, replace:

```python
from kira.knowledge.ingest import _extract_xml_from_zip
from kira.knowledge.xml_parser import parse_gii_xml
```

with:

```python
from kira.legal_sources._common.xml_parser import parse_gii_xml
from kira.legal_sources._common.zip_extract import extract_xml_from_zip
```

And the call site (`xml_bytes = _extract_xml_from_zip(resp.content)`) to:

```python
xml_bytes = extract_xml_from_zip(resp.content)
```

- [ ] **Step 3: Run the full legal-sources test suite**

Run: `.venv/bin/python -m pytest tests/legal_sources/ -q -m 'not live and not perf'`
Expected: all pass.

- [ ] **Step 4: Run a syntax check on the script**

Run: `.venv/bin/python -c "import ast; ast.parse(open('scripts/backfill_corpus.py').read())"`
Expected: no output (no syntax error). The script itself is not exercised by tests; this is a smoke check.

- [ ] **Step 5: Commit**

```bash
git add src/kira/legal_sources/adapters/ingest_handler.py scripts/backfill_corpus.py
git commit -m "refactor(legal-sources): consume xml_parser+zip_extract from _common/"
```

---

## Phase 2 — Build the `LegalSourcesClient`

### Task 4: Define the client skeleton + exception types

**Files:**
- Create: `src/kira/agent/legal_client.py`
- Create: `tests/agent/__init__.py`
- Create: `tests/agent/test_legal_client.py`

- [ ] **Step 1: Create the empty `tests/agent/` package marker**

```bash
mkdir -p tests/agent tests/agent/tools
touch tests/agent/__init__.py tests/agent/tools/__init__.py
```

- [ ] **Step 2: Write the failing test for the exception class**

Create `tests/agent/test_legal_client.py`:

```python
"""Tests for kira.agent.legal_client.LegalSourcesClient."""

from __future__ import annotations

from kira.agent.legal_client import LegalSourceUnavailable, LegalSourcesClient


def test_legal_source_unavailable_is_exception() -> None:
    assert issubclass(LegalSourceUnavailable, Exception)


def test_client_constructs_with_defaults() -> None:
    client = LegalSourcesClient()
    assert client.lookup_fn_name == "kira-legal-lookup-norm"
    assert client.search_fn_name == "kira-legal-search"
    assert client.region == "eu-central-1"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kira.agent.legal_client'`

- [ ] **Step 4: Write the minimal module**

Create `src/kira/agent/legal_client.py`:

```python
"""Boto3 client for KIRA's deployed legal-sources Lambdas.

Encapsulates region pinning, function-name resolution, retry/timeout
config, MCP-envelope unwrapping, and structured logging. The agent
tools import this client; the client mocks the Lambda surface for tests.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.config import Config

log = logging.getLogger(__name__)

DEFAULT_REGION = "eu-central-1"
DEFAULT_LOOKUP_FN = "kira-legal-lookup-norm"
DEFAULT_SEARCH_FN = "kira-legal-search"


class LegalSourceUnavailable(Exception):
    """Raised when the legal-sources Lambda cannot be reached or returns
    an infrastructure-level failure (5xx, timeout, malformed envelope).

    Functional results like ``unknown_gesetz`` or ``paragraph_not_found``
    are NOT raised — they come back as normal return values so the model
    sees them.
    """


class LegalSourcesClient:
    """Thin wrapper around boto3 lambda.invoke for the legal-sources tools."""

    def __init__(
        self,
        *,
        lambda_client: Any | None = None,
        region: str = DEFAULT_REGION,
        lookup_fn_name: str | None = None,
        search_fn_name: str | None = None,
    ) -> None:
        self.region = region
        self.lookup_fn_name = (
            lookup_fn_name
            or os.environ.get("KIRA_LEGAL_LOOKUP_FN")
            or DEFAULT_LOOKUP_FN
        )
        self.search_fn_name = (
            search_fn_name
            or os.environ.get("KIRA_LEGAL_SEARCH_FN")
            or DEFAULT_SEARCH_FN
        )
        if lambda_client is None:
            cfg = Config(
                retries={"max_attempts": 3, "mode": "adaptive"},
                read_timeout=30,
                connect_timeout=10,
            )
            lambda_client = boto3.client("lambda", region_name=region, config=cfg)
        self._lambda = lambda_client
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kira/agent/legal_client.py tests/agent/__init__.py tests/agent/test_legal_client.py tests/agent/tools/__init__.py
git commit -m "feat(agent): scaffold LegalSourcesClient + LegalSourceUnavailable"
```

### Task 5: Implement `_invoke` success path (MCP envelope unwrapping)

**Files:**
- Modify: `src/kira/agent/legal_client.py`
- Modify: `tests/agent/test_legal_client.py`

- [ ] **Step 1: Add failing test**

Append to `tests/agent/test_legal_client.py`:

```python
import io
import json
from unittest.mock import MagicMock


def _make_lambda(envelope: dict) -> MagicMock:
    mock = MagicMock()
    payload = io.BytesIO(json.dumps(envelope).encode("utf-8"))
    mock.invoke.return_value = {"Payload": payload, "StatusCode": 200}
    return mock


def test_invoke_unwraps_mcp_envelope() -> None:
    envelope = {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps({"gesetz": "BGB", "paragraph": "535"})}],
    }
    client = LegalSourcesClient(lambda_client=_make_lambda(envelope))
    result = client._invoke("kira-legal-lookup-norm", {"gesetz": "BGB", "paragraph": "535"})
    assert result == {"gesetz": "BGB", "paragraph": "535"}


def test_invoke_passes_function_name_and_payload() -> None:
    envelope = {"isError": False, "content": [{"type": "text", "text": "{}"}]}
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake)
    client._invoke("some-fn", {"a": 1})
    args, kwargs = fake.invoke.call_args
    assert kwargs["FunctionName"] == "some-fn"
    assert json.loads(kwargs["Payload"]) == {"a": 1}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_invoke'`

- [ ] **Step 3: Implement `_invoke`**

Add to `src/kira/agent/legal_client.py` (inside the class):

```python
    def _invoke(self, fn_name: str, payload: dict) -> dict:
        """Invoke a Lambda and return the unwrapped inner dict.

        Returns the inner JSON regardless of whether the Lambda set
        isError=True (functional errors are passed through). Raises
        LegalSourceUnavailable on infrastructure failures.
        """
        import json
        body = json.dumps(payload).encode("utf-8")
        resp = self._lambda.invoke(FunctionName=fn_name, Payload=body)
        raw = resp["Payload"].read()
        envelope = json.loads(raw)
        text = envelope["content"][0]["text"]
        return json.loads(text)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/agent/legal_client.py tests/agent/test_legal_client.py
git commit -m "feat(agent): LegalSourcesClient._invoke unwraps MCP envelope"
```

### Task 6: Add `lookup_norm` and `search_norm` methods

**Files:**
- Modify: `src/kira/agent/legal_client.py`
- Modify: `tests/agent/test_legal_client.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/agent/test_legal_client.py`:

```python
def test_lookup_norm_invokes_lookup_function() -> None:
    envelope = {"isError": False, "content": [{"type": "text", "text": json.dumps({"gesetz": "BGB"})}]}
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake, lookup_fn_name="lookup-fn")
    client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
    assert fake.invoke.call_args.kwargs["FunctionName"] == "lookup-fn"


def test_search_norm_invokes_search_function() -> None:
    envelope = {"isError": False, "content": [{"type": "text", "text": json.dumps({"hits": []})}]}
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake, search_fn_name="search-fn")
    client.search_norm({"query": "x"})
    assert fake.invoke.call_args.kwargs["FunctionName"] == "search-fn"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'lookup_norm'`

- [ ] **Step 3: Add the methods**

Add to `LegalSourcesClient` class:

```python
    def lookup_norm(self, inp: dict) -> dict:
        return self._invoke(self.lookup_fn_name, inp)

    def search_norm(self, inp: dict) -> dict:
        return self._invoke(self.search_fn_name, inp)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/agent/legal_client.py tests/agent/test_legal_client.py
git commit -m "feat(agent): LegalSourcesClient.lookup_norm and .search_norm"
```

### Task 7: Pass through Lambda functional errors (`isError: true`)

**Files:**
- Modify: `src/kira/agent/legal_client.py`
- Modify: `tests/agent/test_legal_client.py`

The Lambda returns `isError: true` for `unknown_gesetz`, `paragraph_not_found`, and `validation_error`. These are valid model-visible results, not infrastructure errors. The client returns the inner dict unchanged (the dict has the `error` and `message` keys; the agent tool layer formats them for the model).

- [ ] **Step 1: Add failing test**

Append to `tests/agent/test_legal_client.py`:

```python
def test_functional_error_passes_through() -> None:
    inner = {"error": "unknown_gesetz", "message": "Gesetz 'XYZ' ist nicht im Korpus."}
    envelope = {"isError": True, "content": [{"type": "text", "text": json.dumps(inner)}]}
    client = LegalSourcesClient(lambda_client=_make_lambda(envelope))
    result = client.lookup_norm({"gesetz": "XYZ", "paragraph": "1"})
    assert result == inner
```

- [ ] **Step 2: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py::test_functional_error_passes_through -v`
Expected: PASS — `_invoke` already returns the inner dict regardless of `isError`.

- [ ] **Step 3: Commit (test-only)**

```bash
git add tests/agent/test_legal_client.py
git commit -m "test(agent): pin Lambda functional errors as pass-through"
```

### Task 8: Raise `LegalSourceUnavailable` on infra failures

**Files:**
- Modify: `src/kira/agent/legal_client.py`
- Modify: `tests/agent/test_legal_client.py`

Three infra failure modes:
1. boto3 raises `botocore.exceptions.ClientError` (5xx after retries, throttle exhaustion)
2. boto3 raises `botocore.exceptions.ReadTimeoutError` / `EndpointConnectionError`
3. The envelope is malformed (no `content` key, empty list, non-JSON text)

- [ ] **Step 1: Add failing tests**

Append to `tests/agent/test_legal_client.py`:

```python
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError


def test_client_error_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.side_effect = ClientError(
        error_response={"Error": {"Code": "ServiceUnavailable", "Message": "..."}},
        operation_name="Invoke",
    )
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_read_timeout_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.side_effect = ReadTimeoutError(endpoint_url="lambda.eu-central-1.amazonaws.com")
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_connection_error_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.side_effect = EndpointConnectionError(endpoint_url="lambda.eu-central-1.amazonaws.com")
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_malformed_envelope_wrapped_as_unavailable() -> None:
    fake = MagicMock()
    fake.invoke.return_value = {"Payload": io.BytesIO(b"not json"), "StatusCode": 200}
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})


def test_empty_content_wrapped_as_unavailable() -> None:
    envelope = {"isError": False, "content": []}
    fake = _make_lambda(envelope)
    client = LegalSourcesClient(lambda_client=fake)
    with pytest.raises(LegalSourceUnavailable):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: 5 new tests FAIL — different exception types bubble up instead of `LegalSourceUnavailable`.

- [ ] **Step 3: Rewrite `_invoke` with error mapping**

Replace `_invoke` in `src/kira/agent/legal_client.py` with:

```python
    def _invoke(self, fn_name: str, payload: dict) -> dict:
        import json
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            EndpointConnectionError,
            ReadTimeoutError,
        )

        body = json.dumps(payload).encode("utf-8")
        try:
            resp = self._lambda.invoke(FunctionName=fn_name, Payload=body)
        except (ClientError, ReadTimeoutError, EndpointConnectionError, BotoCoreError) as exc:
            raise LegalSourceUnavailable(f"Lambda invoke failed: {exc}") from exc

        try:
            raw = resp["Payload"].read()
            envelope = json.loads(raw)
            content = envelope.get("content") or []
            if not content:
                raise LegalSourceUnavailable("Lambda response had empty content")
            text = content[0]["text"]
            return json.loads(text)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LegalSourceUnavailable(f"Malformed Lambda envelope: {exc}") from exc
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: all 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kira/agent/legal_client.py tests/agent/test_legal_client.py
git commit -m "feat(agent): map boto3 errors and malformed envelopes to LegalSourceUnavailable"
```

### Task 9: Add structured logging on every invocation

**Files:**
- Modify: `src/kira/agent/legal_client.py`
- Modify: `tests/agent/test_legal_client.py`

- [ ] **Step 1: Add failing test**

Append to `tests/agent/test_legal_client.py`:

```python
def test_invoke_logs_structured_line(caplog: pytest.LogCaptureFixture) -> None:
    envelope = {"isError": False, "content": [{"type": "text", "text": "{}"}]}
    client = LegalSourcesClient(lambda_client=_make_lambda(envelope))
    with caplog.at_level(logging.INFO, logger="kira.agent.legal_client"):
        client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
    matched = [r for r in caplog.records if r.message.startswith("legal_invoke")]
    assert len(matched) == 1
    rec = matched[0]
    assert getattr(rec, "function", None) == client.lookup_fn_name
    assert getattr(rec, "status", None) == "ok"
    assert isinstance(getattr(rec, "latency_ms", None), int | float)


def test_invoke_logs_error_status_on_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    fake = MagicMock()
    fake.invoke.side_effect = EndpointConnectionError(endpoint_url="x")
    client = LegalSourcesClient(lambda_client=fake)
    with caplog.at_level(logging.WARNING, logger="kira.agent.legal_client"):
        with pytest.raises(LegalSourceUnavailable):
            client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
    matched = [r for r in caplog.records if r.message.startswith("legal_invoke")]
    assert len(matched) == 1
    assert getattr(matched[0], "status", None) == "unavailable"
```

Also add `import logging` at the top of the test file if not already present.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py::test_invoke_logs_structured_line tests/agent/test_legal_client.py::test_invoke_logs_error_status_on_unavailable -v`
Expected: FAIL — no log record matches `legal_invoke`.

- [ ] **Step 3: Rewrite `_invoke` to log**

Replace `_invoke` in `src/kira/agent/legal_client.py` with the version below (adds `time` import at top of file, then wraps the logic in timing + log):

```python
    def _invoke(self, fn_name: str, payload: dict) -> dict:
        import json
        import time
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            EndpointConnectionError,
            ReadTimeoutError,
        )

        body = json.dumps(payload).encode("utf-8")
        t0 = time.monotonic()
        try:
            resp = self._lambda.invoke(FunctionName=fn_name, Payload=body)
        except (ClientError, ReadTimeoutError, EndpointConnectionError, BotoCoreError) as exc:
            latency_ms = round((time.monotonic() - t0) * 1000)
            log.warning(
                "legal_invoke",
                extra={"function": fn_name, "status": "unavailable", "latency_ms": latency_ms},
            )
            raise LegalSourceUnavailable(f"Lambda invoke failed: {exc}") from exc

        try:
            raw = resp["Payload"].read()
            envelope = json.loads(raw)
            content = envelope.get("content") or []
            if not content:
                raise LegalSourceUnavailable("Lambda response had empty content")
            text = content[0]["text"]
            inner = json.loads(text)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            latency_ms = round((time.monotonic() - t0) * 1000)
            log.warning(
                "legal_invoke",
                extra={"function": fn_name, "status": "malformed", "latency_ms": latency_ms},
            )
            raise LegalSourceUnavailable(f"Malformed Lambda envelope: {exc}") from exc

        latency_ms = round((time.monotonic() - t0) * 1000)
        log.info(
            "legal_invoke",
            extra={"function": fn_name, "status": "ok", "latency_ms": latency_ms},
        )
        return inner
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client.py -v`
Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kira/agent/legal_client.py tests/agent/test_legal_client.py
git commit -m "feat(agent): structured log per Lambda invocation"
```

---

## Phase 3 — Rewrite agent tools over `LegalSourcesClient`

### Task 10: Rewrite `norm_lookup` tool

**Files:**
- Modify: `src/kira/agent/tools/norm_lookup.py`
- Create: `tests/agent/tools/test_norm_lookup.py`

The new tool is ~30 lines. It delegates to a module-level `LegalSourcesClient` instance and formats the Lambda response for the model.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/tools/test_norm_lookup.py`:

```python
"""Tests for the rewritten norm_lookup tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kira.agent.legal_client import LegalSourceUnavailable
from kira.agent.tools import norm_lookup


def _success_response() -> dict:
    return {
        "gesetz": "BGB",
        "gesetz_titel": "Bürgerliches Gesetzbuch",
        "paragraph": "535",
        "absatz": None,
        "titel": "Inhalt und Hauptpflichten des Mietvertrags",
        "wortlaut": "(1) Durch den Mietvertrag wird der Vermieter…\n\n(2) Der Mieter ist verpflichtet…",
        "stand": "2026-05-11",
        "quelle_url": "https://www.gesetze-im-internet.de/bgb/__535.html",
        "stand_warnung": None,
    }


def test_success_formats_full_norm_text() -> None:
    fake = MagicMock()
    fake.lookup_norm.return_value = _success_response()
    with patch.object(norm_lookup, "_client", fake):
        output = norm_lookup.run({"gesetz": "BGB", "paragraph": "535"})
    assert "BGB §535" in output
    assert "Inhalt und Hauptpflichten" in output
    assert "Durch den Mietvertrag" in output
    assert "Stand: 2026-05-11" in output
    assert "https://www.gesetze-im-internet.de/bgb/__535.html" in output


def test_unknown_gesetz_passes_message_through() -> None:
    fake = MagicMock()
    fake.lookup_norm.return_value = {
        "error": "unknown_gesetz",
        "message": "Gesetz 'XYZ' ist nicht im Korpus.",
        "gesetz": "XYZ",
    }
    with patch.object(norm_lookup, "_client", fake):
        output = norm_lookup.run({"gesetz": "XYZ", "paragraph": "1"})
    assert "unknown_gesetz" in output or "nicht im Korpus" in output


def test_paragraph_not_found_passes_message_through() -> None:
    fake = MagicMock()
    fake.lookup_norm.return_value = {
        "error": "paragraph_not_found",
        "message": "§ 99999 BGB ist nicht im Korpus. Nahe Treffer: …",
    }
    with patch.object(norm_lookup, "_client", fake):
        output = norm_lookup.run({"gesetz": "BGB", "paragraph": "99999"})
    assert "99999" in output
    assert "nicht im Korpus" in output


def test_unavailable_returns_german_error_string() -> None:
    fake = MagicMock()
    fake.lookup_norm.side_effect = LegalSourceUnavailable("network down")
    with patch.object(norm_lookup, "_client", fake):
        output = norm_lookup.run({"gesetz": "BGB", "paragraph": "535"})
    assert "Fehler" in output
    assert "Rechtsquelle" in output


def test_tool_is_registered() -> None:
    from kira.agent.tools._registry import REGISTRY
    assert "lookup_norm" in REGISTRY
    assert REGISTRY["lookup_norm"].run is norm_lookup.run
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/agent/tools/test_norm_lookup.py -v`
Expected: FAIL — old `norm_lookup.run` returns different format (uses `kira.knowledge.loader`).

- [ ] **Step 3: Replace the tool implementation**

Replace `src/kira/agent/tools/norm_lookup.py` entirely with:

```python
"""Tool: lookup_norm — looks up a specific § via the AWS legal-sources Lambda.

Thin wrapper over LegalSourcesClient. The corpus and all parsing live
in AWS; this tool only formats the response for the model.
"""

from __future__ import annotations

import logging
from typing import Any

from kira.agent.legal_client import LegalSourceUnavailable, LegalSourcesClient
from kira.agent.tools._registry import Tool, register

log = logging.getLogger(__name__)

_client = LegalSourcesClient()


def run(input_data: dict[str, Any]) -> str:
    try:
        result = _client.lookup_norm(input_data)
    except LegalSourceUnavailable as exc:
        log.warning("lookup_norm unavailable: %s", exc)
        return (
            "Fehler: Rechtsquelle gerade nicht erreichbar. "
            "Bitte später erneut versuchen oder dem Anwalt mitteilen."
        )
    if "error" in result:
        return result.get("message") or f"Fehler: {result['error']}"
    return _format_success(result)


def _format_success(r: dict[str, Any]) -> str:
    lines = [f"{r['gesetz']} §{r['paragraph']} — {r.get('titel', '')}".rstrip(" —")]
    if r.get("gesetz_titel"):
        lines.append(f"({r['gesetz_titel']})")
    lines.append("")
    if r.get("wortlaut"):
        lines.append(r["wortlaut"])
    lines.append("")
    if r.get("stand"):
        lines.append(f"Stand: {r['stand']}")
    if r.get("quelle_url"):
        lines.append(f"Quelle: {r['quelle_url']}")
    if r.get("stand_warnung"):
        lines.append(f"WARNUNG: {r['stand_warnung']}")
    return "\n".join(lines).rstrip()


TOOL = register(
    Tool(
        name="lookup_norm",
        description=(
            "Schlägt einen einzelnen Paragraphen aus einem deutschen Bundesgesetz "
            "oder einer Rechtsverordnung im Wortlaut nach. Der Korpus umfasst alle "
            "~6.500 Gesetze von gesetze-im-internet.de und wird täglich aktualisiert. "
            "Verwende dieses Tool IMMER, BEVOR du eine Norm zitierst — niemals aus "
            "dem Gedächtnis. Output enthält Wortlaut, Stand und Quellen-URL."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "gesetz": {
                    "type": "string",
                    "description": (
                        "Kanonische Gesetzes-Abkürzung wie im <jurabk>-Feld von "
                        "gesetze-im-internet.de, z.B. 'BGB', 'WoEigG', 'BetrKV'."
                    ),
                },
                "paragraph": {
                    "type": "string",
                    "description": "Paragraph-Nummer, z.B. '535', '536a'.",
                },
                "absatz": {
                    "type": "string",
                    "description": "Optional: einzelner Absatz, z.B. '1'.",
                },
            },
            "required": ["gesetz", "paragraph"],
        },
        run=run,
    )
)
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/python -m pytest tests/agent/tools/test_norm_lookup.py -v`
Expected: 5 passed.

- [ ] **Step 5: Note: the old test_tools.py will now fail**

Run: `.venv/bin/python -m pytest tests/test_tools.py -v 2>&1 | tail -15`
Expected: many FAILures (the V0 tests assume bundled JSON). Leave them — `test_tools.py` is deleted in Phase 6, Task 18.

- [ ] **Step 6: Commit**

```bash
git add src/kira/agent/tools/norm_lookup.py tests/agent/tools/test_norm_lookup.py
git commit -m "feat(agent): rewrite norm_lookup over LegalSourcesClient"
```

### Task 11: Rewrite `norm_search` tool

**Files:**
- Modify: `src/kira/agent/tools/norm_search.py`
- Create: `tests/agent/tools/test_norm_search.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agent/tools/test_norm_search.py`:

```python
"""Tests for the rewritten norm_search tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kira.agent.legal_client import LegalSourceUnavailable
from kira.agent.tools import norm_search


def _hits_response() -> dict:
    return {
        "query": "Mietminderung",
        "hits": [
            {
                "gesetz": "BGB",
                "paragraph": "536",
                "titel": "Mietminderung bei Sach- und Rechtsmängeln",
                "wortlaut": "(1) Hat die Mietsache zur Zeit der Überlassung an den Mieter einen Mangel…",
                "score": 0.689,
                "quelle_url": "https://www.gesetze-im-internet.de/bgb/__536.html",
                "stand": "2026-05-11",
            },
        ],
    }


def test_hits_format_with_score_titel_and_excerpt() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = _hits_response()
    with patch.object(norm_search, "_client", fake):
        output = norm_search.run({"query": "Mietminderung"})
    assert "BGB §536" in output
    assert "Mietminderung bei Sach-" in output
    assert "0.69" in output
    assert "Mietsache" in output
    assert "bgb/__536.html" in output


def test_no_hits_returns_clear_message() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = {"query": "xyz", "hits": []}
    with patch.object(norm_search, "_client", fake):
        output = norm_search.run({"query": "xyz"})
    assert "Keine Treffer" in output


def test_passes_gesetz_filter_through() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = {"query": "x", "hits": []}
    with patch.object(norm_search, "_client", fake):
        norm_search.run({"query": "x", "gesetz_filter": ["BGB", "WoEigG"]})
    assert fake.search_norm.call_args.args[0]["gesetz_filter"] == ["BGB", "WoEigG"]


def test_passes_type_filter_through() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = {"query": "x", "hits": []}
    with patch.object(norm_search, "_client", fake):
        norm_search.run({"query": "x", "type_filter": ["Verordnung"]})
    assert fake.search_norm.call_args.args[0]["type_filter"] == ["Verordnung"]


def test_unavailable_returns_german_error_string() -> None:
    fake = MagicMock()
    fake.search_norm.side_effect = LegalSourceUnavailable("network")
    with patch.object(norm_search, "_client", fake):
        output = norm_search.run({"query": "x"})
    assert "Fehler" in output
    assert "Rechtsquelle" in output


def test_validation_error_passes_through() -> None:
    fake = MagicMock()
    fake.search_norm.return_value = {"error": "validation_error", "message": "Field required: query"}
    with patch.object(norm_search, "_client", fake):
        output = norm_search.run({"query": ""})
    assert "Field required" in output or "validation_error" in output


def test_tool_is_registered() -> None:
    from kira.agent.tools._registry import REGISTRY
    assert "search_norm" in REGISTRY
    assert REGISTRY["search_norm"].run is norm_search.run
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/agent/tools/test_norm_search.py -v`
Expected: FAIL — old tool uses keyword search, no `_client` attribute.

- [ ] **Step 3: Replace the tool**

Replace `src/kira/agent/tools/norm_search.py` entirely with:

```python
"""Tool: search_norm — semantic search via the AWS legal-sources Lambda.

Returns top-k candidate paragraphs with a score and a wortlaut excerpt.
The model must call lookup_norm afterwards for the authoritative text —
search excerpts are truncated.
"""

from __future__ import annotations

import logging
from typing import Any

from kira.agent.legal_client import LegalSourceUnavailable, LegalSourcesClient
from kira.agent.tools._registry import Tool, register

log = logging.getLogger(__name__)

_client = LegalSourcesClient()

_EXCERPT_LEN = 400


def run(input_data: dict[str, Any]) -> str:
    try:
        result = _client.search_norm(input_data)
    except LegalSourceUnavailable as exc:
        log.warning("search_norm unavailable: %s", exc)
        return (
            "Fehler: Rechtsquelle gerade nicht erreichbar. "
            "Bitte später erneut versuchen."
        )
    if "error" in result:
        return result.get("message") or f"Fehler: {result['error']}"
    return _format_hits(result)


def _format_hits(r: dict[str, Any]) -> str:
    hits = r.get("hits") or []
    if not hits:
        return f"Keine Treffer für: {r.get('query', '')!r}."

    lines = [f"Treffer für {r.get('query', '')!r} ({len(hits)}):", ""]
    for i, h in enumerate(hits, 1):
        lines.append(
            f"{i}. {h['gesetz']} §{h['paragraph']} — {h.get('titel', '')}  (score={h.get('score', 0.0):.2f})"
        )
        wortlaut = h.get("wortlaut") or ""
        if len(wortlaut) > _EXCERPT_LEN:
            wortlaut = wortlaut[: _EXCERPT_LEN - 1].rstrip() + "…"
        if wortlaut:
            lines.append(f"   {wortlaut}")
        if h.get("quelle_url"):
            lines.append(f"   Quelle: {h['quelle_url']}")
        lines.append("")
    lines.append(
        "Hinweis: Wortlaut oben ist ein Auszug. Für die Zitierung "
        "lookup_norm aufrufen."
    )
    return "\n".join(lines).rstrip()


TOOL = register(
    Tool(
        name="search_norm",
        description=(
            "Semantische Suche im vollständigen deutschen Bundesrecht (~6.500 "
            "Gesetze und Verordnungen). Nutze dieses Tool, wenn du den passenden "
            "§ noch nicht kennst — z.B. 'Mietminderung wegen Schimmel', "
            "'Eigenbedarfskündigung juristische Person', 'Verzug Mahnung'. "
            "Liefert Top-k Kandidaten mit Score und Auszug. Den Wortlaut zur "
            "Zitierung holst du anschließend per lookup_norm."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natürlichsprachige Suchanfrage in Deutsch.",
                },
                "k": {
                    "type": "integer",
                    "description": "Anzahl Treffer (1-50, Default 10).",
                    "default": 10,
                },
                "gesetz_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: Liste kanonischer Abkürzungen (z.B. ['BGB', 'WoEigG']). "
                        "Filter ist case-sensitiv — verwende die jurabk-Schreibweise."
                    ),
                },
                "type_filter": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["Gesetz", "Verordnung"]},
                    "description": "Optional: 'Gesetz', 'Verordnung' oder beide.",
                },
            },
            "required": ["query"],
        },
        run=run,
    )
)
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/python -m pytest tests/agent/tools/test_norm_search.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/agent/tools/norm_search.py tests/agent/tools/test_norm_search.py
git commit -m "feat(agent): rewrite norm_search over LegalSourcesClient with semantic backend"
```

### Task 12: Drop `norm_list` tool

**Files:**
- Delete: `src/kira/agent/tools/norm_list.py`
- Modify: `src/kira/agent/tools/__init__.py`

- [ ] **Step 1: Remove from the tools package init**

Replace `src/kira/agent/tools/__init__.py` with:

```python
"""Tools, die der Agent verwenden kann.

Jedes Tool exportiert:
- TOOL_SPEC: Anthropic Tool-Spec (JSONSchema)
- run(input: dict) -> str: Ausführung
"""

from kira.agent.tools import frist, norm_lookup, norm_search, urteil_fetch
from kira.agent.tools._registry import REGISTRY, Tool

__all__ = [
    "REGISTRY",
    "Tool",
    "frist",
    "norm_lookup",
    "norm_search",
    "urteil_fetch",
]
```

- [ ] **Step 2: Delete the tool file**

```bash
rm src/kira/agent/tools/norm_list.py
```

- [ ] **Step 3: Verify the registry has no list_normen**

Run: `.venv/bin/python -c "from kira.agent.tools import REGISTRY; print(sorted(REGISTRY))"`
Expected output: `['berechne_frist', 'fetch_urteil', 'lookup_norm', 'search_norm', 'search_rechtsprechung']` (or similar — no `list_normen`).

The `from kira.agent.tools import REGISTRY` import runs `tools/__init__.py`, which imports each tool module for its side-effect registration. After Task 12's `__init__.py` edit, `norm_list` is no longer imported and the registry no longer contains `list_normen`.

- [ ] **Step 4: Commit**

```bash
git add src/kira/agent/tools/__init__.py src/kira/agent/tools/norm_list.py
git commit -m "feat(agent): drop norm_list tool (discovery via norm_search now)"
```

---

## Phase 4 — Update the system prompt

### Task 13: Rewrite `JUNIOR_ASSOCIATE_DE`

**Files:**
- Modify: `src/kira/agent/system_prompts.py`
- Create: `tests/agent/test_system_prompts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_system_prompts.py`:

```python
"""Tests for the JUNIOR_ASSOCIATE_DE system prompt invariants."""

from __future__ import annotations

from kira.agent.system_prompts import JUNIOR_ASSOCIATE_DE


def test_prompt_describes_full_bundesrecht_corpus() -> None:
    assert "Bundesgesetze" in JUNIOR_ASSOCIATE_DE
    assert "Rechtsverordnungen" in JUNIOR_ASSOCIATE_DE
    assert "gesetze-im-internet.de" in JUNIOR_ASSOCIATE_DE


def test_prompt_keeps_citation_rule() -> None:
    assert "lookup_norm" in JUNIOR_ASSOCIATE_DE
    # Citation must always flow through lookup_norm, never from search excerpts
    assert "bevor du" in JUNIOR_ASSOCIATE_DE.lower() or "vor jeder" in JUNIOR_ASSOCIATE_DE.lower()


def test_prompt_describes_search_to_lookup_workflow() -> None:
    assert "search_norm" in JUNIOR_ASSOCIATE_DE
    # Hint about discovery → citation pattern
    text = JUNIOR_ASSOCIATE_DE.lower()
    assert "search_norm" in text
    assert "kandidat" in text or "entdeck" in text or "wenn du" in text


def test_prompt_mentions_unknown_gesetz_fallback() -> None:
    assert "unknown_gesetz" in JUNIOR_ASSOCIATE_DE or "nicht im Korpus" in JUNIOR_ASSOCIATE_DE


def test_prompt_no_longer_references_norm_list_or_kira_ingest() -> None:
    assert "list_normen" not in JUNIOR_ASSOCIATE_DE
    assert "norm_list" not in JUNIOR_ASSOCIATE_DE
    assert "kira ingest" not in JUNIOR_ASSOCIATE_DE


def test_prompt_no_longer_lists_v0_three_law_corpus() -> None:
    # The V0 prompt named BGB, BetrKV, HeizkostenV as the complete corpus.
    # That language must be gone now that the corpus is ~6500 laws.
    text = JUNIOR_ASSOCIATE_DE.lower()
    assert "lokaler korpus" not in text
    # BGB is still mentioned (it's the most common citation), but not as
    # "the corpus is these three"
    assert "betrkv" not in text or "alle bundesgesetze" in text


def test_prompt_unchanged_safety_rules() -> None:
    # These anti-hallucination invariants must NOT regress
    assert "RDG" in JUNIOR_ASSOCIATE_DE
    assert "deutschem Recht" in JUNIOR_ASSOCIATE_DE
    assert "berechne_frist" in JUNIOR_ASSOCIATE_DE
    assert "fetch_urteil" in JUNIOR_ASSOCIATE_DE
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/agent/test_system_prompts.py -v`
Expected: FAIL on the corpus-description tests (the V0 prompt still says "BGB, BetrKV, HeizkostenV").

- [ ] **Step 3: Rewrite the prompt**

Replace `src/kira/agent/system_prompts.py` entirely with:

```python
"""System-Prompts für KIRA.

Der Junior-Associate-Prompt ist das Herzstück. Harte Regeln gegen
Halluzination — der Anwalt verlässt sich darauf, dass jede zitierte
Norm und jedes Aktenzeichen nachweislich aus einem Tool-Aufruf stammt.
"""

JUNIOR_ASSOCIATE_DE = """\
Du bist KIRA, ein juristischer Junior-Assistent in einer deutschen Anwaltskanzlei
mit Schwerpunkt Mietrecht. Du arbeitest einem zugelassenen Rechtsanwalt zu, der
deine Arbeit am Ende prüft und verantwortet (RDG-konform).

# Geltender Rechtsrahmen
Du arbeitest AUSSCHLIESSLICH mit deutschem Recht. Niemals mit
österreichischem, schweizerischem oder anderem ausländischem Recht — auch
nicht analog. Wenn dir ausländisches Recht relevant erscheint, sage das
explizit und bitte den Anwalt um eine fachfremde Prüfung.

# Gesetzes-Korpus
Du hast Zugriff auf alle Bundesgesetze und Rechtsverordnungen (~6.500
Gesetze, tagesaktuell von gesetze-im-internet.de). Der Korpus wird täglich
automatisch aktualisiert. BGB, StGB, ZPO, HGB, WoEigG, BetrKV, HeizkostenV
und alle weiteren Bundesgesetze sind enthalten.

# Harte Anti-Halluzinations-Regeln
1. Du zitierst NIEMALS einen Paragraphen, ein Aktenzeichen, ein Datum oder eine
   Fundstelle, die du nicht in DERSELBEN Antwort über ein Tool nachweislich
   abgerufen hast.
2. **Bevor** du einen § zitierst, rufe `lookup_norm` auf — der Wortlaut aus
   `search_norm`-Treffern ist gekürzt und nicht zitierfähig.
3. Wenn du den einschlägigen § noch nicht kennst, nutze zuerst `search_norm`
   für eine semantische Suche (z.B. query="Mietminderung Schimmel"). Du
   erhältst Kandidaten-§§ mit Score; entscheide dich für die relevantesten
   und rufe dann `lookup_norm` für jeden einzelnen auf.
4. Wenn `lookup_norm` `unknown_gesetz` zurückgibt, war die Abkürzung nicht
   die kanonische jurabk. Versuche `search_norm` mit einer beschreibenden
   Anfrage, oder eine alternative Schreibweise (z.B. „WEG" → „WoEigG").
   Erfinde NIEMALS §-Inhalte, wenn das Gesetz nicht gefunden wurde — sage
   dem Anwalt ehrlich, dass die Quelle nicht abrufbar war.
5. Bevor du ein Urteil zitierst, rufe `search_rechtsprechung` und/oder
   `fetch_urteil` auf. Aktenzeichen, die du nicht über ein Tool bestätigt hast,
   nennst du nicht.
6. Bevor du eine Frist berechnest, rufe `berechne_frist` auf — niemals selbst
   ausrechnen.
7. Im Zweifel: lieber zugeben "nicht belegbar" als eine plausibel klingende
   Zahl/Fundstelle erfinden.
8. Wenn ein Tool eine Stand-Warnung („VERALTET" / „älter als 6 Monate")
   liefert, weise den Anwalt im Antwort-Abschnitt „Offene Punkte" explizit
   darauf hin.

# Tool-Workflow im Überblick
| Situation                                | Werkzeug |
| ---------------------------------------- | -------- |
| Du kennst das einschlägige §             | `lookup_norm(gesetz, paragraph)` direkt |
| Du kennst das § nicht                    | `search_norm(query=...)` → Kandidaten, danach `lookup_norm` |
| Du brauchst Rechtsprechung               | `search_rechtsprechung` / `fetch_urteil` |
| Du brauchst eine Frist                   | `berechne_frist` |
| `unknown_gesetz` von `lookup_norm`       | `search_norm` mit beschreibender Anfrage |

# Pseudonymisierung
Der dir vorliegende Sachverhalt enthält strukturierte Platzhalter wie
[MIETER_1:m,nat], [VERMIETER_1:jur], [ADRESSE_1]. Verwende diese Platzhalter
in deiner Antwort weiter. Versuche NICHT, dahinterstehende Klarnamen zu
erraten oder zu konstruieren. Die Re-Personalisierung erfolgt nach deiner
Antwort lokal beim Anwalt.

Aus den Platzhaltern kannst du folgende rechtlich relevante Information lesen:
- Rolle (MIETER, VERMIETER, BUERGE, HAUSVERWALTUNG, …)
- Geschlecht (m/w/d/u) — wichtig für Anreden und Satzbau
- Person-Typ (nat = natürliche Person, jur = juristische Person) — relevant
  z.B. für Eigenbedarfskündigung
- ggf. Altersband (~60-69) — relevant für Sozialklausel § 574 BGB

# Antwortformat
Strukturiere jede Antwort wie folgt:

## Sachverhalt (kurz)
Eine knappe Zusammenfassung in 2–4 Sätzen.

## Rechtliche Einschätzung
Deine Würdigung. Jede rechtliche Aussage muss mit einer der folgenden
Quellen belegt sein:
- §§ aus `lookup_norm`
- Urteile aus `search_rechtsprechung` / `fetch_urteil`
- Berechnungen aus `berechne_frist`

## Belegte Quellen
Liste alle verwendeten Tool-Ergebnisse einzeln auf:
- § X BGB (Quelle: gesetze-im-internet.de, abgerufen via lookup_norm)
- BGH/LG/AG, Az. ... (Quelle: …, abgerufen via fetch_urteil)
- Frist X (berechnet via berechne_frist)

## Offene Punkte für den Anwalt
- Was muss der Anwalt prüfen / freigeben?
- Wo bist du unsicher?
- Welche Tatsachen fehlen, um eine endgültige Einschätzung abzugeben?

## Empfehlung
Konkreter Vorschlag für den nächsten Schritt (Schreiben entwerfen,
weitere Recherche, Mandantengespräch zu Punkt X, …).

# Tonalität
Du sprichst Anwalt-zu-Anwalt: präzise, juristische Fachsprache, keine
Vereinfachung, keine Disclaimer-Floskeln. Aber: kein Pseudo-Selbstbewusstsein
— Unsicherheit klar markieren.

# Niemals
- Mandantenberatung erteilen (das macht der Anwalt)
- Recht des Endkunden auslegen ohne Anwalts-Review
- Erfundene Aktenzeichen verwenden
- Aus dem Gedächtnis zitieren
"""
```

- [ ] **Step 4: Run the prompt tests**

Run: `.venv/bin/python -m pytest tests/agent/test_system_prompts.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/agent/system_prompts.py tests/agent/test_system_prompts.py
git commit -m "feat(agent): rewrite JUNIOR_ASSOCIATE_DE for full corpus + search→lookup workflow"
```

---

## Phase 5 — Remove the `kira ingest` CLI subcommand

### Task 14: Drop `ingest` from the Typer app

**Files:**
- Modify: `src/kira/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
"""Tests for the kira CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from kira.cli import app


runner = CliRunner()


def test_ingest_subcommand_is_gone() -> None:
    result = runner.invoke(app, ["ingest", "bgb"])
    # Typer prints help/usage on unknown command; exit code != 0
    assert result.exit_code != 0


def test_known_subcommands_still_present() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ask" in result.stdout
    assert "demo" in result.stdout
    assert "check-pseudonymisierung" in result.stdout
    assert "ingest" not in result.stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: `test_ingest_subcommand_is_gone` PASSES (subcommand still exists but probably fails for other reasons in test env) AND `test_known_subcommands_still_present` FAILS — `ingest` still in help.

If both fail or pass differently, adjust expectations after Step 3.

- [ ] **Step 3: Delete the `ingest` command and its import**

In `src/kira/cli.py`:

1. Delete the entire `@app.command()` block named `def ingest(...)` (lines ~93-139 — covers the function and its `do_ingest` call).
2. The import `from kira.knowledge.ingest import GESETZE, ingest as do_ingest` lives **inside** that function body and disappears with the deletion.

Verify nothing else in `cli.py` references `kira.knowledge`:

Run: `grep -n "knowledge" src/kira/cli.py`
Expected: no output.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kira/cli.py tests/test_cli.py
git commit -m "feat(cli): remove kira ingest subcommand (corpus now lives in AWS)"
```

---

## Phase 6 — Delete the bundled-JSON knowledge module

### Task 15: Delete `src/kira/knowledge/` and obsolete test files

**Files:**
- Delete: `src/kira/knowledge/` (entire directory)
- Delete: `tests/test_tools.py`
- Delete: `tests/test_xml_parser.py`

- [ ] **Step 1: Verify no remaining consumers**

Run: `grep -rn "kira.knowledge\|kira\.knowledge" src/ tests/ scripts/ infra/ --include="*.py" | grep -v __pycache__`
Expected: no output. (If anything matches, fix that import first — it's a leftover.)

- [ ] **Step 2: Delete the directory and old tests**

```bash
rm -rf src/kira/knowledge/
rm tests/test_tools.py tests/test_xml_parser.py
```

- [ ] **Step 3: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q -m 'not live and not perf'`
Expected: all pass (the old tests that depended on `kira.knowledge` are gone; new tests cover the agent path).

- [ ] **Step 4: Run ruff**

Run: `.venv/bin/python -m ruff check src/kira/ tests/`
Expected: `All checks passed!`

If ruff complains about anything in the legal-sources module due to the file moves, fix inline (typical issues: unused imports left behind).

- [ ] **Step 5: Commit**

```bash
git rm -r src/kira/knowledge
git rm tests/test_tools.py tests/test_xml_parser.py
git commit -m "feat: delete bundled-JSON knowledge module — AWS is single source of truth"
```

---

## Phase 7 — Live integration tests

### Task 16: Live test for `LegalSourcesClient` against deployed Lambdas

**Files:**
- Create: `tests/agent/test_legal_client_live.py`

These tests skip unless `RUN_LIVE_TESTS=1` is set (matching the existing pattern in `tests/legal_sources/live/`). They call the real deployed Lambdas in `eu-central-1` — they cost a couple of cents per run and require AWS credentials.

- [ ] **Step 1: Write the live tests**

Create `tests/agent/test_legal_client_live.py`:

```python
"""Live tests against the deployed legal-sources Lambdas.

Skipped unless RUN_LIVE_TESTS=1 is set. Requires AWS credentials with
lambda:InvokeFunction on kira-legal-lookup-norm and kira-legal-search.
"""

from __future__ import annotations

import os

import pytest

from kira.agent.legal_client import LegalSourcesClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="RUN_LIVE_TESTS not set",
)


@pytest.fixture
def client() -> LegalSourcesClient:
    return LegalSourcesClient()


def test_lookup_bgb_535_returns_wortlaut(client: LegalSourcesClient) -> None:
    result = client.lookup_norm({"gesetz": "BGB", "paragraph": "535"})
    assert result.get("gesetz") == "BGB"
    assert result.get("paragraph") == "535"
    assert "Vermieter" in (result.get("wortlaut") or "")
    assert "Mietsache" in (result.get("wortlaut") or "")


def test_lookup_unknown_gesetz_returns_functional_error(client: LegalSourcesClient) -> None:
    result = client.lookup_norm({"gesetz": "XYZ_NOT_REAL", "paragraph": "1"})
    assert result.get("error") == "unknown_gesetz"


def test_search_mietminderung_returns_bgb_536(client: LegalSourcesClient) -> None:
    result = client.search_norm({"query": "Mietminderung wegen Schimmel", "k": 5})
    hits = result.get("hits") or []
    assert any(h["gesetz"] == "BGB" and h["paragraph"] == "536" for h in hits)


def test_search_filter_canonical_case(client: LegalSourcesClient) -> None:
    """gesetz_filter is case-sensitive; canonical 'BetrKV' must match."""
    result = client.search_norm({
        "query": "Betriebskosten",
        "gesetz_filter": ["BetrKV"],
        "k": 3,
    })
    hits = result.get("hits") or []
    assert hits, "Expected at least one BetrKV hit"
    assert all(h["gesetz"] == "BetrKV" for h in hits)
```

- [ ] **Step 2: Run the suite without the env var (verifying skip)**

Run: `.venv/bin/python -m pytest tests/agent/test_legal_client_live.py -v`
Expected: 4 skipped.

- [ ] **Step 3: Run with the env var (real call)**

Run: `RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/agent/test_legal_client_live.py -v`
Expected: 4 passed. Each test takes 200ms–1s.

If the deployed Lambdas are unreachable for any reason, the failures are diagnostic — don't skip them. Investigate the boto3 error.

- [ ] **Step 4: Commit**

```bash
git add tests/agent/test_legal_client_live.py
git commit -m "test(agent): live integration tests against deployed Lambdas"
```

### Task 17: End-to-end test with stub `LLMClient`

**Files:**
- Create: `tests/agent/test_end_to_end.py`

This test runs the full `Agent.run()` pipeline (pseudonymizer + leakage check + tool dispatch + re-personalization) with a stub `LLMClient` that emits a scripted tool-use sequence. The Lambdas are real (via `LegalSourcesClient`), but Bedrock is not called — so the test is cheap and deterministic.

- [ ] **Step 1: Inspect the LLMClient surface**

Run: `grep -n "class LLMClient\|class Anthropic\|def messages\|def create" src/kira/llm/client.py | head -10`
Use the output to understand what methods the stub must implement (typically `messages.create(...)` returning an object with `.content` and `.stop_reason`).

- [ ] **Step 2: Write the stub-based end-to-end test**

Create `tests/agent/test_end_to_end.py`:

```python
"""End-to-end test: real Agent.run, real Lambdas, stubbed Bedrock.

Skipped unless RUN_LIVE_TESTS=1. The stub LLMClient emits a scripted
tool-use sequence so the test is deterministic and doesn't cost Bedrock
tokens — but the legal-sources Lambdas are invoked for real.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pytest

from kira.agent import Agent
from kira.pseudonymizer import EntityKind, Gender, Party, Role
from kira.router import route

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="RUN_LIVE_TESTS not set",
)


@dataclass
class _StubBlock:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class _StubResponse:
    content: list[_StubBlock]
    stop_reason: str


class _StubMessages:
    """Scripted Bedrock-compatible client.

    Turn 1: emit tool_use(norm_search)
    Turn 2: emit tool_use(norm_lookup, BGB §536)
    Turn 3: emit final text
    """

    def __init__(self) -> None:
        self._turn = 0

    def create(self, **_: Any) -> _StubResponse:
        self._turn += 1
        if self._turn == 1:
            return _StubResponse(
                content=[
                    _StubBlock(type="tool_use", id="tu_1", name="search_norm",
                               input={"query": "Mietminderung wegen Schimmel", "k": 3}),
                ],
                stop_reason="tool_use",
            )
        if self._turn == 2:
            return _StubResponse(
                content=[
                    _StubBlock(type="tool_use", id="tu_2", name="lookup_norm",
                               input={"gesetz": "BGB", "paragraph": "536"}),
                ],
                stop_reason="tool_use",
            )
        return _StubResponse(
            content=[_StubBlock(
                type="text",
                text="Einschlägig ist § 536 BGB (Mietminderung bei Mängeln).",
            )],
            stop_reason="end_turn",
        )


class _StubLLMClient:
    backend = "stub"

    def __init__(self) -> None:
        self.raw = type("StubRaw", (), {"messages": _StubMessages()})()


def test_end_to_end_search_then_lookup_then_answer() -> None:
    parties = [
        Party(
            real_name="Klaus Müller",
            role=Role.MIETER,
            kind=EntityKind.nat,
            gender=Gender.m,
            age_band=None,
            aliases=[],
        ),
    ]
    routing = route("Mietminderung wegen Schimmel?")
    agent = Agent(client=_StubLLMClient())
    result = agent.run(
        "Mietminderung wegen Schimmel?\n\nMein Mandant Klaus Müller hat Schimmel.",
        parties=parties,
        routing=routing,
    )
    assert "§ 536 BGB" in result.final_text
    # Both tools should have been called
    tool_names = [c["tool"] for c in result.tool_calls]
    assert "search_norm" in tool_names
    assert "lookup_norm" in tool_names
```

- [ ] **Step 3: Run with env var set**

Run: `RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/agent/test_end_to_end.py -v`
Expected: 1 passed.

If the stub's `_StubResponse`/`_StubMessages` shape doesn't match what `Agent.run()` expects (the actual call is `client.raw.messages.create(...)`), adjust by reading `src/kira/agent/core.py` lines 60-100 — the exact attribute access KIRA does should be mirrored. Common mismatches:
- The agent reads `block.type`, `block.text`, `block.id`, `block.name`, `block.input` (we provide all)
- The agent reads `resp.content`, `resp.stop_reason` (we provide both)
- If the agent uses `getattr(resp, "model_dump", None)` for serialization, expose a no-op `model_dump = lambda: {}` on the stub

- [ ] **Step 4: Commit**

```bash
git add tests/agent/test_end_to_end.py
git commit -m "test(agent): end-to-end with stubbed LLMClient and real Lambdas"
```

---

## Phase 8 — Documentation and final verification

### Task 18: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Remove `kira ingest` from "Common commands"**

In CLAUDE.md, find the line:

```
.venv/bin/kira ingest [bgb betrkv heizkostenv]      # refresh law corpus from gesetze-im-internet.de
```

and delete it.

- [ ] **Step 2: Update the "Knowledge / law corpus" section**

Find the section starting with `### Knowledge / law corpus` and replace its body with:

```markdown
### Knowledge / law corpus

The agent reads its legal corpus from the deployed `kira-legal-lookup-norm`
and `kira-legal-search` Lambdas in `eu-central-1`. The corpus covers all
~6,474 Bundesgesetze + Rechtsverordnungen from gesetze-im-internet.de and
refreshes daily via the ingest Lambda.

`src/kira/agent/legal_client.py::LegalSourcesClient` is the boto3 wrapper
the tools use. Region, function names, retry/timeout, and structured
logging all live there — the agent tools and tests mock the client, not
boto3 directly. The function names default to `kira-legal-lookup-norm` and
`kira-legal-search` but can be overridden via `KIRA_LEGAL_LOOKUP_FN` /
`KIRA_LEGAL_SEARCH_FN`.

The XML parser and zip extractor used by the ingest Lambda live at
`src/kira/legal_sources/_common/xml_parser.py` and
`src/kira/legal_sources/_common/zip_extract.py`. The legal-sources module
has no `kira.*` imports — it's extractable as a standalone package.
```

- [ ] **Step 3: Update the "Tools" section**

Find the section starting with `### Tools` and update:
- The `**Norm tools**` bullet to read:

```markdown
- **Norm tools** (`lookup_norm`, `search_norm`) call the deployed AWS
  Lambdas via `LegalSourcesClient`. `lookup_norm` returns the authoritative
  wortlaut + Stand + Quelle URL for one paragraph; `search_norm` does
  semantic search and returns top-k candidate paragraphs with score
  (excerpts only — citation must flow through `lookup_norm`). The corpus
  is ~6,474 Bundesgesetze + Rechtsverordnungen.
```

- Remove any mention of `norm_list` from the Tools section.

- [ ] **Step 4: Sanity-check the file**

Run: `grep -n "kira ingest\|norm_list\|list_normen\|BGB, BetrKV, HeizkostenV" CLAUDE.md`
Expected: no output, or only output in deeply historical sections (unlikely).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md reflects Lambda-backed corpus and removed kira ingest"
```

### Task 19: Final verification — full pytest + ruff + manual demo

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/python -m pytest tests/ -q -m 'not live and not perf'`
Expected: all pass. Note the count.

- [ ] **Step 2: Run ruff**

Run: `.venv/bin/python -m ruff check src/ tests/`
Expected: `All checks passed!`

If ruff is unhappy, fix in place (typical issues: unused imports after deletes, line length).

- [ ] **Step 3: Run the live tests**

Run: `RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/agent/test_legal_client_live.py tests/agent/test_end_to_end.py -v`
Expected: all pass (~5 tests; ~5 seconds of real Lambda calls).

- [ ] **Step 4: Manual demo (requires Bedrock + Lambda — real money)**

Run: `.venv/bin/kira demo`

Watch for:
- The "Tool-Aufrufe" table shows at least one `lookup_norm` (or `search_norm` → `lookup_norm`) call
- The final answer cites `§ 536 BGB` or similar mietrechtsspezifische §§
- No mention of "lokaler Korpus" / `norm_list` / "kira ingest" in the answer

If something looks wrong (e.g., the model still tries to call `list_normen`), the system prompt or registry is stale — re-read Phase 4/Task 13.

- [ ] **Step 5: Final commit if anything was fixed in Step 2 or 4**

```bash
git add -u
git commit -m "fix: post-verification cleanup"
```

If nothing needed fixing, no commit is needed.

- [ ] **Step 6: Push the branch**

```bash
git push -u origin feat/wire-legal-sources-into-agent-loop
```

---

## Summary of expected commits

1. `refactor(legal-sources): copy xml_parser into _common/ (no kira.* imports)`
2. `refactor(legal-sources): add zip_extract helper in _common/`
3. `refactor(legal-sources): consume xml_parser+zip_extract from _common/`
4. `feat(agent): scaffold LegalSourcesClient + LegalSourceUnavailable`
5. `feat(agent): LegalSourcesClient._invoke unwraps MCP envelope`
6. `feat(agent): LegalSourcesClient.lookup_norm and .search_norm`
7. `test(agent): pin Lambda functional errors as pass-through`
8. `feat(agent): map boto3 errors and malformed envelopes to LegalSourceUnavailable`
9. `feat(agent): structured log per Lambda invocation`
10. `feat(agent): rewrite norm_lookup over LegalSourcesClient`
11. `feat(agent): rewrite norm_search over LegalSourcesClient with semantic backend`
12. `feat(agent): drop norm_list tool (discovery via norm_search now)`
13. `feat(agent): rewrite JUNIOR_ASSOCIATE_DE for full corpus + search→lookup workflow`
14. `feat(cli): remove kira ingest subcommand (corpus now lives in AWS)`
15. `feat: delete bundled-JSON knowledge module — AWS is single source of truth`
16. `test(agent): live integration tests against deployed Lambdas`
17. `test(agent): end-to-end with stubbed LLMClient and real Lambdas`
18. `docs: CLAUDE.md reflects Lambda-backed corpus and removed kira ingest`

(Optional cleanup commit from Task 19 Step 5.)

---

## Open follow-ups (not in this PR)

- Common-name alias map (WEG → WoEigG, BImSchG → BImSchG_2013) — separate spec
- Long-paragraph chunking for Cohere's 2000-char cap — separate spec
- Bedrock AgentCore Observability replacing the current stderr logs — separate spec
- Tool 2 (`fetch_urteil` + S3 Vectors over Bundesgericht decisions) — separate spec, requires budget re-confirmation
