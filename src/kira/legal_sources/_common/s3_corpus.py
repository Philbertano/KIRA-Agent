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
    def from_env(cls) -> LazyCorpusLoader:
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
        # Try direct slug match first (e.g. 'bgb' -> manifest['bgb']).
        entry = manifest.gesetze.get(abk)
        # Fallback: scan manifest for an entry whose `abkuerzung` matches
        # case-insensitively. Lawyers type "WEG" but the URL slug is
        # "woeigg" — the abkuerzung is the canonical lookup key.
        if entry is None:
            abk_upper = abk.upper()
            for _slug, candidate in manifest.gesetze.items():
                if candidate.abkuerzung.upper() == abk_upper:
                    entry = candidate
                    break
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
