"""Wissensbasis: Dokumente, Chunks und die Outbox nach Qdrant.

Postgres ist hier die Quelle der Wahrheit. Qdrant hält nur eine Ableitung –
die Vektoren. Damit beide nicht auseinanderlaufen, wird jede Indexänderung in
derselben Transaktion wie der Inhalt in die Outbox geschrieben.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SourceType(StrEnum):
    UPLOAD = "upload"
    FAQ = "faq"
    SEED = "seed"


class OutboxOp(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType, name="document_source_type", values_callable=lambda e: [m.value for m in e]
        ),
        nullable=False,
    )
    filename: Mapped[str | None] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(120))
    # Verhindert, dass erneutes Indizieren denselben Inhalt verdoppelt.
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Chunk.ord"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    # UUID statt Integer: die Qdrant-Punkt-ID wird deterministisch daraus
    # abgeleitet, damit Upserts idempotent sind.
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (UniqueConstraint("document_id", "ord", name="uq_chunks_document_ord"),)


class IndexOutbox(Base):
    """Ein Auftrag, Qdrant an den Stand von Postgres anzugleichen.

    Wird gemeinsam mit dem Inhalt committet. Fällt Qdrant aus, bleibt der
    Auftrag `pending` und wird vom Sweep nachgeholt.
    """

    __tablename__ = "index_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Kein Fremdschlüssel: bei op=delete ist der Chunk in Postgres schon weg.
    chunk_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[int | None] = mapped_column(Integer)
    op: Mapped[OutboxOp] = mapped_column(
        Enum(OutboxOp, name="outbox_op", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, name="outbox_status", values_callable=lambda e: [m.value for m in e]),
        default=OutboxStatus.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_index_outbox_status_created", "status", "created_at"),)
