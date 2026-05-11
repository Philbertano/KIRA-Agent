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
            except Exception as exc:
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
    embed_inputs: list[str] = []
    embed_keys: list[str] = []
    deleted_keys: list[str] = []

    old_meta = _read_old_meta(s3, bucket, abk_slug)
    old_para_shas = {
        p: e.get("content_sha256", "")
        for p, e in (old_meta.get("paragraphen") or {}).items()
    }

    for paragraph, norm in parsed.normen.items():
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
