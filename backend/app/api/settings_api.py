"""Bereitschaftskontakt pflegen.

Wer hier eingetragen ist, wird bei einem Notfall alarmiert. Ist niemand aktiv,
scheitert die Alarmierung – und Resa sagt das der anrufenden Person auch. Der
Zustand ist deshalb sichtbar und nicht stillschweigend leer.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.db.models import OnCallContact
from app.deps import CurrentUser, SessionDep

router = APIRouter(prefix="/settings", tags=["einstellungen"])


class ContactIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=64)


class ContactOut(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None
    active: bool
    created_at: datetime


def _out(contact: OnCallContact) -> ContactOut:
    return ContactOut(
        id=contact.id,
        name=contact.name,
        email=contact.email,
        phone=contact.phone,
        active=contact.active,
        created_at=contact.created_at,
    )


@router.get("/on-call", response_model=list[ContactOut])
async def list_contacts(session: SessionDep, _: CurrentUser) -> list[ContactOut]:
    contacts = (
        (await session.execute(select(OnCallContact).order_by(OnCallContact.id.desc())))
        .scalars()
        .all()
    )
    return [_out(c) for c in contacts]


@router.post("/on-call", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def create_contact(payload: ContactIn, session: SessionDep, _: CurrentUser) -> ContactOut:
    # Genau einer ist aktiv: Zwei Bereitschaften gleichzeitig hieße, dass
    # niemand weiß, wer zuständig ist.
    for contact in (await session.execute(select(OnCallContact))).scalars().all():
        contact.active = False

    contact = OnCallContact(
        name=payload.name, email=payload.email, phone=payload.phone, active=True
    )
    session.add(contact)
    await session.commit()
    return _out(contact)


@router.post("/on-call/{contact_id}/activate", response_model=ContactOut)
async def activate_contact(contact_id: int, session: SessionDep, _: CurrentUser) -> ContactOut:
    target = await session.get(OnCallContact, contact_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kontakt nicht gefunden.")

    for contact in (await session.execute(select(OnCallContact))).scalars().all():
        contact.active = contact.id == contact_id
    await session.commit()
    return _out(target)


@router.delete("/on-call/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(contact_id: int, session: SessionDep, _: CurrentUser) -> None:
    contact = await session.get(OnCallContact, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kontakt nicht gefunden.")
    if contact.active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Der aktive Bereitschaftskontakt kann nicht gelöscht werden. "
            "Setze zuerst einen anderen aktiv.",
        )
    await session.delete(contact)
    await session.commit()
