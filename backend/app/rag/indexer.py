"""Outbox → Qdrant.

Der einzige Weg, auf dem Vektoren nach Qdrant gelangen. Kein anderer Codepfad
schreibt daran vorbei – nur so bleibt der Abgleich in `repair` aussagekräftig.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document, IndexOutbox, OutboxOp, OutboxStatus
from app.rag.embeddings import EmbeddingError, get_embeddings
from app.rag.vectorstore import VectorStore, make_point

logger = logging.getLogger(__name__)

BATCH = 64
MAX_ATTEMPTS = 5


@dataclass(slots=True)
class DrainResult:
    upserted: int = 0
    deleted: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return f"{self.upserted} indiziert, {self.deleted} gelöscht, {self.failed} fehlgeschlagen"


async def drain_outbox(session: AsyncSession, store: VectorStore | None = None) -> DrainResult:
    """Arbeitet offene Aufträge ab. Idempotent: mehrfaches Ausführen schadet nicht."""
    owns_store = store is None
    store = store or VectorStore()
    result = DrainResult()

    # Cursor statt reiner Statusabfrage: Ein gescheiterter Auftrag bleibt
    # `pending` und würde sonst im selben Durchlauf sofort wieder gezogen – das
    # verbrennt das Wiederholungsbudget in Sekunden, statt es über die Zeit zu
    # verteilen. So bekommt jeder Auftrag je Durchlauf genau einen Versuch.
    last_id = 0
    try:
        while True:
            pending = (
                (
                    await session.execute(
                        select(IndexOutbox)
                        .where(
                            IndexOutbox.status == OutboxStatus.PENDING,
                            IndexOutbox.attempts < MAX_ATTEMPTS,
                            IndexOutbox.id > last_id,
                        )
                        .order_by(IndexOutbox.id)
                        .limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not pending:
                break
            last_id = max(entry.id for entry in pending)

            deletes = [e for e in pending if e.op is OutboxOp.DELETE]
            upserts = [e for e in pending if e.op is OutboxOp.UPSERT]

            if deletes:
                try:
                    await store.delete([e.chunk_id for e in deletes])
                    _mark_done(deletes)
                    result.deleted += len(deletes)
                except Exception as exc:  # noqa: BLE001
                    _mark_failed(deletes, str(exc))
                    result.failed += len(deletes)

            if upserts:
                result.failed += await _process_upserts(session, store, upserts, result)

            await session.commit()
    finally:
        if owns_store:
            await store.close()

    if result.failed:
        logger.warning("Outbox abgearbeitet: %s", result)
    else:
        logger.info("Outbox abgearbeitet: %s", result)
    return result


async def _process_upserts(
    session: AsyncSession,
    store: VectorStore,
    entries: list[IndexOutbox],
    result: DrainResult,
) -> int:
    chunk_ids = [e.chunk_id for e in entries]
    rows = (
        await session.execute(
            select(Chunk, Document.title, Document.source_type)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id.in_(chunk_ids))
        )
    ).all()
    by_id = {row[0].id: row for row in rows}

    # Chunk zwischenzeitlich gelöscht: der Auftrag ist gegenstandslos.
    obsolete = [e for e in entries if e.chunk_id not in by_id]
    _mark_done(obsolete)

    live = [e for e in entries if e.chunk_id in by_id]
    if not live:
        return 0

    texts = [by_id[e.chunk_id][0].content for e in live]
    try:
        vectors = await get_embeddings().embed_documents(texts)
    except EmbeddingError as exc:
        _mark_failed(live, str(exc))
        return len(live)

    points = []
    for entry, vector in zip(live, vectors, strict=True):
        chunk, title, source_type = by_id[entry.chunk_id]
        points.append(
            make_point(
                chunk.id,
                vector,
                content=chunk.content,
                title=title,
                source_type=source_type.value,
                document_id=chunk.document_id,
                chunk_ord=chunk.ord,
            )
        )

    try:
        await store.upsert(points)
    except Exception as exc:  # noqa: BLE001
        _mark_failed(live, str(exc))
        return len(live)

    _mark_done(live)
    result.upserted += len(live)
    return 0


def _mark_done(entries: list[IndexOutbox]) -> None:
    now = datetime.now(UTC)
    for entry in entries:
        entry.status = OutboxStatus.DONE
        entry.processed_at = now
        entry.last_error = None


def _mark_failed(entries: list[IndexOutbox], error: str) -> None:
    for entry in entries:
        entry.attempts += 1
        entry.last_error = error[:2000]
        if entry.attempts >= MAX_ATTEMPTS:
            entry.status = OutboxStatus.FAILED
            logger.error("Indexauftrag %s endgültig aufgegeben: %s", entry.id, error)


@dataclass(slots=True)
class RepairReport:
    """Zustand vor und nach der Reparatur.

    Der Stand *danach* entscheidet – ein Bericht, der die Ausgangslage als
    Ergebnis meldet, wäre irreführend.
    """

    in_postgres: int
    missing_before: int
    orphaned_before: int
    indexed: int
    removed: int
    in_qdrant_after: int
    missing_after: int
    orphaned_after: int

    @property
    def in_sync(self) -> bool:
        return self.missing_after == 0 and self.orphaned_after == 0


async def _diff(session: AsyncSession, store: VectorStore) -> tuple[set, set, set]:
    expected = set((await session.execute(select(Chunk.id))).scalars().all())
    actual = await store.all_point_ids()
    return expected, expected - actual, actual - expected


async def repair(session: AsyncSession) -> RepairReport:
    """Gleicht Postgres gegen Qdrant ab und behebt die Abweichung.

    Fehlende Vektoren werden neu eingereiht, verwaiste Punkte entfernt. Danach
    wird erneut gemessen, damit der Bericht den tatsächlichen Endstand zeigt.
    """
    store = VectorStore()
    try:
        await store.ensure_collection()
        expected, missing, orphaned = await _diff(session, store)

        for chunk_id in missing:
            document_id = (
                await session.execute(select(Chunk.document_id).where(Chunk.id == chunk_id))
            ).scalar_one_or_none()
            session.add(IndexOutbox(chunk_id=chunk_id, document_id=document_id, op=OutboxOp.UPSERT))
        await session.commit()

        # Erst die offenen Aufträge abarbeiten (darunter auch Löschungen, die
        # beim Ausfall liegen geblieben sind), dann erst die verwaisten Punkte
        # bestimmen – sonst löscht man Punkte, die gleich neu geschrieben werden.
        drained = await drain_outbox(session, store)

        _, _, orphaned_now = await _diff(session, store)
        if orphaned_now:
            await store.delete(list(orphaned_now))

        expected_after, missing_after, orphaned_after = await _diff(session, store)

        return RepairReport(
            in_postgres=len(expected),
            missing_before=len(missing),
            orphaned_before=len(orphaned),
            indexed=drained.upserted,
            removed=len(orphaned_now),
            in_qdrant_after=len(expected_after) - len(missing_after) + len(orphaned_after),
            missing_after=len(missing_after),
            orphaned_after=len(orphaned_after),
        )
    finally:
        await store.close()
