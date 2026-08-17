"""Anthropic-Anbindung: Streaming plus Werkzeugschleife.

Konfiguration und ihre Gründe:

* `thinking: adaptive` mit `effort: low`. Niedriger Effort ist auf Opus 5
  auffällig stark und hält die Antwortzeit kurz – bei einem gesprochenen
  Gespräch zählt jede Zehntelsekunde. Thinking wird *nicht* abgeschaltet: ohne
  Thinking schreibt das Modell Werkzeugaufrufe gelegentlich als sichtbaren Text
  (der Aufruf läuft dann nie) und lässt interne Tags durchsickern.
* `max_tokens` großzügig, weil Thinking mit hineinzählt.
* `cache_control` auf dem System-Block. Tools und Systemprompt stehen am Anfang
  jeder Anfrage; über ein Gespräch mit zehn Turns spart das den Großteil der
  Eingabekosten.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from anthropic import APIError, AsyncAnthropic

from app.config import settings
from app.core.prompts import SYSTEM_PROMPT
from app.core.tools import TOOLS, ToolContext, execute_tool

logger = logging.getLogger(__name__)

MAX_TOKENS = 2048
# Schutz gegen eine Schleife, die sich nicht beruhigt. In der Praxis reichen
# zwei Runden: einmal suchen oder alarmieren, einmal antworten.
MAX_TOOL_ROUNDS = 5


class AgentNotConfigured(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Ereignisse
# --------------------------------------------------------------------------


@dataclass(slots=True)
class TextDelta:
    text: str


@dataclass(slots=True)
class ToolStarted:
    """Wird ausgegeben, *bevor* das Werkzeug läuft.

    Ein Zug mit Werkzeugaufruf braucht zwei Modellaufrufe, und der erste liefert
    keinen Text – ohne dieses Signal bliebe es mehrere Sekunden still, während
    im Hintergrund alarmiert wird.
    """

    name: str


@dataclass(slots=True)
class ToolInvoked:
    name: str
    signals: list[str]


@dataclass(slots=True)
class NewMessage:
    """Eine Nachricht, wie sie in Postgres landet."""

    role: str
    content: str  # Anzeigetext fürs Transkript
    blocks: list[Any]  # exakte Content-Blocks für die API


@dataclass(slots=True)
class TurnComplete:
    text: str
    new_messages: list[NewMessage] = field(default_factory=list)


AgentEvent = TextDelta | ToolStarted | ToolInvoked | TurnComplete


def _client() -> AsyncAnthropic:
    if not settings.anthropic_configured:
        raise AgentNotConfigured(
            "ANTHROPIC_API_KEY ist nicht gesetzt. "
            "Trage ihn in die .env ein, damit Resa antworten kann."
        )
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


def _text_of(blocks: list[Any]) -> str:
    return "".join(b.text for b in blocks if getattr(b, "type", None) == "text").strip()


async def stream_turn(
    history: list[dict[str, Any]],
    ctx: ToolContext,
) -> AsyncIterator[AgentEvent]:
    """Verarbeitet einen Gesprächszug und streamt die Antwort.

    `history` enthält bereits die neue Nutzernachricht – sie wird vom Aufrufer
    gespeichert, *bevor* das Modell läuft. Sonst fehlte sie im Verlauf, den eine
    Notfall-Alarmierung mitschickt, und ginge bei einem Abbruch mitten im Zug
    ganz verloren.
    """
    client = _client()

    messages: list[dict[str, Any]] = list(history)
    new_messages: list[NewMessage] = []

    request: dict[str, Any] = {
        "model": settings.anthropic_model,
        "max_tokens": MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "tools": TOOLS,
        "thinking": {"type": "adaptive"},
        # Über extra_body, damit es unabhängig davon funktioniert, ob die
        # installierte SDK-Version das Feld schon typisiert.
        "extra_body": {"output_config": {"effort": "low"}},
    }

    reply_parts: list[str] = []

    for _round in range(MAX_TOOL_ROUNDS):
        try:
            async with client.messages.stream(messages=messages, **request) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and getattr(event.delta, "type", None) == "text_delta"
                    ):
                        reply_parts.append(event.delta.text)
                        yield TextDelta(event.delta.text)
                    elif (
                        event.type == "content_block_start"
                        and getattr(event.content_block, "type", None) == "tool_use"
                    ):
                        # Sobald das Modell *anfängt*, den Aufruf zu schreiben –
                        # nicht erst, wenn die ganze Antwort fertig ist. Das sind
                        # rund anderthalb Sekunden Unterschied bis zum ersten Ton.
                        yield ToolStarted(name=event.content_block.name)
                final = await stream.get_final_message()
        except APIError as exc:
            logger.exception("Anthropic-Aufruf fehlgeschlagen")
            raise RuntimeError(f"Anthropic-Aufruf fehlgeschlagen: {exc}") from exc

        assistant_text = _text_of(final.content)
        assistant_blocks = [b.model_dump(exclude_none=True) for b in final.content]
        messages.append({"role": "assistant", "content": assistant_blocks})
        new_messages.append(
            NewMessage(role="assistant", content=assistant_text, blocks=assistant_blocks)
        )

        if final.stop_reason == "refusal":
            yield TurnComplete(
                text="Dazu kann ich nichts sagen.",
                new_messages=new_messages,
            )
            return

        tool_uses = [b for b in final.content if b.type == "tool_use"]
        if not tool_uses:
            break

        result_blocks: list[dict[str, Any]] = []
        for block in tool_uses:
            outcome = await execute_tool(block.name, dict(block.input), ctx)
            yield ToolInvoked(name=block.name, signals=outcome.signals)
            result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.content,
                    **({"is_error": True} if outcome.is_error else {}),
                }
            )

        # Alle Ergebnisse in *einer* Nachricht – getrennt zu senden bringt das
        # Modell dazu, künftig keine parallelen Aufrufe mehr zu machen.
        messages.append({"role": "user", "content": result_blocks})
        new_messages.append(NewMessage(role="user", content="", blocks=result_blocks))
    else:
        logger.warning("Werkzeugschleife nach %d Runden abgebrochen", MAX_TOOL_ROUNDS)

    yield TurnComplete(text="".join(reply_parts).strip(), new_messages=new_messages)
