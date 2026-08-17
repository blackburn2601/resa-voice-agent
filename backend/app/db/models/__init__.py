"""Sammelimport, damit Alembic alle Tabellen in Base.metadata sieht."""

from app.db.models.conversation import (
    Category,
    Conversation,
    ConversationStatus,
    IntakeRecord,
    Message,
    MessageRole,
    Urgency,
)
from app.db.models.knowledge import (
    Chunk,
    Document,
    IndexOutbox,
    OutboxOp,
    OutboxStatus,
    SourceType,
)
from app.db.models.ops import (
    AttemptStatus,
    Escalation,
    EscalationAttempt,
    EscalationChannel,
    EscalationStatus,
    OnCallContact,
    TtsCache,
    User,
    UserRole,
)

__all__ = [
    "AttemptStatus",
    "Category",
    "Chunk",
    "Conversation",
    "ConversationStatus",
    "Document",
    "Escalation",
    "EscalationAttempt",
    "EscalationChannel",
    "EscalationStatus",
    "IndexOutbox",
    "IntakeRecord",
    "Message",
    "MessageRole",
    "OnCallContact",
    "OutboxOp",
    "OutboxStatus",
    "SourceType",
    "TtsCache",
    "Urgency",
    "User",
    "UserRole",
]
