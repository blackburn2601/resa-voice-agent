"""Werkzeugdefinitionen und ihre Ausführung.

Die Beschreibungen sagen bewusst, *wann* ein Werkzeug zu rufen ist, nicht nur
was es tut – daran entscheidet das Modell.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, IntakeRecord, Urgency
from app.escalation.service import EscalationService
from app.rag import retrieve
from app.rag.embeddings import EmbeddingError

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Definitionen
# --------------------------------------------------------------------------

SEARCH_KNOWLEDGE = {
    "name": "search_knowledge",
    "description": (
        "Durchsucht die Wissensbasis der Hausverwaltung und gibt die passenden "
        "Textstellen mit Quellenangabe zurück. Rufe dies auf, sobald die Person "
        "nach etwas fragt, das die Hausverwaltung festgelegt hat: Bürozeiten, "
        "Erreichbarkeit, Notdienst-Regeln, Zuständigkeiten, Nebenkostenabrechnung, "
        "Hausordnung, Kündigungsfristen. Für Angaben, die die Person selbst gerade "
        "genannt hat, brauchst du keine Suche. Findet die Suche nichts, sage das "
        "offen, statt eine Antwort zu erfinden."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Die Suchanfrage in natürlicher Sprache, z. B. "
                    "'Wann ist das Büro erreichbar?' oder 'Notdienst am Wochenende'."
                ),
            }
        },
        "required": ["query"],
    },
}

ESCALATE_EMERGENCY = {
    "name": "escalate_emergency",
    "description": (
        "Alarmiert sofort den diensthabenden Bereitschaftskontakt. Rufe dies auf, "
        "SOBALD du einen Notfall erkennst - noch bevor die Erfassung vollständig "
        "ist. Ein Gespräch kann abbrechen; die Alarmierung darf davon nicht "
        "abhängen. Notfall heißt: Wasseraustritt, Gasgeruch, Heizungsausfall bei "
        "Kälte, Stromausfall, nicht verschließbare Wohnungstür, oder alles, was "
        "Menschen gefährdet. Das Werkzeug meldet zurück, welche Kanäle tatsächlich "
        "zugestellt haben - sage der Person nur das, was dort steht. Ein zweiter "
        "Aufruf im selben Gespräch löst keine zweite Alarmierung aus."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "kurzbeschreibung": {
                "type": "string",
                "description": (
                    "Worum es geht, in einem Satz. "
                    "Z. B. 'Wasseraustritt aus der Heizung im Keller'."
                ),
            },
            "zusammenfassung": {
                "type": "string",
                "description": "Alles, was bisher bekannt ist - auch wenn noch Angaben fehlen.",
            },
            "adresse": {
                "type": "string",
                "description": "Adresse des betroffenen Objekts, falls schon genannt.",
            },
            "rueckrufnummer": {
                "type": "string",
                "description": "Telefonnummer der anrufenden Person, falls schon genannt.",
            },
            "gefahr_im_verzug": {
                "type": "boolean",
                "description": (
                    "true, wenn Menschen unmittelbar gefährdet sind oder der Schaden "
                    "sich in Minuten vergrößert."
                ),
            },
        },
        "required": ["kurzbeschreibung", "zusammenfassung", "gefahr_im_verzug"],
    },
}

LOG_INTAKE = {
    "name": "log_intake",
    "description": (
        "Speichert den erfassten Fall strukturiert im Dashboard. Rufe dies am Ende "
        "auf, sobald du Name, Kategorie und eine Zusammenfassung hast - bei einer "
        "Schadensmeldung zusätzlich die Adresse. Danach bestätigst du der Person "
        "kurz, was aufgenommen wurde."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "caller_name": {"type": "string", "description": "Name der anrufenden Person."},
            "category": {
                "type": "string",
                "enum": [c.value for c in Category],
                "description": "Kategorie des Anliegens.",
            },
            "summary": {
                "type": "string",
                "description": (
                    "Das Anliegen in ein bis zwei Sätzen, aus Sicht des Teams formuliert."
                ),
            },
            "property_address": {
                "type": "string",
                "description": "Adresse des Objekts. Bei einer Schadensmeldung erforderlich.",
            },
            "urgency": {
                "type": "string",
                "enum": [u.value for u in Urgency],
                "description": (
                    "notfall = sofortiges Handeln nötig, dringend = heute noch, "
                    "normal = im regulären Ablauf."
                ),
            },
            "occurred_since": {
                "type": "string",
                "description": "Seit wann das Problem besteht, z. B. 'seit heute Morgen'.",
            },
            "danger_to_life": {
                "type": "boolean",
                "description": "true, wenn Menschen gefährdet sind.",
            },
            "callback_requested": {
                "type": "boolean",
                "description": "true, wenn die Person zurückgerufen werden möchte.",
            },
            "preferred_callback_time": {
                "type": "string",
                "description": "Wunschzeit für den Rückruf, z. B. 'morgen vormittags'.",
            },
            "caller_phone": {
                "type": "string",
                "description": "Rückrufnummer, falls genannt.",
            },
        },
        "required": ["caller_name", "category", "summary", "urgency"],
    },
}

TOOLS: list[dict[str, Any]] = [SEARCH_KNOWLEDGE, ESCALATE_EMERGENCY, LOG_INTAKE]


# --------------------------------------------------------------------------
# Ausführung
# --------------------------------------------------------------------------


class SearchArgs(BaseModel):
    query: str


class EscalateArgs(BaseModel):
    kurzbeschreibung: str
    zusammenfassung: str
    gefahr_im_verzug: bool = False
    adresse: str | None = None
    rueckrufnummer: str | None = None


class IntakeArgs(BaseModel):
    caller_name: str
    category: Category
    summary: str
    urgency: Urgency = Urgency.NORMAL
    property_address: str | None = None
    occurred_since: str | None = None
    danger_to_life: bool = False
    callback_requested: bool = False
    preferred_callback_time: str | None = Field(default=None)
    caller_phone: str | None = None


@dataclass(slots=True)
class ToolContext:
    session: AsyncSession
    conversation_id: int


@dataclass(slots=True)
class ToolOutcome:
    content: str
    is_error: bool = False
    # Signale für die Oberfläche – der WebSocket-Adapter reicht sie durch.
    signals: list[str] = field(default_factory=list)


async def _run_search(args: SearchArgs) -> ToolOutcome:
    try:
        passages = await retrieve.search(args.query)
    except EmbeddingError as exc:
        return ToolOutcome(
            f"Die Wissensbasis ist gerade nicht erreichbar ({exc}). Sage der Person, "
            "dass du das nicht nachsehen kannst, und biete einen Rückruf an.",
            is_error=True,
        )
    return ToolOutcome(retrieve.format_passages(passages))


async def _run_escalate(args: EscalateArgs, ctx: ToolContext) -> ToolOutcome:
    service = EscalationService(ctx.session)
    outcome = await service.trigger(
        conversation_id=ctx.conversation_id,
        reason=args.kurzbeschreibung,
        summary=args.zusammenfassung,
        address=args.adresse,
        phone=args.rueckrufnummer,
        danger_to_life=args.gefahr_im_verzug,
    )
    signals = ["escalated"] if outcome.any_delivered else ["escalation_failed"]
    return ToolOutcome(outcome.as_tool_result(), signals=signals)


async def _run_intake(args: IntakeArgs, ctx: ToolContext) -> ToolOutcome:
    existing = await ctx.session.execute(
        select(IntakeRecord).where(IntakeRecord.conversation_id == ctx.conversation_id)
    )
    record = existing.scalars().first()
    if record is None:
        record = IntakeRecord(conversation_id=ctx.conversation_id)
        ctx.session.add(record)

    # Erneuter Aufruf korrigiert den Datensatz, statt einen zweiten anzulegen –
    # Resa trägt manchmal nach, wenn die Person noch etwas ergänzt.
    record.caller_name = args.caller_name
    record.category = args.category
    record.summary = args.summary
    record.urgency = args.urgency
    record.property_address = args.property_address
    record.occurred_since = args.occurred_since
    record.danger_to_life = args.danger_to_life
    record.callback_requested = args.callback_requested
    record.preferred_callback_time = args.preferred_callback_time
    record.caller_phone = args.caller_phone
    record.incomplete = False

    await ctx.session.commit()
    return ToolOutcome(
        f"Fall gespeichert: {args.category.value}, Dringlichkeit {args.urgency.value}.",
        signals=["intake_logged"],
    )


_ARG_MODELS = {
    "search_knowledge": SearchArgs,
    "escalate_emergency": EscalateArgs,
    "log_intake": IntakeArgs,
}


async def execute_tool(name: str, tool_input: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    model = _ARG_MODELS.get(name)
    if model is None:
        return ToolOutcome(f"Unbekanntes Werkzeug: {name}", is_error=True)

    try:
        args = model(**tool_input)
    except ValidationError as exc:
        # Zurück ans Modell statt Abbruch – es kann den Aufruf korrigieren.
        logger.warning("Ungültige Argumente für %s: %s", name, exc)
        return ToolOutcome(f"Ungültige Argumente: {exc}", is_error=True)

    try:
        if name == "search_knowledge":
            return await _run_search(args)  # type: ignore[arg-type]
        if name == "escalate_emergency":
            return await _run_escalate(args, ctx)  # type: ignore[arg-type]
        return await _run_intake(args, ctx)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Werkzeug %s ist gescheitert", name)
        await ctx.session.rollback()
        return ToolOutcome(f"Das Werkzeug ist gescheitert: {exc}", is_error=True)
