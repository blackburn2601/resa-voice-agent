"""Gesprächskern: Verlauf, Reihenfolge, Abbruchbehandlung."""

from sqlalchemy import select

from app.core.agent import NewMessage
from app.core.conversation import ConversationService
from app.core.prompts import GREETING
from app.db.models import (
    Conversation,
    ConversationStatus,
    Escalation,
    EscalationStatus,
    IntakeRecord,
    Message,
)


async def test_start_legt_gespraech_mit_begruessung_an(session):
    service = ConversationService(session)
    conversation = await service.start()

    assert conversation.public_id is not None
    assert conversation.status is ConversationStatus.ACTIVE

    messages = (
        (await session.execute(select(Message).where(Message.conversation_id == conversation.id)))
        .scalars()
        .all()
    )
    assert len(messages) == 1
    assert messages[0].seq == 0
    assert messages[0].content == GREETING


async def test_history_ueberspringt_fuehrende_begruessung(session):
    """Die Messages-API verlangt, dass der Verlauf mit `user` beginnt."""
    service = ConversationService(session)
    conversation = await service.start()

    history = await service._history(conversation.id)
    assert history == []

    await service._persist(
        conversation.id,
        [
            NewMessage(role="user", content="Hallo", blocks=[{"type": "text", "text": "Hallo"}]),
            NewMessage(
                role="assistant",
                content="Guten Tag",
                blocks=[{"type": "text", "text": "Guten Tag"}],
            ),
        ],
    )

    history = await service._history(conversation.id)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == [{"type": "text", "text": "Hallo"}]


async def test_persist_vergibt_fortlaufende_seq(session):
    service = ConversationService(session)
    conversation = await service.start()

    await service._persist(
        conversation.id,
        [
            NewMessage(role="user", content="a", blocks=[{"type": "text", "text": "a"}]),
            NewMessage(role="assistant", content="b", blocks=[{"type": "text", "text": "b"}]),
        ],
    )
    await service._persist(
        conversation.id,
        [NewMessage(role="user", content="c", blocks=[{"type": "text", "text": "c"}])],
    )

    seqs = (
        (
            await session.execute(
                select(Message.seq)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.seq)
            )
        )
        .scalars()
        .all()
    )
    assert seqs == [0, 1, 2, 3]


async def test_werkzeug_nachrichten_erscheinen_nicht_im_transkript(session):
    """tool_result-Turns haben keinen Anzeigetext, gehen aber an die API zurück."""
    service = ConversationService(session)
    conversation = await service.start()

    await service._persist(
        conversation.id,
        [
            NewMessage(
                role="user",
                content="Wann habt ihr auf?",
                blocks=[{"type": "text", "text": "Wann habt ihr auf?"}],
            ),
            NewMessage(
                role="assistant",
                content="",
                blocks=[
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "search_knowledge",
                        "input": {"query": "Bürozeiten"},
                    }
                ],
            ),
            NewMessage(
                role="user",
                content="",
                blocks=[
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "Mo-Do 8-16 Uhr"}
                ],
            ),
        ],
    )

    sichtbar = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id, Message.content != "")
                .order_by(Message.seq)
            )
        )
        .scalars()
        .all()
    )
    assert [m.content for m in sichtbar] == [GREETING, "Wann habt ihr auf?"]

    # Für das Modell bleiben die Werkzeug-Blocks dagegen erhalten.
    history = await service._history(conversation.id)
    assert history[1]["content"][0]["type"] == "tool_use"
    assert history[2]["content"][0]["type"] == "tool_result"


async def test_abbruch_ohne_falldatensatz_wird_als_abandoned_markiert(session):
    service = ConversationService(session)
    conversation = await service.start()

    await service.end(conversation.id, reason="disconnect")

    refreshed = await session.get(Conversation, conversation.id)
    assert refreshed.status is ConversationStatus.ABANDONED
    assert refreshed.ended_at is not None


async def test_abbruch_nach_alarmierung_traegt_fall_nach(session):
    """Wer nach 'im Keller steht Wasser' auflegt, darf nicht aus dem Dashboard fallen."""
    service = ConversationService(session)
    conversation = await service.start()

    session.add(
        Escalation(
            conversation_id=conversation.id,
            reason="Wasseraustritt im Keller",
            summary="Anrufer meldet Wasser im Keller, Heizungsraum",
            property_address="Musterstraße 1",
            status=EscalationStatus.DELIVERED,
        )
    )
    await session.flush()

    await service.end(conversation.id, reason="disconnect")

    record = (
        (
            await session.execute(
                select(IntakeRecord).where(IntakeRecord.conversation_id == conversation.id)
            )
        )
        .scalars()
        .first()
    )

    assert record is not None
    assert record.incomplete is True
    assert record.property_address == "Musterstraße 1"
    assert "Wasser" in record.summary
