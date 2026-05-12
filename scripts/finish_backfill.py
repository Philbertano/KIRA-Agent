"""Finish a partially-complete backfill (streaming).

Walks the existing S3 corpus law-by-law, generates embeddings, upserts
to S3 Vectors in small chunks, and writes the top-level _manifest.json
at the end.

Streaming design:
  - One law at a time → read its _meta.json + per-paragraph files.
  - Accumulate up to EMBED_BATCH embed inputs, flush via Cohere, upsert.
  - Visible per-batch progress; no 200k-tuple in-memory list.
  - boto3 connection pool sized for 32 concurrent sockets.

Usage:
    LEGAL_CORPUS_BUCKET=kira-legal-corpus-...-eu-central-1 \\
      python scripts/finish_backfill.py \\
        --vector-index kira-legal-norms \\
        --embed-batch 96
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date

import boto3
from botocore.config import Config

from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.region import REQUIRED_REGION
from kira.legal_sources._common.vector_index import VectorIndex, VectorRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("finish")


_BOTO_CONFIG = Config(
    max_pool_connections=32,
    retries={"max_attempts": 5, "mode": "adaptive"},
)


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

    log.info("Listing _meta.json files under gesetze/")
    meta_keys = _list_meta_keys(s3, bucket)
    log.info("Found %d Gesetze with _meta.json", len(meta_keys))

    manifest_entries: dict[str, dict] = {}
    embed_buffer: list[tuple[str, str, dict]] = []  # (key, text, metadata)
    paragraphs_done = 0
    laws_done = 0
    t0 = time.time()

    for meta_key in meta_keys:
        try:
            slug, m_entry, inputs_md = _load_one_law(s3, bucket, meta_key)
        except Exception as exc:
            log.warning("Skip %s: %s", meta_key, exc)
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
            remaining = (len(meta_keys) - laws_done) / rate if rate else 0.0
            log.info(
                "  %d/%d laws | %d paragraphs embedded | %.1f laws/s | ETA %.0fs",
                laws_done, len(meta_keys), paragraphs_done, rate, remaining,
            )

    # Final flush
    if embed_buffer:
        _flush(embed_buffer, embedder, vector_index)
        paragraphs_done += len(embed_buffer)

    log.info("Writing _manifest.json (%d laws)", len(manifest_entries))
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
        "duration_seconds": round(time.time() - t0, 1),
    }
    log.info("DONE %s", summary)
    print(json.dumps(summary, indent=2))
    return 0


def _flush(
    buf: list[tuple[str, str, dict]],
    embedder: CohereMultilingualEmbedder,
    vector_index: VectorIndex,
) -> None:
    texts = [t for _, t, _ in buf]
    keys = [k for k, _, _ in buf]
    mds = [m for _, _, m in buf]
    try:
        vectors = embedder.embed_documents(texts)
    except Exception as exc:
        log.error("Embed batch failed (%d items): %s", len(buf), exc)
        return
    records = [
        VectorRecord(key=k, vector=v, metadata=m)
        for k, v, m in zip(keys, vectors, mds, strict=True)
    ]
    vector_index.upsert(records)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--vector-index", default="kira-norms")
    p.add_argument("--vector-bucket", default="kira-legal-norms")
    p.add_argument("--embed-batch", type=int, default=96)
    return p.parse_args()


def _truncate_to_bytes(s: str, max_bytes: int) -> str:
    """Truncate a UTF-8 string to fit within max_bytes (whole-character safe)."""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    # Trim and append a marker. Decode with errors="ignore" to drop any
    # partial multi-byte sequence at the boundary.
    trimmed = encoded[: max_bytes - len(" […gekürzt…]".encode())]
    return trimmed.decode("utf-8", errors="ignore") + " […gekürzt…]"


def _list_meta_keys(s3, bucket: str) -> list[str]:
    out: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="gesetze/"):
        for obj in page.get("Contents") or []:
            if obj["Key"].endswith("/_meta.json"):
                out.append(obj["Key"])
    return out


def _load_one_law(s3, bucket: str, meta_key: str):
    meta_resp = s3.get_object(Bucket=bucket, Key=meta_key)
    meta = json.loads(meta_resp["Body"].read())
    abk = meta["abkuerzung"]
    abk_slug = meta_key.split("/")[1]
    titel = meta["titel"]
    type_str = meta["type"]
    stand = meta["stand"]
    meta_md = meta_resp.get("Metadata", {}) or {}

    m_entry = {
        "abkuerzung": abk,
        "titel": titel,
        "type": type_str,
        "meta_key": meta_key,
        "upstream_etag": meta_md.get("upstream_etag", ""),
        "upstream_last_modified": meta_md.get("upstream_last_modified", ""),
    }

    inputs_md: list[tuple[str, str, dict]] = []
    for paragraph, idx_entry in meta["paragraphen"].items():
        try:
            p_resp = s3.get_object(Bucket=bucket, Key=idx_entry["key"])
            p_payload = json.loads(p_resp["Body"].read())
        except Exception:
            continue
        wortlaut = "\n\n".join(
            f"({a['nummer']}) {a['text']}" for a in p_payload.get("absaetze", [])
        )
        vec_key = f"{abk_slug}-{paragraph}"
        embed_text = f"{abk} §{paragraph} ({p_payload.get('titel','')}):\n\n{wortlaut}"
        # S3 Vectors caps total metadata at 40 KB per vector.
        # Truncate wortlaut to fit (32 KB leaves margin for other fields).
        truncated_wortlaut = _truncate_to_bytes(wortlaut, 32000)
        md = {
            "gesetz": abk,
            "paragraph": paragraph,
            "abkuerzung": abk,
            "type": type_str,
            "titel": p_payload.get("titel", ""),
            "wortlaut": truncated_wortlaut,
            "quelle_url": p_payload.get("quelle_url", ""),
            "stand": stand,
            "content_sha256": idx_entry.get("content_sha256", ""),
        }
        inputs_md.append((vec_key, embed_text, md))
    return abk_slug, m_entry, inputs_md


if __name__ == "__main__":
    sys.exit(main())
