"""Health-Check über alle Abhängigkeiten.

Prüft absichtlich auch die *fachliche* Erreichbarkeit – ob das Embedding-Modell
vorhanden ist, nicht nur ob der Port offen ist. Ein Ollama ohne bge-m3 wäre
sonst „gesund" und würde erst beim Indizieren scheitern.
"""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db.session import SessionFactory
from app.rag.embeddings import OllamaEmbeddings
from app.rag.vectorstore import VectorStore

router = APIRouter(tags=["system"])


async def _check_postgres() -> tuple[bool, str]:
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        return True, "verbunden"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


@router.get("/health")
async def health() -> JSONResponse:
    store = VectorStore()
    try:
        db_ok, db_detail = await _check_postgres()
        qdrant_ok, qdrant_detail = await store.health()
        embed_ok, embed_detail = await OllamaEmbeddings().health()
    finally:
        await store.close()

    checks: dict[str, Any] = {
        "postgres": {"ok": db_ok, "detail": db_detail},
        "qdrant": {"ok": qdrant_ok, "detail": qdrant_detail},
        "embeddings": {"ok": embed_ok, "detail": embed_detail},
    }
    # Anthropic und ElevenLabs werden nicht angepingt (jeder Aufruf kostet).
    # Ihr Konfigurationsstand ist für die Oberfläche trotzdem interessant.
    config = {
        "anthropic": settings.anthropic_configured,
        "elevenlabs": settings.elevenlabs_configured,
    }

    healthy = all(c["ok"] for c in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks, "config": config},
    )
