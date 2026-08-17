"""WebSocket-Kanal für den Browser.

Der Aufbau ist eine Fließstrecke mit zwei Bahnen:

    Claude → Textdeltas ──→ sofort an den Browser (Transkript)
                        └─→ Satzpuffer → Warteschlange → Sprachausgabe → Audio

Text erscheint dadurch ohne Verzögerung, das Audio folgt einen Takt später.
Weil die Sprachausgabe in einer eigenen Aufgabe *sequenziell* aus der
Warteschlange liest, bleibt die Reihenfolge der Sätze erhalten, ohne den
Textstrom auszubremsen.

Protokoll – Client an Server:
    {"type": "user_text", "text": "..."}   ein Gesprächszug
    {"type": "interrupt"}                  Resa unterbrechen
    {"type": "end"}                        Gespräch abschließen

Server an Client:
    {"type": "ready", ...}                 Gespräch angelegt, Begrüßung folgt
    {"type": "assistant_delta", ...}       Text, zeichenweise
    {"type": "tool", ...}                  Werkzeug lief (Alarmierung, Erfassung)
    {"type": "speech", "seq": n, ...}      Ankündigung – der nächste Frame ist Audio
    <binärer Frame>                        MP3 zum vorigen speech-Rahmen
    {"type": "turn_done"}                  Zug beendet
    {"type": "interrupted"}                Abbruch bestätigt
    {"type": "error", ...}
"""

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.core.agent import (
    AgentNotConfigured,
    TextDelta,
    ToolInvoked,
    ToolStarted,
    TurnComplete,
)
from app.core.conversation import ConversationService
from app.core.prompts import GREETING, TOOL_FILLERS
from app.core.sentences import SentenceBuffer
from app.db.session import SessionFactory
from app.events import broadcaster
from app.limits import MAX_TEXT_LENGTH, MAX_TURNS, limiter
from app.voice.tts import TtsError, VoiceService

logger = logging.getLogger(__name__)

router = APIRouter()

# Ende-Markierung für die Sprach-Warteschlange.
_DONE = object()


class WebChannel:
    def __init__(self, websocket: WebSocket, session, conversation_id: int) -> None:
        self.ws = websocket
        self.session = session
        self.conversation_id = conversation_id
        self.voice = VoiceService(session)
        self._seq = 0

    # -- Sprachausgabe -----------------------------------------------------

    async def _speak_worker(self, queue: asyncio.Queue) -> None:
        """Liest Sätze der Reihe nach und schickt Ankündigung plus Audio."""
        while True:
            item = await queue.get()
            if item is _DONE:
                return
            text: str = item
            self._seq += 1
            seq = self._seq
            try:
                audio, cached = await self.voice.speak(text)
            except TtsError as exc:
                # Stumm weiterlaufen ist besser als das Gespräch abzubrechen –
                # der Text steht ohnehin schon im Transkript.
                logger.warning("Sprachausgabe fehlgeschlagen: %s", exc)
                await self._send({"type": "speech_failed", "seq": seq, "detail": str(exc)})
                continue

            if not audio:
                continue
            await self._send({"type": "speech", "seq": seq, "text": text, "cached": cached})
            await self.ws.send_bytes(audio)

    async def _send(self, payload: dict) -> None:
        await self.ws.send_json(payload)

    # -- Gesprächszug ------------------------------------------------------

    async def run_turn(self, user_text: str) -> None:
        service = ConversationService(self.session)
        buffer = SentenceBuffer()
        queue: asyncio.Queue = asyncio.Queue()
        worker = asyncio.create_task(self._speak_worker(queue))

        spoken_anything = False
        try:
            async for event in service.handle_turn(self.conversation_id, user_text):
                if isinstance(event, TextDelta):
                    await self._send({"type": "assistant_delta", "text": event.text})
                    for sentence in buffer.feed(event.text):
                        spoken_anything = True
                        await queue.put(sentence)
                elif isinstance(event, ToolStarted):
                    # Nur überbrücken, wenn bisher nichts gesagt wurde. Hat Resa
                    # schon zu sprechen begonnen, wäre die Ansage eine Störung.
                    filler = TOOL_FILLERS.get(event.name)
                    if filler and not spoken_anything and not buffer.pending:
                        spoken_anything = True
                        await self._send({"type": "assistant_delta", "text": filler + " "})
                        await queue.put(filler)
                elif isinstance(event, ToolInvoked):
                    await self._send({"type": "tool", "name": event.name, "signals": event.signals})
                    # Ein erfasster Fall oder eine Alarmierung soll sofort im
                    # Dashboard auftauchen, nicht erst beim nächsten Laden.
                    await broadcaster.publish(
                        {"type": "conversation_updated", "signals": event.signals}
                    )
                elif isinstance(event, TurnComplete):
                    rest = buffer.flush()
                    if rest:
                        await queue.put(rest)
        finally:
            await queue.put(_DONE)
            await worker

        await self._send({"type": "turn_done"})

    async def greet(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        worker = asyncio.create_task(self._speak_worker(queue))
        await queue.put(GREETING)
        await queue.put(_DONE)
        await worker


@router.websocket("/ws/web")
async def web_channel(websocket: WebSocket) -> None:
    # Bewusst ohne Anmeldung: Wer anruft, hat keine Zugangsdaten. Dafür greift
    # eine Mengenbegrenzung – der Endpunkt kostet bei jedem Aufruf Geld.
    herkunft = websocket.client.host if websocket.client else "unbekannt"
    abgelehnt = limiter.acquire(herkunft)
    if abgelehnt is not None:
        await websocket.accept()
        await websocket.send_json({"type": "error", "detail": abgelehnt.reason})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.info("Gespräch abgewiesen (%s): %s", herkunft, abgelehnt.reason)
        return

    async with SessionFactory() as session:
        await websocket.accept()
        service = ConversationService(session)
        conversation = await service.start()
        channel = WebChannel(websocket, session, conversation.id)
        zuege = 0

        await websocket.send_json(
            {
                "type": "ready",
                "conversation_id": str(conversation.public_id),
                "greeting": GREETING,
            }
        )
        await broadcaster.publish({"type": "conversation_started"})

        turn: asyncio.Task | None = None
        try:
            await channel.greet()

            while True:
                message = await websocket.receive_json()
                kind = message.get("type")

                if kind == "interrupt":
                    if turn is not None and not turn.done():
                        turn.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await turn
                    await websocket.send_json({"type": "interrupted"})
                    continue

                if kind == "end":
                    break

                if kind != "user_text":
                    continue

                text = (message.get("text") or "").strip()[:MAX_TEXT_LENGTH]
                if not text:
                    continue

                zuege += 1
                if zuege > MAX_TURNS:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "detail": (
                                "Dieses Gespräch hat die maximale Länge erreicht. "
                                "Bitte beginne ein neues."
                            ),
                        }
                    )
                    break

                if turn is not None and not turn.done():
                    # Nachgeschobene Eingabe während Resa noch spricht: der
                    # laufende Zug weicht, der neue gilt.
                    turn.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await turn

                turn = asyncio.create_task(channel.run_turn(text))
                try:
                    await turn
                except asyncio.CancelledError:
                    pass
                except AgentNotConfigured as exc:
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Gesprächszug gescheitert")
                    await websocket.send_json(
                        {"type": "error", "detail": f"Es ist etwas schiefgelaufen: {exc}"}
                    )

        except WebSocketDisconnect:
            logger.info("Browser hat die Verbindung getrennt")
        finally:
            if turn is not None and not turn.done():
                turn.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await turn
            await service.end(conversation.id, reason="closed")
            await broadcaster.publish({"type": "conversation_ended"})
            limiter.release(herkunft)
            with contextlib.suppress(RuntimeError):
                await websocket.close()
