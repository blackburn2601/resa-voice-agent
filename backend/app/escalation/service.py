"""Notfall-Alarmierung.

Zwei Eigenschaften sind hier tragend:

1. **Idempotenz.** Eine Eskalation je Gespräch, erzwungen über die Unique-
   Constraint. Ruft Resa das Werkzeug ein zweites Mal auf, kommt dieselbe
   Auskunft zurück statt einer zweiten Alarmierung.
2. **Wahrheitsgemäße Rückmeldung.** Was zurückgegeben wird, ist das, was
   tatsächlich zugestellt wurde – nicht das, was versucht wurde.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AttemptStatus,
    Escalation,
    EscalationAttempt,
    EscalationChannel,
    EscalationStatus,
    Message,
    OnCallContact,
)
from app.escalation.channels import (
    DeliveryResult,
    DeliveryStatus,
    EmailNotifier,
    NoopSmsNotifier,
    Notifier,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EscalationOutcome:
    already_escalated: bool
    delivered_channels: list[str]
    failed_channels: list[str]
    contact_name: str | None

    @property
    def any_delivered(self) -> bool:
        return bool(self.delivered_channels)

    def as_tool_result(self) -> str:
        """Der Text, den Resa liest, bevor sie der Person antwortet."""
        if self.already_escalated:
            base = "Für dieses Gespräch wurde bereits alarmiert; es wurde nichts doppelt gesendet."
            if self.delivered_channels:
                return f"{base} Zugestellt wurde damals über: {', '.join(self.delivered_channels)}."
            return base

        if self.any_delivered:
            who = f" an {self.contact_name}" if self.contact_name else ""
            text = f"Alarmierung zugestellt{who} über: {', '.join(self.delivered_channels)}."
            if self.failed_channels:
                text += f" Fehlgeschlagen: {', '.join(self.failed_channels)}."
            text += " Du kannst der Person sagen, dass der Notdienst informiert ist."
            return text

        return (
            "Die Alarmierung konnte NICHT zugestellt werden "
            f"(fehlgeschlagen: {', '.join(self.failed_channels) or 'kein Kanal verfügbar'}). "
            "Sage der Person offen, dass du den Notdienst nicht erreichen konntest, und "
            "weise bei Gefahr für Menschen auf den Notruf 112 hin."
        )


def _build_body(
    *,
    reason: str,
    summary: str,
    address: str | None,
    phone: str | None,
    danger: bool,
    transcript: str,
) -> str:
    lines = [
        "Resa hat einen Notfall aufgenommen.",
        "",
        f"Gemeldet:  {datetime.now(UTC).astimezone():%d.%m.%Y %H:%M %Z}",
        f"Anliegen:  {reason}",
        f"Objekt:    {address or 'nicht genannt'}",
        f"Rückruf:   {phone or 'nicht genannt'}",
        f"Gefahr im Verzug: {'JA' if danger else 'nein'}",
        "",
        "Zusammenfassung",
        "---------------",
        summary,
        "",
        "Gesprächsverlauf",
        "----------------",
        transcript or "(noch kein Verlauf)",
    ]
    return "\n".join(lines)


class EscalationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        # Über das Protokoll typisiert: Ein echter SMS-Anbieter tritt hier
        # später neben die Attrappe, ohne dass sonst etwas angefasst wird.
        self.notifiers: list[Notifier] = [EmailNotifier(), NoopSmsNotifier()]

    async def _active_contact(self) -> OnCallContact | None:
        result = await self.session.execute(
            select(OnCallContact).where(OnCallContact.active.is_(True)).order_by(OnCallContact.id)
        )
        return result.scalars().first()

    async def _transcript(self, conversation_id: int) -> str:
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.content != "")
            .order_by(Message.seq)
        )
        speaker = {"user": "Anrufer", "assistant": "Resa"}
        return "\n".join(
            f"{speaker.get(m.role.value, m.role.value)}: {m.content}" for m in result.scalars()
        )

    async def _existing(self, conversation_id: int) -> Escalation | None:
        result = await self.session.execute(
            select(Escalation)
            .where(Escalation.conversation_id == conversation_id)
            .options(selectinload(Escalation.attempts))
        )
        return result.scalars().first()

    async def trigger(
        self,
        *,
        conversation_id: int,
        reason: str,
        summary: str,
        address: str | None = None,
        phone: str | None = None,
        danger_to_life: bool = False,
    ) -> EscalationOutcome:
        existing = await self._existing(conversation_id)
        if existing is not None:
            return EscalationOutcome(
                already_escalated=True,
                delivered_channels=[
                    a.channel.value
                    for a in existing.attempts
                    if a.status is AttemptStatus.DELIVERED
                ],
                failed_channels=[],
                contact_name=None,
            )

        escalation = Escalation(
            conversation_id=conversation_id,
            reason=reason,
            summary=summary,
            property_address=address,
            caller_phone=phone,
            status=EscalationStatus.PENDING,
        )
        self.session.add(escalation)
        try:
            # Früh festschreiben: Selbst wenn das Versenden gleich scheitert oder
            # der Prozess stirbt, ist der Notfall dokumentiert.
            await self.session.flush()
        except IntegrityError:
            # Zwei Aufrufe praktisch gleichzeitig – die Constraint hat gehalten.
            await self.session.rollback()
            existing = await self._existing(conversation_id)
            return EscalationOutcome(
                already_escalated=True,
                delivered_channels=[
                    a.channel.value
                    for a in (existing.attempts if existing else [])
                    if a.status is AttemptStatus.DELIVERED
                ],
                failed_channels=[],
                contact_name=None,
            )

        contact = await self._active_contact()
        transcript = await self._transcript(conversation_id)
        body = _build_body(
            reason=reason,
            summary=summary,
            address=address,
            phone=phone,
            danger=danger_to_life,
            transcript=transcript,
        )
        subject = f"[NOTFALL] {reason[:80]}" + (" – Gefahr im Verzug" if danger_to_life else "")

        results: list[DeliveryResult] = []
        if contact is None:
            logger.error("Kein aktiver Bereitschaftskontakt hinterlegt – Alarmierung unmöglich")
            results.append(
                DeliveryResult(
                    "email", DeliveryStatus.FAILED, "kein aktiver Bereitschaftskontakt hinterlegt"
                )
            )
        else:
            for notifier in self.notifiers:
                target = contact.email if notifier.channel == "email" else (contact.phone or "")
                if not target:
                    results.append(
                        DeliveryResult(
                            notifier.channel,
                            DeliveryStatus.SKIPPED,
                            "kein Ziel im Bereitschaftskontakt hinterlegt",
                        )
                    )
                    continue
                results.append(await notifier.send(to=target, subject=subject, body=body))

        for result in results:
            self.session.add(
                EscalationAttempt(
                    escalation_id=escalation.id,
                    channel=EscalationChannel(result.channel),
                    status=AttemptStatus(result.status.value),
                    detail=result.detail,
                )
            )

        delivered = [r.channel for r in results if r.ok]
        failed = [r.channel for r in results if r.status is DeliveryStatus.FAILED]
        if delivered and not failed:
            escalation.status = EscalationStatus.DELIVERED
        elif delivered:
            escalation.status = EscalationStatus.PARTIAL
        else:
            escalation.status = EscalationStatus.FAILED

        await self.session.commit()

        return EscalationOutcome(
            already_escalated=False,
            delivered_channels=delivered,
            failed_channels=failed,
            contact_name=contact.name if contact else None,
        )
