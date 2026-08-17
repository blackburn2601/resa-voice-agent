"""Wissensbasis: Zerlegen, Aufnehmen und der Gleichlauf mit Qdrant."""

import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import Chunk, Document, IndexOutbox, OutboxOp, OutboxStatus, SourceType
from app.rag import indexer as indexer_module
from app.rag.ingest import ingest_text, split_markdown

MARKDOWN = """# Bürozeiten

Montags bis donnerstags von acht bis sechzehn Uhr.

## Rückrufe

Am nächsten Werktag.

## Feiertage

Das Büro bleibt geschlossen.
"""


class _FakeEmbeddings:
    dim = 1024

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            from app.rag.embeddings import EmbeddingError

            raise EmbeddingError("Ollama nicht erreichbar")
        return [[0.01] * self.dim for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.01] * self.dim


class _FakeStore:
    """Steht für Qdrant. `down=True` bildet einen Ausfall nach."""

    def __init__(self, down: bool = False) -> None:
        self.points: dict[uuid.UUID, dict] = {}
        self.down = down

    async def upsert(self, points) -> None:
        if self.down:
            raise ConnectionError("Qdrant nicht erreichbar")
        for point in points:
            self.points[uuid.UUID(point.id)] = point.payload

    async def delete(self, chunk_ids) -> None:
        if self.down:
            raise ConnectionError("Qdrant nicht erreichbar")
        for chunk_id in chunk_ids:
            self.points.pop(chunk_id, None)

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_embeddings(monkeypatch):
    def install(fail: bool = False) -> _FakeEmbeddings:
        client = _FakeEmbeddings(fail=fail)
        monkeypatch.setattr(indexer_module, "get_embeddings", lambda: client)
        return client

    return install


# -- Zerlegen --------------------------------------------------------------


def test_split_trennt_an_ueberschriften():
    pieces = split_markdown(MARKDOWN)
    assert len(pieces) == 3
    assert pieces[0].startswith("# Bürozeiten")
    assert pieces[1].startswith("## Rückrufe")


def test_split_teilt_zu_lange_abschnitte():
    langer = "# Titel\n\n" + "\n\n".join(f"Absatz {i}. " + "x" * 300 for i in range(20))
    pieces = split_markdown(langer)
    assert len(pieces) > 1
    assert all(len(p) <= 2000 for p in pieces)


def test_split_auf_leerem_text():
    assert split_markdown("   \n  ") == []


# -- Aufnehmen -------------------------------------------------------------


async def test_ingest_legt_chunks_und_indexauftraege_an(session):
    result = await ingest_text(
        session, text=MARKDOWN, title="Bürozeiten", source_type=SourceType.SEED
    )
    await session.commit()

    assert result.chunks == 3
    assert not result.skipped

    chunks = (await session.execute(select(Chunk))).scalars().all()
    assert len(chunks) == 3

    outbox = (await session.execute(select(IndexOutbox))).scalars().all()
    assert len(outbox) == 3
    assert all(e.op is OutboxOp.UPSERT for e in outbox)
    assert all(e.status is OutboxStatus.PENDING for e in outbox)
    # Der Auftrag zeigt auf denselben Chunk – daraus wird später die Punkt-ID.
    assert {e.chunk_id for e in outbox} == {c.id for c in chunks}


async def test_inhaltsgleiches_dokument_wird_uebersprungen(session):
    await ingest_text(session, text=MARKDOWN, title="A", source_type=SourceType.SEED)
    await session.commit()

    zweiter = await ingest_text(session, text=MARKDOWN, title="A", source_type=SourceType.SEED)
    await session.commit()

    assert zweiter.skipped
    assert zweiter.reason == "inhaltsgleich"
    count = (await session.execute(select(func.count()).select_from(Document))).scalar_one()
    assert count == 1


async def test_gleiche_datei_neuer_inhalt_ersetzt_die_alte(session):
    await ingest_text(
        session, text=MARKDOWN, title="A", source_type=SourceType.SEED, filename="a.md"
    )
    await session.commit()

    await ingest_text(
        session,
        text=MARKDOWN + "\n\n## Neu\n\nZusatz.",
        title="A",
        source_type=SourceType.SEED,
        filename="a.md",
    )
    await session.commit()

    documents = (await session.execute(select(Document))).scalars().all()
    assert len(documents) == 1, "Zwei Fassungen wären zwei Wahrheiten"

    # Für die alten Chunks muss ein Löschauftrag existieren, sonst blieben
    # verwaiste Vektoren in Qdrant zurück.
    deletes = (
        (await session.execute(select(IndexOutbox).where(IndexOutbox.op == OutboxOp.DELETE)))
        .scalars()
        .all()
    )
    assert len(deletes) == 3


# -- Gleichlauf mit Qdrant -------------------------------------------------


async def test_drain_schreibt_nach_qdrant(session, fake_embeddings):
    fake_embeddings()
    await ingest_text(session, text=MARKDOWN, title="A", source_type=SourceType.SEED)
    await session.commit()

    store = _FakeStore()
    result = await indexer_module.drain_outbox(session, store)

    assert result.upserted == 3
    assert result.failed == 0
    assert len(store.points) == 3

    offen = (
        await session.execute(
            select(func.count())
            .select_from(IndexOutbox)
            .where(IndexOutbox.status == OutboxStatus.PENDING)
        )
    ).scalar_one()
    assert offen == 0


async def test_qdrant_ausfall_verliert_nichts(session, fake_embeddings):
    """Der Kern des Outbox-Musters: Inhalt ist sicher, auch wenn der Index fehlt."""
    fake_embeddings()
    await ingest_text(session, text=MARKDOWN, title="A", source_type=SourceType.SEED)
    await session.commit()

    kaputt = _FakeStore(down=True)
    result = await indexer_module.drain_outbox(session, kaputt)

    assert result.upserted == 0
    assert result.failed == 3

    # Inhalt liegt vollständig in Postgres …
    assert (await session.execute(select(func.count()).select_from(Chunk))).scalar_one() == 3
    # … und die Aufträge stehen weiter offen.
    offen = (
        (
            await session.execute(
                select(IndexOutbox).where(IndexOutbox.status == OutboxStatus.PENDING)
            )
        )
        .scalars()
        .all()
    )
    assert len(offen) == 3
    assert all(e.attempts == 1 for e in offen)

    # Ist Qdrant zurück, holt derselbe Aufruf alles nach.
    heil = _FakeStore()
    nachgeholt = await indexer_module.drain_outbox(session, heil)
    assert nachgeholt.upserted == 3
    assert len(heil.points) == 3


async def test_embedding_ausfall_laesst_auftrag_offen(session, fake_embeddings):
    fake_embeddings(fail=True)
    await ingest_text(session, text=MARKDOWN, title="A", source_type=SourceType.SEED)
    await session.commit()

    result = await indexer_module.drain_outbox(session, _FakeStore())
    assert result.failed == 3

    offen = (
        await session.execute(
            select(func.count())
            .select_from(IndexOutbox)
            .where(IndexOutbox.status == OutboxStatus.PENDING)
        )
    ).scalar_one()
    assert offen == 3


async def test_wiederholtes_indizieren_verdoppelt_nichts(session, fake_embeddings):
    """Deterministische Punkt-IDs: derselbe Chunk überschreibt sich selbst."""
    fake_embeddings()
    await ingest_text(session, text=MARKDOWN, title="A", source_type=SourceType.SEED)
    await session.commit()

    store = _FakeStore()
    await indexer_module.drain_outbox(session, store)

    chunk_ids = (await session.execute(select(Chunk.id))).scalars().all()
    for chunk_id in chunk_ids:
        session.add(IndexOutbox(chunk_id=chunk_id, op=OutboxOp.UPSERT))
    await session.commit()
    await indexer_module.drain_outbox(session, store)

    assert len(store.points) == 3, "Qdrant darf keine Duplikate ansammeln"


async def test_auftrag_fuer_geloeschten_chunk_wird_abgeraeumt(session, fake_embeddings):
    fake_embeddings()
    await ingest_text(session, text=MARKDOWN, title="A", source_type=SourceType.SEED)
    await session.commit()

    verwaist = uuid.uuid4()
    session.add(IndexOutbox(chunk_id=verwaist, op=OutboxOp.UPSERT))
    await session.commit()

    result = await indexer_module.drain_outbox(session, _FakeStore())

    assert result.upserted == 3
    assert result.failed == 0
    offen = (
        await session.execute(
            select(func.count())
            .select_from(IndexOutbox)
            .where(IndexOutbox.status == OutboxStatus.PENDING)
        )
    ).scalar_one()
    assert offen == 0
