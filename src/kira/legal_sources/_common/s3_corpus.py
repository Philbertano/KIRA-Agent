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

    # --- S3 (stub; implemented in next task) ---

    def _load_s3(self) -> dict[str, GesetzKorpus]:
        raise CorpusUnavailableError("S3 branch not yet implemented")
