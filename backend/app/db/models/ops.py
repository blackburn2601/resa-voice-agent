"""Betrieb: Benutzer, Bereitschaft, Eskalationen und der TTS-Cache."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserRole(StrEnum):
    ADMIN = "admin"
    VIEWER = "viewer"


class EscalationStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    PARTIAL = "partial"
    FAILED = "failed"


class EscalationChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


class AttemptStatus(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e]),
        default=UserRole.ADMIN,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OnCallContact(Base):
    """Wer bei einem Notfall alarmiert wird."""

    __tablename__ = "on_call_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Escalation(Base):
    """Eine Alarmierung je Gespräch – die Unique-Constraint macht sie idempotent."""

    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    property_address: Mapped[str | None] = mapped_column(String(300))
    caller_phone: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[EscalationStatus] = mapped_column(
        Enum(
            EscalationStatus,
            name="escalation_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=EscalationStatus.PENDING,
        nullable=False,
    )

    attempts: Mapped[list["EscalationAttempt"]] = relationship(
        back_populates="escalation", cascade="all, delete-orphan", order_by="EscalationAttempt.id"
    )


class EscalationAttempt(Base):
    """Ein Zustellversuch über einen Kanal – die Belegkette der Alarmierung."""

    __tablename__ = "escalation_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    escalation_id: Mapped[int] = mapped_column(
        ForeignKey("escalations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[EscalationChannel] = mapped_column(
        Enum(
            EscalationChannel,
            name="escalation_channel",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    status: Mapped[AttemptStatus] = mapped_column(
        Enum(AttemptStatus, name="attempt_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    detail: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    escalation: Mapped[Escalation] = relationship(back_populates="attempts")


class TtsCache(Base):
    """Fertiges Audio je Satz. Die Begrüßung ist damit ab dem zweiten Mal sofort da."""

    __tablename__ = "tts_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    voice_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    audio: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("uq_tts_cache_key", "text_hash", "voice_id", "model_id", unique=True),)
