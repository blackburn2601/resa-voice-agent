"""FastAPI-Anwendung."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, channel_web, conversations, health, knowledge, settings_api
from app.config import settings
from app.db.session import SessionFactory, engine
from app.rag.vectorstore import VectorStore

logging.basicConfig(
    level=logging.INFO if settings.is_dev else logging.WARNING,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("resa")


async def _outbox_sweeper(interval: float = 120.0) -> None:
    """Holt liegengebliebene Indexaufträge nach.

    Der Normalfall ist, dass direkt nach dem Commit indiziert wird. Diese
    Schleife greift nur, wenn Qdrant oder der Embedding-Dienst zwischenzeitlich
    weg waren – dann bleibt der Auftrag offen und wird hier nachgeholt, ohne
    dass jemand von Hand eingreifen muss.
    """
    from app.rag.indexer import drain_outbox

    while True:
        try:
            async with SessionFactory() as session:
                result = await drain_outbox(session)
            if result.upserted or result.deleted:
                logger.info("Outbox nachgearbeitet: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 – die Schleife darf nie sterben
            logger.exception("Outbox-Sweep gescheitert")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Die Collection wird beim Start angelegt, nicht beim ersten Suchtreffer –
    # ein Konfigurationsfehler soll sofort auffallen, nicht mitten im Gespräch.
    store = VectorStore()
    try:
        created = await store.ensure_collection()
        logger.info(
            "Qdrant bereit (Collection '%s'%s)",
            store.collection,
            ", neu angelegt" if created else "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Qdrant nicht bereit: %s", exc)
    finally:
        await store.close()

    sweeper = asyncio.create_task(_outbox_sweeper())

    yield

    sweeper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweeper
    await engine.dispose()


app = FastAPI(
    title="Resa – KI-Assistentin für Hausverwaltungen",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.is_dev else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.is_dev else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
# Der WebSocket hängt bewusst nicht unter /api – Caddy leitet /ws/* getrennt weiter.
app.include_router(channel_web.router)
