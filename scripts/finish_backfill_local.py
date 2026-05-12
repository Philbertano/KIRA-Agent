"""Finish backfill from a locally-synced corpus directory.

Pre-req: `aws s3 sync s3://<bucket>/gesetze/ /tmp/kira-corpus-local/gesetze/`

This avoids the slow per-file S3 GET path entirely. Walks the local
directory, embeds in batches via Cohere on Bedrock, upserts to S3 Vectors,
and writes the top-level manifest back to S3.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import boto3
from botocore.config import Config

from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.region import REQUIRED_REGION
from kira.legal_sources._common.vector_index import VectorIndex, VectorRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("finish-local")

_BOTO_CONFIG = Config(max_pool_connections=32, retries={"max_attempts": 5, "mode": "adaptive"})


def main() -> int:
    args = _parse_args()
    bucket = os.environ["LEGAL_CORPUS_BUCKET"]
    s3 = boto3.client("s3", region_name=REQUIRED_REGION, config=_BOTO_CONFIG)
    embedder = CohereMultilingualEmbedder(
        bedrock_client=boto3.client(
            "bedrock-runtime", region_name=REQUIRED_REGION, config=_BOTO_CONFIG
        ),
    )
    vector_index = VectorIndex(
        s3vectors_client=boto3.client(
            "s3vectors", region_name=REQUIRED_REGION, config=_BOTO_CONFIG
        ),
        index_name=args.vector_index,
        vector_bucket_name=args.vector_bucket,
    )

    local_root = Path(args.local_dir)
    if not local_root.is_dir():
        log.error("Local corpus dir %s does not exist", local_root)
        return 2

    log.info("Scanning local corpus at %s", local_root)
    meta_paths = sorted(local_root.glob("*/_meta.json"))
    log.info("Found %d _meta.json files", len(meta_paths))

    manifest_entries: dict[str, dict] = {}
    embed_buffer: list[tuple[str, str, dict]] = []
    paragraphs_done = 0
    laws_done = 0
    skipped_meta = 0
    t0 = time.time()

    for meta_path in meta_paths:
        try:
            slug, m_entry, inputs_md = _load_one_law_local(meta_path)
        except Exception as exc:
            log.warning("Skip %s: %s", meta_path, exc)
            skipped_meta += 1
            continue
        manifest_entries[slug] = m_entry
        for key, text, md in inputs_md:
            embed_buffer.append((key, text, md))
            if len(embed_buffer) >= args.embed_batch:
                _flush(embed_buffer, embedder, vector_index)
                paragraphs_done += len(embed_buffer)
                embed_buffer = []
        laws_done += 1
        if laws_done % 50 == 0:
            elapsed = time.time() - t0
            rate = laws_done / elapsed if elapsed else 0.0
            remaining = (len(meta_paths) - laws_done) / rate if rate else 0.0
            log.info(
                "  %d/%d laws | %d paragraphs embedded | %.1f laws/s | ETA %.0fs",
                laws_done, len(meta_paths), paragraphs_done, rate, remaining,
            )

    if embed_buffer:
        _flush(embed_buffer, embedder, vector_index)
        paragraphs_done += len(embed_buffer)

    log.info("Writing _manifest.json (%d laws) to S3", len(manifest_entries))
    payload = {
        "version": 2,
        "stand": date.today().isoformat(),
        "gesetze": manifest_entries,
    }
    s3.put_object(
        Bucket=bucket,
        Key="gesetze/_manifest.json",
        Body=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )

    summary = {
        "laws_in_manifest": len(manifest_entries),
        "paragraphs_embedded": paragraphs_done,
        "skipped_meta": skipped_meta,
        "duration_seconds": round(time.time() - t0, 1),
    }
    log.info("DONE %s", summary)
    print(json.dumps(summary, indent=2))
    return 0


def _flush(buf, embedder, vector_index) -> None:
    texts = [t for _, t, _ in buf]
    keys = [k for k, _, _ in buf]
    mds = [m for _, _, m in buf]
    try:
        vectors = embedder.embed_documents(texts)
    except Exception as exc:
        log.error("Embed batch failed (%d items): %s", len(buf), exc)
        return
    try:
        records = [
            VectorRecord(key=k, vector=v, metadata=m)
            for k, v, m in zip(keys, vectors, mds, strict=True)
        ]
        vector_index.upsert(records)
    except Exception as exc:
        log.error("Upsert failed (%d items): %s", len(buf), exc)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--local-dir", default="/tmp/kira-corpus-local/gesetze")
    p.add_argument("--vector-index", default="kira-norms")
    p.add_argument("--vector-bucket", default="kira-legal-norms")
    p.add_argument("--embed-batch", type=int, default=96)
    return p.parse_args()


def _truncate_to_bytes(s: str, max_bytes: int) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    trimmed = encoded[: max_bytes - len(" […gekürzt…]".encode())]
    return trimmed.decode("utf-8", errors="ignore") + " […gekürzt…]"


def _load_one_law_local(meta_path: Path):
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    abk = meta["abkuerzung"]
    abk_slug = meta_path.parent.name
    titel = meta["titel"]
    type_str = meta["type"]
    stand = meta["stand"]

    m_entry = {
        "abkuerzung": abk,
        "titel": titel,
        "type": type_str,
        "meta_key": f"gesetze/{abk_slug}/_meta.json",
        "upstream_etag": "",
        "upstream_last_modified": "",
    }

    inputs_md = []
    for paragraph, idx_entry in meta["paragraphen"].items():
        p_path = meta_path.parent / f"{paragraph}.json"
        if not p_path.exists():
            continue
        try:
            p_payload = json.loads(p_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        wortlaut = "\n\n".join(
            f"({a['nummer']}) {a['text']}" for a in p_payload.get("absaetze", [])
        )
        vec_key = f"{abk_slug}-{paragraph}"
        embed_text = f"{abk} §{paragraph} ({p_payload.get('titel','')}):\n\n{wortlaut}"
        md = {
            "gesetz": abk,
            "paragraph": paragraph,
            "abkuerzung": abk,
            "type": type_str,
            "titel": p_payload.get("titel", ""),
            "wortlaut": _truncate_to_bytes(wortlaut, 32000),
            "quelle_url": p_payload.get("quelle_url", ""),
            "stand": stand,
            "content_sha256": idx_entry.get("content_sha256", ""),
        }
        inputs_md.append((vec_key, embed_text, md))
    return abk_slug, m_entry, inputs_md


if __name__ == "__main__":
    sys.exit(main())
