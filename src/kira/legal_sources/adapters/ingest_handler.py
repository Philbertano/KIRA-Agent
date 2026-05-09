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
import botocore.exceptions
import httpx

from kira.knowledge.ingest import GESETZE, GesetzKonfiguration

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
                [f"§§ {cfg.paragraph_range[0]}–{cfg.paragraph_range[1]}"]  # noqa: RUF001
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
    except botocore.exceptions.ClientError:
        return None
    return (head.get("Metadata") or {}).get("content-sha256")


def _write_manifest(s3, bucket: str) -> None:
    objs = s3.list_objects_v2(Bucket=bucket, Prefix="gesetze/")
    files = sorted(
        o["Key"]
        for o in objs.get("Contents", [])
        if o["Key"].endswith(".json") and not o["Key"].endswith("_manifest.json")
    )
    body = json.dumps({"version": 1, "files": files}, sort_keys=True).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key="gesetze/_manifest.json",
        Body=body,
        ContentType="application/json",
    )
