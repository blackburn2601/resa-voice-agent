"""Notfall-Alarmierung: Idempotenz und wahrheitsgemäße Rückmeldung."""

import pytest
from sqlalchemy import func, select

from app.core.conversation import ConversationService
from app.db.models import (
    AttemptStatus,
    Escalation,
    EscalationAttempt,
    EscalationStatus,
    OnCallContact,
)
from app.escalation import channels
from app.escalation.channels import DeliveryResult, DeliveryStatus
from app.escalation.service import EscalationService


class _StubNotifier:
    def __init__(self, channel: str, status: DeliveryStatus) -> None:
        self.channel = channel
        self._status = status
        self.calls: list[str] = []

    async def send(self, *, to: str, subject: str, body: str) -> DeliveryResult:
        self.calls.append(body)
        return DeliveryResult(self.channel, self._status, f"stub/{self._status.value}")


@pytest.fixture
async def conversation(session):
    return await ConversationService(session).start()


@pytest.fixture
async def contact(session):
    entry = OnCallContact(name="Bereitschaft", email="notdienst@example.de", phone="+49301234567")
    session.add(entry)
    await session.flush()
    return entry


async def test_erfolgreiche_alarmierung_meldet_kanal_zurueck(session, conversation, contact):
    service = EscalationService(session)
    service.notifiers = [_StubNotifier("email", DeliveryStatus.DELIVERED)]

    outcome = await service.trigger(
        conversation_id=conversation.id,
        reason="Wasseraustritt im Keller",
        summary="Wasser läuft aus der Heizung",
        address="Musterstraße 1",
        danger_to_life=False,
    )

    assert outcome.any_delivered
    assert outcome.delivered_channels == ["email"]
    assert "informiert" in outcome.as_tool_result()

    row = (await session.execute(select(Escalation))).scalars().first()
    assert row.status is EscalationStatus.DELIVERED


async def test_gescheiterte_alarmierung_wird_nicht_beschoenigt(session, conversation, contact):
    """Der Text muss Resa davon abhalten, eine Alarmierung zu behaupten."""
    service = EscalationService(session)
    service.notifiers = [_StubNotifier("email", DeliveryStatus.FAILED)]

    outcome = await service.trigger(
        conversation_id=conversation.id,
        reason="Gasgeruch",
        summary="Anrufer riecht Gas im Treppenhaus",
        danger_to_life=True,
    )

    assert not outcome.any_delivered
    text = outcome.as_tool_result()
    assert "NICHT" in text
    assert "112" in text

    row = (await session.execute(select(Escalation))).scalars().first()
    assert row.status is EscalationStatus.FAILED


async def test_zweiter_aufruf_alarmiert_nicht_erneut(session, conversation, contact):
    notifier = _StubNotifier("email", DeliveryStatus.DELIVERED)
    service = EscalationService(session)
    service.notifiers = [notifier]

    first = await service.trigger(
        conversation_id=conversation.id, reason="Wasser", summary="Wasser im Keller"
    )
    second = await service.trigger(
        conversation_id=conversation.id, reason="Wasser", summary="Wasser im Keller"
    )

    assert first.already_escalated is False
    assert second.already_escalated is True
    assert len(notifier.calls) == 1, "Der Kanal darf kein zweites Mal angesprochen werden"

    count = (await session.execute(select(func.count()).select_from(Escalation))).scalar_one()
    assert count == 1


async def test_ohne_bereitschaftskontakt_schlaegt_alarmierung_fehl(session, conversation):
    service = EscalationService(session)
    service.notifiers = [_StubNotifier("email", DeliveryStatus.DELIVERED)]

    outcome = await service.trigger(
        conversation_id=conversation.id, reason="Wasser", summary="Wasser im Keller"
    )

    assert not outcome.any_delivered
    assert "NICHT" in outcome.as_tool_result()


async def test_verlauf_landet_in_der_benachrichtigung(session, conversation, contact):
    from app.core.agent import NewMessage

    await ConversationService(session)._persist(
        conversation.id,
        [
            NewMessage(
                role="user",
                content="Im Keller steht Wasser",
                blocks=[{"type": "text", "text": "x"}],
            )
        ],
    )

    notifier = _StubNotifier("email", DeliveryStatus.DELIVERED)
    service = EscalationService(session)
    service.notifiers = [notifier]

    await service.trigger(
        conversation_id=conversation.id,
        reason="Wasseraustritt",
        summary="Wasser im Keller",
        phone="+4917612345",
    )

    body = notifier.calls[0]
    assert "Anrufer: Im Keller steht Wasser" in body
    assert "+4917612345" in body


async def test_sms_ist_als_uebersprungen_protokolliert(session, conversation, contact):
    """SMS ist bewusst nicht aktiv – das soll im Protokoll sichtbar sein,
    nicht stillschweigend fehlen."""
    service = EscalationService(session)
    service.notifiers = [
        _StubNotifier("email", DeliveryStatus.DELIVERED),
        channels.NoopSmsNotifier(),
    ]

    await service.trigger(
        conversation_id=conversation.id, reason="Wasser", summary="Wasser im Keller"
    )

    attempts = (await session.execute(select(EscalationAttempt))).scalars().all()
    by_channel = {a.channel.value: a for a in attempts}
    assert by_channel["email"].status is AttemptStatus.DELIVERED
    assert by_channel["sms"].status is AttemptStatus.SKIPPED
