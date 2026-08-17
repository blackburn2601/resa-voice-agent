"""Gespräche fürs Dashboard: Liste, Detail und der Live-Strom."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.db.models import (
    Conversation,
    Escalation,
    IntakeRecord,
    Message,
    Urgency,
)
from app.deps import CurrentUser, SessionDep
from app.events import broadcaster, sse_stream

router = APIRouter(tags=["gespräche"])


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    role: str
    content: str
    created_at: datetime


class AttemptOut(BaseModel):
    channel: str
    status: str
    detail: str | None
    attempted_at: datetime


class EscalationOut(BaseModel):
    triggered_at: datetime
    reason: str
    summary: str
    status: str
    attempts: list[AttemptOut]


class IntakeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    caller_name: str
    category: str
    summary: str
    property_address: str | None
    urgency: str
    occurred_since: str | None
    danger_to_life: bool
    callback_requested: bool
    preferred_callback_time: str | None
    caller_phone: str | None
    incomplete: bool


class ConversationListItem(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None
    status: str
    caller_name: str | None
    category: str | None
    urgency: str | None
    summary: str | None
    escalated: bool
    incomplete: bool


class ConversationDetail(ConversationListItem):
    messages: list[MessageOut]
    intake: IntakeOut | None
    escalation: EscalationOut | None


def _to_item(conversation: Conversation, escalated: bool) -> ConversationListItem:
    intake = conversation.intake
    return ConversationListItem(
        id=str(conversation.public_id),
        started_at=conversation.started_at,
        ended_at=conversation.ended_at,
        status=conversation.status.value,
        caller_name=intake.caller_name if intake else None,
        category=intake.category.value if intake else None,
        urgency=intake.urgency.value if intake else None,
        summary=intake.summary if intake else None,
        escalated=escalated,
        incomplete=bool(intake and intake.incomplete),
    )


@router.get("/conversations", response_model=list[ConversationListItem])
async def list_conversations(
    session: SessionDep,
    _: CurrentUser,
    urgency: Urgency | None = None,
    status_filter: Literal["active", "completed", "abandoned"] | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ConversationListItem]:
    query = (
        select(Conversation)
        .options(selectinload(Conversation.intake))
        .order_by(Conversation.started_at.desc())
    )
    if urgency is not None:
        query = query.join(IntakeRecord).where(IntakeRecord.urgency == urgency)
    if status_filter is not None:
        query = query.where(Conversation.status == status_filter)

    conversations = (await session.execute(query.limit(limit).offset(offset))).scalars().all()

    escalated_ids = set(
        (
            await session.execute(
                select(Escalation.conversation_id).where(
                    Escalation.conversation_id.in_([c.id for c in conversations] or [0])
                )
            )
        )
        .scalars()
        .all()
    )
    return [_to_item(c, c.id in escalated_ids) for c in conversations]


@router.get("/conversations/stats")
async def stats(session: SessionDep, _: CurrentUser) -> dict:
    total = (await session.execute(select(func.count()).select_from(Conversation))).scalar_one()
    emergencies = (
        await session.execute(
            select(func.count())
            .select_from(IntakeRecord)
            .where(IntakeRecord.urgency == Urgency.NOTFALL)
        )
    ).scalar_one()
    escalations = (await session.execute(select(func.count()).select_from(Escalation))).scalar_one()
    open_cases = (
        await session.execute(
            select(func.count()).select_from(Conversation).where(Conversation.status == "active")
        )
    ).scalar_one()
    return {
        "gespraeche": total,
        "notfaelle": emergencies,
        "alarmierungen": escalations,
        "laufend": open_cases,
    }


@router.get("/conversations/{public_id}", response_model=ConversationDetail)
async def conversation_detail(
    public_id: str, session: SessionDep, _: CurrentUser
) -> ConversationDetail:
    conversation = (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.public_id == public_id)
                .options(selectinload(Conversation.intake))
            )
        )
        .scalars()
        .first()
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gespräch nicht gefunden.")

    # Nur sichtbare Nachrichten: Werkzeugaufrufe und ihre Ergebnisse gehören in
    # den Verlauf fürs Modell, nicht ins Transkript für Menschen.
    messages = (
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

    escalation = (
        (
            await session.execute(
                select(Escalation)
                .where(Escalation.conversation_id == conversation.id)
                .options(selectinload(Escalation.attempts))
            )
        )
        .scalars()
        .first()
    )

    base = _to_item(conversation, escalation is not None)
    return ConversationDetail(
        **base.model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
        intake=IntakeOut.model_validate(conversation.intake) if conversation.intake else None,
        escalation=(
            EscalationOut(
                triggered_at=escalation.triggered_at,
                reason=escalation.reason,
                summary=escalation.summary,
                status=escalation.status.value,
                attempts=[
                    AttemptOut(
                        channel=a.channel.value,
                        status=a.status.value,
                        detail=a.detail,
                        attempted_at=a.attempted_at,
                    )
                    for a in escalation.attempts
                ],
            )
            if escalation
            else None
        ),
    )


@router.get("/events")
async def events(request: Request, _: CurrentUser) -> StreamingResponse:
    """Live-Strom fürs Dashboard."""

    async def generator():
        async with broadcaster.subscribe() as queue:
            async for chunk in sse_stream(queue, request.is_disconnected):
                yield chunk

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
