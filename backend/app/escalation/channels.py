"""Zustellkanäle für die Notfall-Alarmierung.

Jeder Kanal meldet ehrlich zurück, ob zugestellt wurde. Diese Rückmeldung geht
bis zu Resa durch – sie soll nie behaupten, jemand sei informiert, wenn das
nicht stimmt.
"""

import logging
from dataclasses import dataclass
from email.message import EmailMessage
from enum import StrEnum
from typing import Protocol

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class DeliveryResult:
    channel: str
    status: DeliveryStatus
    detail: str

    @property
    def ok(self) -> bool:
        return self.status is DeliveryStatus.DELIVERED


class Notifier(Protocol):
    channel: str

    async def send(self, *, to: str, subject: str, body: str) -> DeliveryResult: ...


class EmailNotifier:
    channel = "email"

    async def send(self, *, to: str, subject: str, body: str) -> DeliveryResult:
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message["X-Priority"] = "1"
        message.set_content(body)

        try:
            await aiosmtplib.send(
                message,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=settings.smtp_password or None,
                start_tls=settings.smtp_use_tls,
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001 – jeder Fehler ist hier eine Nichtzustellung
            logger.error("E-Mail-Alarmierung an %s fehlgeschlagen: %s", to, exc)
            return DeliveryResult("email", DeliveryStatus.FAILED, str(exc))

        logger.info("E-Mail-Alarmierung an %s zugestellt", to)
        return DeliveryResult("email", DeliveryStatus.DELIVERED, f"zugestellt an {to}")


class NoopSmsNotifier:
    """Platzhalter.

    SMS ist bewusst nicht im MVP: Deutsche Festnetznummern versenden in der Regel
    keine SMS, ein alphanumerischer Absender ist teils registrierungspflichtig.
    Die Schnittstelle steht, damit ein echter Anbieter (Brevo, MessageBird,
    Telnyx) später nur eine weitere Klasse ist.
    """

    channel = "sms"

    async def send(self, *, to: str, subject: str, body: str) -> DeliveryResult:
        return DeliveryResult(
            "sms", DeliveryStatus.SKIPPED, "SMS-Versand ist in dieser Ausbaustufe nicht aktiv"
        )
