"""Der Gesprächskern.

Kennt weder Browser noch Sprachausgabe. Er bekommt eine Gesprächs-ID und einen
Text, lädt den Verlauf aus Postgres, ruft das Modell und schreibt zurück.

Damit ersetzt Postgres das frühere Prozess-Dictionary: Der Verlauf überlebt
Neustarts und funktioniert mit mehreren Workern.
"""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent import AgentEvent, NewMessage, TurnComplete, stream_turn
from app.core.prompts import GREETING
from app.core.tools import ToolContext
from app.db.models import (
    Conversation,
    ConversationStatus,
    Escalation,
    IntakeRecord,
    Message,
    MessageRole,
)

logger = logging.getLogger(__name__)


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Lebenszyklus ------------------------------------------------------

    async def start(self) -> Conversation:
        conversation = Conversation(status=ConversationStatus.ACTIVE)
        self.session.add(conversation)
        await self.session.flush()

        # Die Begrüßung ist fester Text, kein Modellaufruf: schneller, günstiger
        # und dadurch cachebar für die Sprachausgabe.
        self.session.add(
            Message(
                conversation_id=conversation.id,
                seq=0,
                role=MessageRole.ASSISTANT,
                content=GREETING,
                blocks=[{"type": "text", "text": GREETING}],
            )
        )
        await self.session.commit()
        return conversation

    async def get(self, public_id: str) -> Conversation | None:
        result = await self.session.execute(
            select(Conversation).where(Conversation.public_id == public_id)
        )
        return result.scalars().first()

    async def end(self, conversation_id: int, reason: str = "closed") -> None:
        conversation = await self.session.get(Conversation, conversation_id)
        if conversation is None or conversation.ended_at is not None:
            return

        has_intake = (
            await self.session.execute(
                select(func.count())
                .select_from(IntakeRecord)
                .where(IntakeRecord.conversation_id == conversation_id)
            )
        ).scalar_one() > 0

        conversation.ended_at = datetime.now(UTC)
        conversation.end_reason = reason
        conversation.status = (
            ConversationStatus.COMPLETED if has_intake else ConversationStatus.ABANDONED
        )

        if not has_intake:
            await self._handle_abandoned(conversation_id)

        await self.session.commit()

    async def _handle_abandoned(self, conversation_id: int) -> None:
        """Absicherung: Eskalation ohne Falldatensatz.

        Jemand meldet einen Wasserschaden und legt auf, bevor Resa Name und
        Adresse hat. Die Alarmierung ist raus – der Fall darf trotzdem nicht
        ohne Eintrag im Dashboard bleiben.
        """
        escalation = (
            (
                await self.session.execute(
                    select(Escalation).where(Escalation.conversation_id == conversation_id)
                )
            )
            .scalars()
            .first()
        )
        if escalation is None:
            return

        logger.warning(
            "Gespräch %s abgebrochen, aber alarmiert – trage unvollständigen Fall nach",
            conversation_id,
        )
        from app.db.models import Category, Urgency  # lokal: vermeidet Zirkelimport

        self.session.add(
            IntakeRecord(
                conversation_id=conversation_id,
                caller_name="unbekannt (Gespräch abgebrochen)",
                category=Category.SCHADENSMELDUNG,
                summary=escalation.summary,
                property_address=escalation.property_address,
                caller_phone=escalation.caller_phone,
                urgency=Urgency.NOTFALL,
                danger_to_life=False,
                incomplete=True,
            )
        )

    # -- Verlauf -----------------------------------------------------------

    async def _history(self, conversation_id: int) -> list[dict[str, Any]]:
        """Baut die API-Nachrichten aus den gespeicherten Blocks.

        Die erste Nachricht ist die Begrüßung von Resa. Die Messages-API
        verlangt, dass der Verlauf mit `user` beginnt – also wird sie
        übersprungen und stattdessen im Systemkontext mitgedacht.
        """
        result = await self.session.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.seq)
        )
        rows = list(result.scalars())

        messages: list[dict[str, Any]] = []
        for row in rows:
            if not messages and row.role is MessageRole.ASSISTANT:
                continue
            if not row.blocks:
                continue
            messages.append({"role": row.role.value, "content": row.blocks})
        return messages

    async def _next_seq(self, conversation_id: int) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(Message.seq), -1)).where(
                Message.conversation_id == conversation_id
            )
        )
        return result.scalar_one() + 1

    async def _persist(self, conversation_id: int, new_messages: list[NewMessage]) -> None:
        seq = await self._next_seq(conversation_id)
        for item in new_messages:
            self.session.add(
                Message(
                    conversation_id=conversation_id,
                    seq=seq,
                    role=MessageRole(item.role),
                    content=item.content,
                    blocks=item.blocks,
                )
            )
            seq += 1
        await self.session.commit()

    # -- Gesprächszug ------------------------------------------------------

    async def handle_turn(self, conversation_id: int, user_text: str) -> AsyncIterator[AgentEvent]:
        # Zuerst festschreiben, dann rechnen: Eine Notfall-Alarmierung feuert
        # mitten im Zug und schickt den Verlauf mit – ohne diese Reihenfolge
        # fehlten dort ausgerechnet die Worte der anrufenden Person.
        await self._persist(
            conversation_id,
            [
                NewMessage(
                    role="user",
                    content=user_text,
                    blocks=[{"type": "text", "text": user_text}],
                )
            ],
        )

        history = await self._history(conversation_id)
        ctx = ToolContext(session=self.session, conversation_id=conversation_id)

        async for event in stream_turn(history, ctx):
            if isinstance(event, TurnComplete):
                await self._persist(conversation_id, event.new_messages)
            yield event
