"""Anmeldung.

Das Token liegt in einem `httpOnly`-Cookie, nicht im localStorage – so kommt
kein eingeschleustes Skript im Browser daran.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from app.config import settings
from app.db.models import User
from app.deps import SESSION_COOKIE, CurrentUser, SessionDep
from app.security import create_access_token, hash_password, needs_rehash, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Einfache Bremse gegen Durchprobieren. Für ein Referenzprojekt mit einer
# Handvoll Konten ausreichend; bei mehreren Instanzen gehörte das nach Redis.
_ATTEMPTS: dict[str, list[float]] = {}
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 300


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _ATTEMPTS.get(key, []) if now - t < WINDOW_SECONDS]
    _ATTEMPTS[key] = recent
    return len(recent) >= MAX_ATTEMPTS


def _record_attempt(key: str) -> None:
    _ATTEMPTS.setdefault(key, []).append(time.monotonic())


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    email: str
    role: str


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginRequest, request: Request, response: Response, session: SessionDep
) -> UserOut:
    client = request.client.host if request.client else "unbekannt"
    if _rate_limited(client):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele Versuche. Bitte in einigen Minuten erneut probieren.",
        )
    _record_attempt(client)

    user = (
        (await session.execute(select(User).where(User.email == payload.email))).scalars().first()
    )

    # Auch ohne Treffer wird gehasht: Sonst verriete die Antwortzeit, welche
    # Adressen existieren.
    stored = user.password_hash if user else hash_password("dummy")
    valid = verify_password(payload.password, stored)

    if user is None or not valid or not user.is_active:
        await asyncio.sleep(0.3)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-Mail-Adresse oder Passwort ist falsch.",
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        await session.commit()

    response.set_cookie(
        SESSION_COOKIE,
        create_access_token(user.email, user.role.value),
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        max_age=12 * 3600,
        path="/",
    )
    logger.info("Anmeldung: %s", user.email)
    return UserOut(email=user.email, role=user.role.value)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut(email=user.email, role=user.role.value)
