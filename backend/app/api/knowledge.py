"""Wissensbasis über die Oberfläche pflegen.

Uploads sind der einzige Weg, auf dem fremde Daten ins System kommen – deshalb
liegt hier die Prüfung: Größe, Endung und ein Blick in den tatsächlichen Inhalt,
nicht nur auf den Dateinamen.
"""

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import settings
from app.db.models import Chunk, Document, IndexOutbox, OutboxStatus, SourceType
from app.deps import CurrentUser, SessionDep
from app.rag import indexer, ingest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["wissensbasis"])

ALLOWED_SUFFIXES = {".md", ".txt", ".pdf", ".docx"}

# Ein Blick auf die ersten Bytes: Eine umbenannte .exe soll nicht als .pdf
# durchgehen. Für Text gibt es keine Signatur – der wird beim Parsen geprüft.
MAGIC = {
    ".pdf": b"%PDF-",
    ".docx": b"PK\x03\x04",
}


class DocumentOut(BaseModel):
    id: int
    title: str
    source_type: str
    filename: str | None
    chunks: int
    created_at: datetime
    indexed_at: datetime | None


class FaqIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)
    answer: str = Field(min_length=3, max_length=20000)


class IndexStatus(BaseModel):
    documents: int
    chunks: int
    vectors: int
    pending: int
    failed: int
    in_sync: bool


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(session: SessionDep, _: CurrentUser) -> list[DocumentOut]:
    rows = (
        await session.execute(
            select(Document, func.count(Chunk.id))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .group_by(Document.id)
            .order_by(Document.created_at.desc())
        )
    ).all()
    return [
        DocumentOut(
            id=document.id,
            title=document.title,
            source_type=document.source_type.value,
            filename=document.filename,
            chunks=count,
            created_at=document.created_at,
            indexed_at=document.indexed_at,
        )
        for document, count in rows
    ]


@router.post("/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload(session: SessionDep, _: CurrentUser, file: UploadFile = File(...)) -> DocumentOut:
    name = Path(file.filename or "").name  # kein Pfad, keine Traversierung
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Nicht unterstützt: {suffix or 'ohne Endung'}. "
            f"Erlaubt sind {', '.join(sorted(ALLOWED_SUFFIXES))}.",
        )

    payload = await file.read(settings.max_upload_bytes + 1)
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Die Datei ist größer als {settings.max_upload_bytes // 1024 // 1024} MB.",
        )
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Die Datei ist leer.")

    expected = MAGIC.get(suffix)
    if expected and not payload.startswith(expected):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Der Inhalt passt nicht zur Endung {suffix}.",
        )

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / name
    target.write_bytes(payload)

    try:
        text = ingest.extract_text(target)
    except Exception as exc:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Die Datei konnte nicht gelesen werden: {exc}"
        ) from exc

    if not text.strip():
        target.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kein lesbarer Text gefunden.")

    result = await ingest.ingest_text(
        session,
        text=text,
        title=ingest.derive_title(text, Path(name).stem),
        source_type=SourceType.UPLOAD,
        filename=name,
        mime_type=file.content_type,
    )
    await session.commit()

    if result.skipped:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Dieser Inhalt ist bereits in der Wissensbasis ({result.reason}).",
        )

    await indexer.drain_outbox(session)
    await ingest.mark_indexed(session, [result.document_id] if result.document_id else [])

    document = await session.get(Document, result.document_id)
    return DocumentOut(
        id=document.id,
        title=document.title,
        source_type=document.source_type.value,
        filename=document.filename,
        chunks=result.chunks,
        created_at=document.created_at,
        indexed_at=document.indexed_at,
    )


@router.post("/faq", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_faq(payload: FaqIn, session: SessionDep, _: CurrentUser) -> DocumentOut:
    # Frage und Antwort zusammen: Beim Suchen trifft die Frage, gebraucht wird
    # die Antwort – getrennt indiziert fände man das eine ohne das andere.
    text = f"# {payload.question.strip()}\n\n{payload.answer.strip()}\n"
    result = await ingest.ingest_text(
        session,
        text=text,
        title=payload.question.strip(),
        source_type=SourceType.FAQ,
    )
    await session.commit()

    if result.skipped:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Dieser Eintrag existiert bereits ({result.reason})."
        )

    await indexer.drain_outbox(session)
    await ingest.mark_indexed(session, [result.document_id] if result.document_id else [])

    document = await session.get(Document, result.document_id)
    return DocumentOut(
        id=document.id,
        title=document.title,
        source_type=document.source_type.value,
        filename=None,
        chunks=result.chunks,
        created_at=document.created_at,
        indexed_at=document.indexed_at,
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: int, session: SessionDep, _: CurrentUser) -> None:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dokument nicht gefunden.")

    filename = document.filename
    if not await ingest.delete_document(session, document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dokument nicht gefunden.")

    await indexer.drain_outbox(session)

    if filename:
        (Path(settings.upload_dir) / Path(filename).name).unlink(missing_ok=True)


@router.get("/status", response_model=IndexStatus)
async def index_status(session: SessionDep, _: CurrentUser) -> IndexStatus:
    from app.rag.vectorstore import VectorStore

    documents = (await session.execute(select(func.count()).select_from(Document))).scalar_one()
    chunks = (await session.execute(select(func.count()).select_from(Chunk))).scalar_one()
    pending = (
        await session.execute(
            select(func.count())
            .select_from(IndexOutbox)
            .where(IndexOutbox.status == OutboxStatus.PENDING)
        )
    ).scalar_one()
    failed = (
        await session.execute(
            select(func.count())
            .select_from(IndexOutbox)
            .where(IndexOutbox.status == OutboxStatus.FAILED)
        )
    ).scalar_one()

    store = VectorStore()
    try:
        vectors = await store.count()
    except Exception:  # noqa: BLE001
        vectors = -1
    finally:
        await store.close()

    return IndexStatus(
        documents=documents,
        chunks=chunks,
        vectors=vectors,
        pending=pending,
        failed=failed,
        in_sync=vectors == chunks and pending == 0 and failed == 0,
    )


@router.post("/reindex", response_model=IndexStatus)
async def reindex(session: SessionDep, user: CurrentUser) -> IndexStatus:
    """Gleicht Qdrant gegen Postgres ab – die Oberflächen-Variante von
    `repair-index`."""
    report = await indexer.repair(session)
    logger.info(
        "Neuindizierung durch %s: %d nachgetragen, %d entfernt",
        user.email,
        report.indexed,
        report.removed,
    )
    return await index_status(session, user)
