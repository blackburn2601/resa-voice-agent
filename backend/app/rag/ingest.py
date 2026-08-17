"""Aufnahme von Wissen: Datei lesen, zerlegen, einbetten, ablegen.

Der Ablauf schreibt Dokument, Chunks *und* die Indexaufträge in einer
Transaktion. Fällt Qdrant aus, ist der Auftrag trotzdem festgehalten und wird
später nachgeholt – siehe `indexer.py`.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document, IndexOutbox, OutboxOp, SourceType

logger = logging.getLogger(__name__)

# bge-m3 verarbeitet 8192 Token; die Grenze hier richtet sich also allein nach
# der Abrufqualität. Zu große Abschnitte verwässern den Vektor, zu kleine
# verlieren den Zusammenhang.
MAX_CHARS = 1800
OVERLAP_CHARS = 200


@dataclass(slots=True)
class IngestResult:
    document_id: int | None
    title: str
    chunks: int
    skipped: bool
    reason: str = ""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Grobe Schätzung. Für Anzeige und Grenzwerte ausreichend – wir brauchen
    hier keinen echten Tokenizer."""
    return max(1, len(text) // 4)


def split_markdown(text: str) -> list[str]:
    """Zerlegt entlang der Überschriften und teilt zu lange Abschnitte weiter.

    Ein Abschnitt unter einer Überschrift ist inhaltlich meist genau eine Antwort
    – das ist die natürliche Einheit für den Abruf.
    """
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    sections: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if re.match(r"^#{1,3} ", line) and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())

    chunks: list[str] = []
    for section in sections:
        if not section:
            continue
        if len(section) <= MAX_CHARS:
            chunks.append(section)
            continue
        # Zu langer Abschnitt: an Absatzgrenzen weiter zerlegen, mit Überlappung,
        # damit ein Satz an der Nahtstelle nicht verloren geht.
        start = 0
        while start < len(section):
            end = min(start + MAX_CHARS, len(section))
            if end < len(section):
                cut = section.rfind("\n\n", start, end)
                if cut > start + MAX_CHARS // 2:
                    end = cut
            piece = section[start:end].strip()
            if piece:
                chunks.append(piece)
            if end >= len(section):
                break
            start = max(start, end - OVERLAP_CHARS)
    return chunks


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if suffix == ".docx":
        import docx

        document = docx.Document(str(path))
        return "\n\n".join(p.text for p in document.paragraphs)
    raise ValueError(f"Nicht unterstütztes Format: {suffix}")


def derive_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if stripped:
            return stripped[:200]
    return fallback


async def ingest_text(
    session: AsyncSession,
    *,
    text: str,
    title: str,
    source_type: SourceType,
    filename: str | None = None,
    mime_type: str | None = None,
    replace_existing: bool = True,
) -> IngestResult:
    """Legt ein Dokument samt Chunks und Indexaufträgen an.

    Kein Commit – das übernimmt der Aufrufer, damit mehrere Dokumente in einem
    Rutsch verarbeitet werden können.
    """
    digest = content_hash(text)

    existing = (
        (await session.execute(select(Document).where(Document.content_hash == digest)))
        .scalars()
        .first()
    )
    if existing is not None:
        return IngestResult(existing.id, existing.title, 0, skipped=True, reason="inhaltsgleich")

    if replace_existing and filename:
        # Gleicher Dateiname, anderer Inhalt: die alte Fassung weicht, sonst
        # stehen zwei Wahrheiten in der Wissensbasis.
        stale = (
            (await session.execute(select(Document).where(Document.filename == filename)))
            .scalars()
            .all()
        )
        for document in stale:
            await _enqueue_deletions(session, document)
            await session.delete(document)

    pieces = split_markdown(text)
    if not pieces:
        return IngestResult(None, title, 0, skipped=True, reason="kein Inhalt")

    document = Document(
        title=title,
        source_type=source_type,
        filename=filename,
        mime_type=mime_type,
        content_hash=digest,
    )
    session.add(document)
    await session.flush()

    for ord_, piece in enumerate(pieces):
        chunk = Chunk(
            document_id=document.id,
            ord=ord_,
            content=piece,
            token_count=estimate_tokens(piece),
        )
        session.add(chunk)
        await session.flush()
        session.add(IndexOutbox(chunk_id=chunk.id, document_id=document.id, op=OutboxOp.UPSERT))

    return IngestResult(document.id, title, len(pieces), skipped=False)


async def _enqueue_deletions(session: AsyncSession, document: Document) -> None:
    chunk_ids = (
        (await session.execute(select(Chunk.id).where(Chunk.document_id == document.id)))
        .scalars()
        .all()
    )
    for chunk_id in chunk_ids:
        session.add(IndexOutbox(chunk_id=chunk_id, document_id=document.id, op=OutboxOp.DELETE))


async def delete_document(session: AsyncSession, document_id: int) -> bool:
    document = await session.get(Document, document_id)
    if document is None:
        return False
    await _enqueue_deletions(session, document)
    await session.delete(document)
    await session.commit()
    return True


async def ingest_directory(session: AsyncSession, directory: Path) -> list[IngestResult]:
    results: list[IngestResult] = []
    if not directory.exists():
        logger.warning("Wissensverzeichnis %s existiert nicht", directory)
        return results

    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".pdf", ".docx"}:
            continue
        try:
            text = extract_text(path)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s konnte nicht gelesen werden: %s", path.name, exc)
            continue

        result = await ingest_text(
            session,
            text=text,
            title=derive_title(text, path.stem),
            source_type=SourceType.SEED,
            filename=path.name,
        )
        results.append(result)

    await session.commit()
    return results


async def mark_indexed(session: AsyncSession, document_ids: list[int]) -> None:
    if not document_ids:
        return
    now = datetime.now(UTC)
    documents = (
        (await session.execute(select(Document).where(Document.id.in_(document_ids))))
        .scalars()
        .all()
    )
    for document in documents:
        document.indexed_at = now
    await session.commit()


async def purge_all(session: AsyncSession) -> int:
    """Alles löschen – inklusive Indexaufträgen. Für `reindex --fresh`."""
    documents = (await session.execute(select(Document))).scalars().all()
    for document in documents:
        await _enqueue_deletions(session, document)
    count = len(documents)
    await session.execute(delete(Chunk))
    await session.execute(delete(Document))
    await session.commit()
    return count
