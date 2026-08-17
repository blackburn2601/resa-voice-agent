"""Sprachausgabe über ElevenLabs.

Aufgerufen wird satzweise, nicht für die ganze Antwort – das Audio läuft
dadurch schon, während Claude noch schreibt.

Der Cache ist mehr als eine Optimierung: ElevenLabs rechnet pro Zeichen ab, und
Sätze wie die Begrüßung fallen in jedem Gespräch an. Ab dem zweiten Mal kommen
sie ohne Netzaufruf und ohne Kosten.
"""

import hashlib
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import TtsCache

logger = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io/v1"
# mp3 spielt jeder Browser direkt ab; 44,1 kHz bei 128 kbit/s ist für Sprache
# reichlich und hält die Übertragung klein.
OUTPUT_FORMAT = "mp3_44100_128"
MIME_TYPE = "audio/mpeg"


class TtsError(RuntimeError):
    pass


class TtsNotConfigured(TtsError):
    pass


def cache_key(text: str, voice_id: str, model_id: str) -> str:
    return hashlib.sha256(f"{voice_id}|{model_id}|{text}".encode()).hexdigest()


class ElevenLabsTTS:
    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.elevenlabs_api_key
        self.voice_id = voice_id or settings.elevenlabs_voice_id
        self.model_id = model_id or settings.elevenlabs_model
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.voice_id)

    async def synthesize(self, text: str) -> bytes:
        if not self.configured:
            raise TtsNotConfigured(
                "ELEVENLABS_API_KEY oder ELEVENLABS_VOICE_ID fehlt – Resa bleibt stumm."
            )

        url = f"{API_BASE}/text-to-speech/{self.voice_id}/stream"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    params={"output_format": OUTPUT_FORMAT},
                    headers={"xi-api-key": self.api_key, "accept": MIME_TYPE},
                    json={"text": text, "model_id": self.model_id},
                )
                if response.status_code >= 400:
                    raise TtsError(
                        f"ElevenLabs antwortete {response.status_code}: {response.text[:300]}"
                    )
                return response.content
        except httpx.HTTPError as exc:
            raise TtsError(f"ElevenLabs nicht erreichbar: {exc}") from exc

    async def health(self) -> tuple[bool, str]:
        """Prüft die Sprachausgabe über einen echten, minimalen Aufruf.

        Nicht über `/v1/voices/{id}`: API-Schlüssel lassen sich bei ElevenLabs
        eng auf einzelne Rechte zuschneiden, und ein Schlüssel, der nur sprechen
        darf, wird dort mit 401 abgewiesen – obwohl genau das funktioniert, was
        wir brauchen. Geprüft wird deshalb die Fähigkeit selbst.

        Bewusst *nicht* im `/api/health`-Endpunkt aufrufen: Der läuft als
        Container-Healthcheck im Sekundentakt, und jeder Aufruf kostet Zeichen.
        """
        if not self.configured:
            return False, "nicht konfiguriert"
        try:
            audio = await self.synthesize("Test.")
        except TtsError as exc:
            return False, str(exc)
        return True, f"Stimme {self.voice_id}, Modell {self.model_id} ({len(audio)} Bytes Probe)"


class VoiceService:
    """TTS mit Zwischenspeicher in Postgres."""

    def __init__(self, session: AsyncSession, client: ElevenLabsTTS | None = None) -> None:
        self.session = session
        self.client = client or ElevenLabsTTS()

    async def speak(self, text: str) -> tuple[bytes, bool]:
        """Gibt (Audio, aus_dem_cache) zurück."""
        text = text.strip()
        if not text:
            return b"", False

        key = cache_key(text, self.client.voice_id or "", self.client.model_id)
        cached = (
            (await self.session.execute(select(TtsCache).where(TtsCache.text_hash == key)))
            .scalars()
            .first()
        )
        if cached is not None:
            return cached.audio, True

        audio = await self.client.synthesize(text)

        self.session.add(
            TtsCache(
                text_hash=key,
                voice_id=self.client.voice_id or "",
                model_id=self.client.model_id,
                audio=audio,
            )
        )
        try:
            await self.session.commit()
        except Exception:  # noqa: BLE001 – ein voller Cache darf nie das Sprechen verhindern
            await self.session.rollback()
            logger.warning("TTS-Cache konnte nicht geschrieben werden", exc_info=True)
        return audio, False
