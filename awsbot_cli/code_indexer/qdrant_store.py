from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)
from qdrant_client.models import PayloadSchemaType


@dataclass
class QdrantStore:
    host: str
    port: int
    collection: str
    vector_size: int

    def __post_init__(self) -> None:
        self.client = QdrantClient(host=self.host, port=self.port)
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=self.vector_size, distance=Distance.COSINE
                ),
            )
        # Helpful payload indexes for filtering (optional but improves performance)
        for key in [
            "workspace_id",
            "file_path",
            "language",
            "chunk_type",
            "symbol_name",
            "git_ref",
        ]:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=key,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                # Index may already exist; ignore.
                pass

    def delete_file(self, workspace_id: str, file_path: str, git_ref: str) -> None:
        filt = Filter(
            must=[
                FieldCondition(
                    key="workspace_id", match=MatchValue(value=workspace_id)
                ),
                FieldCondition(key="file_path", match=MatchValue(value=file_path)),
                FieldCondition(key="git_ref", match=MatchValue(value=git_ref)),
            ]
        )
        self.client.delete(
            collection_name=self.collection, points_selector=filt, wait=True
        )

    def upsert_chunks(self, points: list[PointStruct]) -> None:
        if not points:
            return
        self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def query(
        self, query_vector: list[float], limit: int, qfilter: Filter | None = None
    ) -> list[Dict[str, Any]]:
        res = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=qfilter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        # qdrant-client returns an object with `points` list
        out: list[Dict[str, Any]] = []
        for p in getattr(res, "points", []):
            payload = dict(getattr(p, "payload", {}) or {})
            out.append(
                {
                    "id": str(getattr(p, "id")),
                    "score": float(getattr(p, "score", 0.0)),
                    "payload": payload,
                }
            )
        return out
