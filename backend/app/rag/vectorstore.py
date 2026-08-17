"""Qdrant-Anbindung.

Qdrant hält nur eine Ableitung aus Postgres: den Vektor je Chunk plus die
Nutzdaten, die für die Antwort gebraucht werden. Die Punkt-ID *ist* die
Chunk-UUID aus Postgres – damit überschreibt jeder Wiederholversuch denselben
Punkt, statt einen zweiten anzulegen. Wiederholen ist deshalb gefahrlos.
"""

import logging
import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchHit:
    chunk_id: uuid.UUID
    content: str
    title: str
    source_type: str
    score: float


class VectorStore:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection: str | None = None,
        dim: int | None = None,
    ) -> None:
        self.collection = collection or settings.qdrant_collection
        self.dim = dim or settings.embedding_dim
        self._client = AsyncQdrantClient(
            url=url or settings.qdrant_url,
            api_key=api_key or settings.qdrant_api_key,
            timeout=30,
        )

    @property
    def client(self) -> AsyncQdrantClient:
        return self._client

    async def ensure_collection(self) -> bool:
        """Legt die Collection an, falls sie fehlt. Gibt True zurück, wenn neu."""
        if await self._client.collection_exists(self.collection):
            info = await self._client.get_collection(self.collection)
            existing = info.config.params.vectors.size  # type: ignore[union-attr]
            if existing != self.dim:
                raise RuntimeError(
                    f"Collection '{self.collection}' hat {existing} Dimensionen, "
                    f"konfiguriert sind {self.dim}. Die Vektorgröße ist nachträglich nur "
                    f"durch Neuanlegen und Neuindizieren änderbar."
                )
            return False

        await self._client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE),
        )
        # Filter nach Quelle sollen nicht über einen vollen Scan laufen.
        await self._client.create_payload_index(
            collection_name=self.collection,
            field_name="source_type",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        await self._client.create_payload_index(
            collection_name=self.collection,
            field_name="document_id",
            field_schema=models.PayloadSchemaType.INTEGER,
        )
        logger.info("Qdrant-Collection '%s' angelegt (%d Dim.)", self.collection, self.dim)
        return True

    async def upsert(self, points: list[models.PointStruct]) -> None:
        if not points:
            return
        await self._client.upsert(collection_name=self.collection, points=points, wait=True)

    async def delete(self, chunk_ids: list[uuid.UUID]) -> None:
        if not chunk_ids:
            return
        await self._client.delete(
            collection_name=self.collection,
            points_selector=models.PointIdsList(points=[str(cid) for cid in chunk_ids]),
            wait=True,
        )

    async def search(
        self,
        vector: list[float],
        limit: int = 4,
        score_threshold: float | None = 0.35,
        source_types: list[str] | None = None,
    ) -> list[SearchHit]:
        query_filter = None
        if source_types:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="source_type", match=models.MatchAny(any=source_types)
                    )
                ]
            )

        response = await self._client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=True,
        )

        hits: list[SearchHit] = []
        for point in response.points:
            payload = point.payload or {}
            hits.append(
                SearchHit(
                    chunk_id=uuid.UUID(str(point.id)),
                    content=payload.get("content", ""),
                    title=payload.get("title", ""),
                    source_type=payload.get("source_type", ""),
                    score=point.score,
                )
            )
        return hits

    async def all_point_ids(self) -> set[uuid.UUID]:
        """Für den Abgleich gegen Postgres in `repair-index`."""
        found: set[uuid.UUID] = set()
        offset = None
        while True:
            points, offset = await self._client.scroll(
                collection_name=self.collection,
                limit=1000,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            found.update(uuid.UUID(str(p.id)) for p in points)
            if offset is None:
                break
        return found

    async def count(self) -> int:
        result = await self._client.count(collection_name=self.collection, exact=True)
        return result.count

    async def health(self) -> tuple[bool, str]:
        try:
            exists = await self._client.collection_exists(self.collection)
            if not exists:
                return True, f"erreichbar, Collection '{self.collection}' noch nicht angelegt"
            return True, f"{await self.count()} Vektoren in '{self.collection}'"
        except Exception as exc:  # noqa: BLE001 – Health darf nie werfen
            return False, str(exc)

    async def close(self) -> None:
        await self._client.close()


def make_point(
    chunk_id: uuid.UUID,
    vector: list[float],
    *,
    content: str,
    title: str,
    source_type: str,
    document_id: int,
    chunk_ord: int,
) -> models.PointStruct:
    """`content` liegt bewusst doppelt (Postgres + Payload) – spart beim Suchen
    den Rückweg in die Datenbank."""
    return models.PointStruct(
        id=str(chunk_id),
        vector=vector,
        payload={
            "content": content,
            "title": title,
            "source_type": source_type,
            "document_id": document_id,
            "chunk_ord": chunk_ord,
        },
    )
