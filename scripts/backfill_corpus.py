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
                except Exception as exc:
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

    for paragraph, norm in parsed.normen.items():
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
