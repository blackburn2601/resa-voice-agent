"""Wiederverwendbare Abhängigkeiten."""

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import get_session
from app.security import decode_access_token

SESSION_COOKIE = "resa_session"

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def resolve_user(token: str | None, session: AsyncSession) -> User | None:
    """Auch vom WebSocket genutzt – dort gibt es kein HTTPException-Handling."""
    if not token:
        return None
    payload = decode_access_token(token)
    if payload is None:
        return None
    user = (
        (await session.execute(select(User).where(User.email == payload.get("sub"))))
        .scalars()
        .first()
    )
    if user is None or not user.is_active:
        return None
    return user


async def current_user(
    session: SessionDep,
    resa_session: Annotated[str | None, Cookie()] = None,
) -> User:
    user = await resolve_user(resa_session, session)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht angemeldet.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
