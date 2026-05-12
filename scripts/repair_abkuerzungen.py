"""Repair the corpus by replacing slug-derived abkuerzungen with the
canonical ones from gii-toc.xml's <description> field.

Background: V2 backfill mistakenly set `abkuerzung = slug.upper()` rather
than the canonical jurabk from the XML. This script reads gii-toc.xml,
builds a slug→canonical-abkuerzung map, and rewrites:

  1. Local /tmp/kira-corpus-local/gesetze/<slug>/_meta.json (in-place)
  2. Optionally per-§ JSONs' `gesetz` field (--rewrite-norms)
  3. Re-uploads to S3
  4. Regenerates _manifest.json + re-upserts vector metadata via Cohere

This is a one-time repair. Once the ingest code (which now reads
`parsed.abkuerzung`) runs daily, the corpus stays correct.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import boto3
import httpx
from botocore.config import Config

from kira.legal_sources._common.embedder import CohereMultilingualEmbedder
from kira.legal_sources._common.region import REQUIRED_REGION
from kira.legal_sources._common.toc import GII_TOC_URL, parse_toc, slug_for
from kira.legal_sources._common.vector_index import VectorIndex, VectorRecord

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("repair-abk")

_BOTO_CONFIG = Config(max_pool_connections=32, retries={"max_attempts": 5, "mode": "adaptive"})


def main() -> int:
    args = _parse_args()
    bucket = os.environ["LEGAL_CORPUS_BUCKET"]
    local_root = Path(args.local_dir)
    if not local_root.is_dir():
        log.error("Local corpus dir %s does not exist", local_root)
        return 2

    # 1) Build slug → abkuerzung map from gii-toc.xml descriptions.
    log.info("Fetching gii-toc.xml for slug→abkuerzung map")
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as c:
        resp = c.get(GII_TOC_URL)
        resp.raise_for_status()
    entries = parse_toc(resp.content)
    slug_to_abk: dict[str, str] = {}
    raw_root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(resp.content)
    # parse_toc strips <description> — re-walk the XML to pick up descriptions.
    for item in raw_root.iter("item"):
        link_el = item.find("link")
        desc_el = item.find("description")
        if link_el is None or desc_el is None:
            continue
        slug = slug_for((link_el.text or "").strip())
        desc = (desc_el.text or "").strip()
        if slug and desc:
            slug_to_abk[slug] = desc
    log.info("TOC: %d entries, %d slug→abk pairs", len(entries), len(slug_to_abk))

    # 2) Walk local _meta.json files, fix abkuerzung in place.
    meta_paths = sorted(local_root.glob("*/_meta.json"))
    log.info("Walking %d local _meta.json files", len(meta_paths))
    rewritten = 0
    for meta_path in meta_paths:
        slug = meta_path.parent.name
        canonical = slug_to_abk.get(slug)
        if not canonical:
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("abkuerzung") == canonical:
            continue
        meta["abkuerzung"] = canonical
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        rewritten += 1
        if args.rewrite_norms:
            for p_path in meta_path.parent.glob("*.json"):
                if p_path.name == "_meta.json":
                    continue
                try:
                    p_payload = json.loads(p_path.read_text(encoding="utf-8"))
                    if p_payload.get("gesetz") != canonical:
                        p_payload["gesetz"] = canonical
                        p_path.write_text(
                            json.dumps(p_payload, ensure_ascii=False, sort_keys=True),
                            encoding="utf-8",
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Skip per-§ %s: %s", p_path, exc)
    log.info("Rewrote %d _meta.json files", rewritten)

    if args.dry_run:
        log.warning("--dry-run set; NOT syncing back to S3 or re-upserting vectors")
        return 0

    # 3) Sync corrected files back to S3.
    log.info("Syncing local corpus back to S3")
    import subprocess
    r = subprocess.run(
        [
            "aws", "s3", "sync", str(local_root), f"s3://{bucket}/gesetze/",
            "--region", REQUIRED_REGION,
            "--delete",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.error("aws s3 sync failed: %s", r.stderr)
        return 3
    log.info("Sync done. Output:\n%s", r.stdout[-2000:])

    # 4) Rebuild manifest with corrected abkuerzungen.
    log.info("Rebuilding manifest from corrected _meta.json files")
    manifest_entries: dict[str, dict] = {}
    for meta_path in meta_paths:
        slug = meta_path.parent.name
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        manifest_entries[slug] = {
            "abkuerzung": meta["abkuerzung"],
            "titel": meta["titel"],
            "type": meta["type"],
            "meta_key": f"gesetze/{slug}/_meta.json",
            "upstream_etag": "",
            "upstream_last_modified": "",
        }
    payload = {
        "version": 2,
        "stand": date.today().isoformat(),
        "gesetze": manifest_entries,
    }
    s3 = boto3.client("s3", region_name=REQUIRED_REGION, config=_BOTO_CONFIG)
    s3.put_object(
        Bucket=bucket,
        Key="gesetze/_manifest.json",
        Body=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )
    log.info("Manifest written with %d entries", len(manifest_entries))

    # 5) Refresh vector metadata. Re-embed in batches (same paragraphs,
    #    new abkuerzung in metadata).
    if args.refresh_vectors:
        log.info("Refreshing vector metadata via re-embedding")
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

        buffer: list[tuple[str, str, dict]] = []
        embedded = 0
        laws_done = 0
        import time as _time
        t0 = _time.time()
        for meta_path in meta_paths:
            slug = meta_path.parent.name
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            abk = meta["abkuerzung"]
            type_str = meta["type"]
            stand = meta["stand"]
            for paragraph in meta["paragraphen"].keys():
                p_path = meta_path.parent / f"{paragraph}.json"
                if not p_path.exists():
                    continue
                p_payload = json.loads(p_path.read_text(encoding="utf-8"))
                wortlaut = "\n\n".join(
                    f"({a['nummer']}) {a['text']}" for a in p_payload.get("absaetze", [])
                )
                vec_key = f"{slug}-{paragraph}"
                embed_text = f"{abk} §{paragraph} ({p_payload.get('titel','')}):\n\n{wortlaut}"
                buffer.append((vec_key, embed_text, {
                    "gesetz": abk,
                    "paragraph": paragraph,
                    "abkuerzung": abk,
                    "type": type_str,
                    "titel": p_payload.get("titel", ""),
                    "wortlaut": _truncate_to_bytes(wortlaut, 32000),
                    "quelle_url": p_payload.get("quelle_url", ""),
                    "stand": stand,
                    "content_sha256": meta["paragraphen"][paragraph].get("content_sha256", ""),
                }))
                if len(buffer) >= args.embed_batch:
                    _flush(buffer, embedder, vector_index)
                    embedded += len(buffer)
                    buffer = []
            laws_done += 1
            if laws_done % 50 == 0:
                elapsed = _time.time() - t0
                rate = laws_done / elapsed if elapsed else 0
                eta = (len(meta_paths) - laws_done) / rate if rate else 0
                log.info(
                    "  %d/%d laws | %d embedded | %.1f laws/s | ETA %.0fs",
                    laws_done, len(meta_paths), embedded, rate, eta,
                )
        if buffer:
            _flush(buffer, embedder, vector_index)
            embedded += len(buffer)
        log.info("Vector refresh done: %d paragraphs re-upserted", embedded)

    log.info("DONE")
    return 0


def _flush(buf, embedder, vector_index):
    texts = [t for _, t, _ in buf]
    keys = [k for k, _, _ in buf]
    mds = [m for _, _, m in buf]
    try:
        vectors = embedder.embed_documents(texts)
    except Exception as exc:  # noqa: BLE001
        log.error("Embed batch failed (%d): %s", len(buf), exc)
        return
    try:
        records = [
            VectorRecord(key=k, vector=v, metadata=m)
            for k, v, m in zip(keys, vectors, mds, strict=True)
        ]
        vector_index.upsert(records)
    except Exception as exc:  # noqa: BLE001
        log.error("Upsert failed (%d): %s", len(buf), exc)


def _truncate_to_bytes(s: str, max_bytes: int) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    trimmed = encoded[: max_bytes - len(" […gekürzt…]".encode())]
    return trimmed.decode("utf-8", errors="ignore") + " […gekürzt…]"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--local-dir", default="/tmp/kira-corpus-local/gesetze")
    p.add_argument("--vector-index", default="kira-norms")
    p.add_argument("--vector-bucket", default="kira-legal-norms")
    p.add_argument("--embed-batch", type=int, default=96)
    p.add_argument("--rewrite-norms", action="store_true",
                   help="Also rewrite per-§ JSONs' `gesetz` field")
    p.add_argument("--refresh-vectors", action="store_true",
                   help="Re-embed all paragraphs and re-upsert vectors")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
