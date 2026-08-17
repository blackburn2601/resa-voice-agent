"""Gespräche, Nachrichtenverlauf und die strukturierte Fallerfassung."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Urgency(StrEnum):
    NOTFALL = "notfall"
    DRINGEND = "dringend"
    NORMAL = "normal"


class Category(StrEnum):
    TERMINANFRAGE = "Terminanfrage"
    SCHADENSMELDUNG = "Schadensmeldung"
    ALLGEMEINE_FRAGE = "Allgemeine Frage"
    SONSTIGES = "Sonstiges"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nach außen sichtbare Kennung – die laufende id bleibt intern.
    public_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False, index=True
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(
            ConversationStatus,
            name="conversation_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ConversationStatus.ACTIVE,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(64))

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.seq"
    )
    intake: Mapped["IntakeRecord | None"] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )

    # Postgres liest den Index auch rückwärts, DESC ist hier nicht nötig.
    __table_args__ = (Index("ix_conversations_started_at", "started_at"),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Fortlaufend je Gespräch – die Reihenfolge darf nicht von der ID abhängen.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    # Anzeigetext für das Transkript. Leer bei Turns, die nur aus Werkzeug-
    # aufrufen oder -ergebnissen bestehen.
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Die exakten Content-Blocks, wie sie an die API zurückgehen. Ohne sie
    # verlöre der Verlauf die tool_use/tool_result-Paare, und das Modell wüsste
    # in einem späteren Turn nicht mehr, dass es bereits alarmiert hat.
    blocks: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_seq"),
    )


class IntakeRecord(Base):
    """Was Resa strukturiert aus dem Gespräch herausgeholt hat."""

    __tablename__ = "intake_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    caller_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[Category] = mapped_column(
        Enum(Category, name="intake_category", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Für eine Schadensmeldung tragend, sonst optional.
    property_address: Mapped[str | None] = mapped_column(String(300))
    urgency: Mapped[Urgency] = mapped_column(
        Enum(Urgency, name="intake_urgency", values_callable=lambda e: [m.value for m in e]),
        default=Urgency.NORMAL,
        nullable=False,
    )
    occurred_since: Mapped[str | None] = mapped_column(String(120))
    danger_to_life: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    callback_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preferred_callback_time: Mapped[str | None] = mapped_column(String(120))
    caller_phone: Mapped[str | None] = mapped_column(String(64))

    # Wurde der Datensatz nach einem Gesprächsabbruch nachgetragen?
    incomplete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="intake")
