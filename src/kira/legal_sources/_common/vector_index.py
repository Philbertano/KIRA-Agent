"""S3 Vectors wrapper for the kira-legal-norms index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from kira.legal_sources._common.errors import CorpusUnavailableError

DEFAULT_UPSERT_BATCH_SIZE = 100
DEFAULT_DELETE_BATCH_SIZE = 100


@dataclass(frozen=True)
class VectorRecord:
    key: str
    vector: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VectorSearchHit:
    key: str
    score: float
    metadata: dict[str, Any]


class VectorIndex:
    """Thin wrapper over `boto3.client('s3vectors')`.

    Translates from cosine *distance* (what S3 Vectors returns) to cosine
    *similarity* score in `[0, 1]` (what callers want), via `score = 1 - dist`.
    """

    def __init__(
        self,
        *,
        s3vectors_client: Any,
        index_name: str,
        vector_bucket_name: str | None = None,
        upsert_batch_size: int = DEFAULT_UPSERT_BATCH_SIZE,
        delete_batch_size: int = DEFAULT_DELETE_BATCH_SIZE,
    ) -> None:
        self._client = s3vectors_client
        self._index_name = index_name
        # S3 Vectors requires (vectorBucketName, indexName) on every call.
        # Default the bucket name to the index name since in our deploy they
        # share the same value (one vector bucket per index).
        self._vector_bucket_name = vector_bucket_name or index_name
        self._upsert_batch = upsert_batch_size
        self._delete_batch = delete_batch_size

    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        for start in range(0, len(records), self._upsert_batch):
            batch = records[start : start + self._upsert_batch]
            self._client.put_vectors(
                vectorBucketName=self._vector_bucket_name,
                indexName=self._index_name,
                vectors=[
                    {
                        "key": r.key,
                        "data": {"float32": r.vector},
                        "metadata": r.metadata,
                    }
                    for r in batch
                ],
            )

    def delete(self, keys: list[str]) -> None:
        if not keys:
            return
        for start in range(0, len(keys), self._delete_batch):
            batch = keys[start : start + self._delete_batch]
            self._client.delete_vectors(
                vectorBucketName=self._vector_bucket_name,
                indexName=self._index_name,
                keys=batch,
            )

    def query(
        self,
        *,
        vector: list[float],
        k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchHit]:
        kwargs: dict[str, Any] = {
            "vectorBucketName": self._vector_bucket_name,
            "indexName": self._index_name,
            "queryVector": {"float32": vector},
            "topK": k,
            "returnMetadata": True,
            "returnDistance": True,
        }
        if metadata_filter is not None:
            kwargs["filter"] = metadata_filter
        try:
            response = self._client.query_vectors(**kwargs)
        except ClientError as exc:
            raise CorpusUnavailableError(
                f"S3 Vectors query failed on index {self._index_name!r}: {exc}"
            ) from exc
        return [
            VectorSearchHit(
                key=v["key"],
                score=1.0 - float(v.get("distance", 1.0)),
                metadata=v.get("metadata", {}),
            )
            for v in response.get("vectors", [])
        ]
