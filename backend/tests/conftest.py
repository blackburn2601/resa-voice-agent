"""Test-Fixtures.

Tests laufen gegen eine **eigene Datenbank** (`<name>_test`), die zu Beginn der
Sitzung angelegt wird. Sonst sähen sie die Entwicklungsdaten – etwa die
eingelesene Wissensbasis – und würden je nach Zustand der Umgebung anders
ausgehen.

Innerhalb der Sitzung läuft jeder Test in einer Transaktion, die am Ende
zurückgerollt wird. Über `join_transaction_mode="create_savepoint"` werden auch
die `commit()`-Aufrufe *innerhalb* der Dienste zu Savepoints: Die Tests sehen
echtes Commit-Verhalten, hinterlassen aber nichts.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.db.models  # noqa: F401  – registriert alle Tabellen
from app.config import settings
from app.db.base import Base


def _test_database_url() -> str:
    base, _, name = settings.database_url.rpartition("/")
    return f"{base}/{name}_test"


TEST_DATABASE_URL = _test_database_url()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
async def _database() -> AsyncIterator[None]:
    admin = create_async_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    target = TEST_DATABASE_URL.rsplit("/", 1)[-1]
    async with admin.connect() as connection:
        from sqlalchemy import text

        exists = await connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target}
        )
        if not exists:
            await connection.execute(text(f'CREATE DATABASE "{target}"'))
    await admin.dispose()

    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        # expire_on_commit=False wie in der App: sonst löst jeder Attributzugriff
        # nach einem Commit synchrones Nachladen aus und scheitert im Async-Kontext.
        db = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield db
        finally:
            await db.close()
            await transaction.rollback()
    await engine.dispose()
