"""Kommandozeile für Betrieb und Prüfung.

python -m app.cli embed-check "Wasserschaden im Keller"
python -m app.cli reindex [--fresh]
python -m app.cli repair-index
python -m app.cli search "Wann habt ihr auf?"
python -m app.cli create-user dev@example.de
python -m app.cli set-oncall "Name" mail@example.de [+49...]
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db.models import Document, OnCallContact, User
from app.db.session import SessionFactory
from app.rag import indexer, ingest, retrieve
from app.rag.embeddings import OllamaEmbeddings
from app.rag.vectorstore import VectorStore
from app.voice.tts import ElevenLabsTTS, VoiceService


async def cmd_embed_check(text: str) -> int:
    """Prüft die Embedding-Kette, bevor irgendetwas indiziert wird."""
    client = OllamaEmbeddings()
    ok, detail = await client.health()
    print(f"Dienst:  {client.base_url}")
    print(f"Status:  {'erreichbar' if ok else 'NICHT erreichbar'} – {detail}")
    if not ok:
        return 1

    vector = await client.embed_query(text)
    print(f"Text:    {text!r}")
    print(f"Vektor:  {len(vector)} Dimensionen")
    print(f"Anfang:  {[round(v, 5) for v in vector[:5]]}")
    if len(vector) != settings.embedding_dim:
        print(f"FEHLER: erwartet wurden {settings.embedding_dim} Dimensionen.")
        return 1
    print("\nPasst zur konfigurierten Collection-Größe.")
    return 0


async def cmd_reindex(fresh: bool) -> int:
    async with SessionFactory() as session:
        if fresh:
            removed = await ingest.purge_all(session)
            print(f"{removed} Dokument(e) entfernt.")

        results = await ingest.ingest_directory(session, Path(settings.knowledge_dir))
        if not results:
            print(f"Keine Dateien in {settings.knowledge_dir} gefunden.")
        for result in results:
            if result.skipped:
                print(f"  übersprungen  {result.title} ({result.reason})")
            else:
                print(f"  aufgenommen   {result.title} – {result.chunks} Abschnitt(e)")

        print("\nIndiziere …")
        drained = await indexer.drain_outbox(session)
        print(f"  {drained}")

        await ingest.mark_indexed(
            session,
            [r.document_id for r in results if r.document_id and not r.skipped],
        )
        return 1 if drained.failed else 0


async def cmd_repair() -> int:
    async with SessionFactory() as session:
        report = await indexer.repair(session)
    print(f"Postgres:        {report.in_postgres} Abschnitt(e)")
    print("Vorher —")
    print(f"  fehlend:       {report.missing_before}")
    print(f"  verwaist:      {report.orphaned_before}")
    print("Behoben —")
    print(f"  indiziert:     {report.indexed}")
    print(f"  entfernt:      {report.removed}")
    print("Nachher —")
    print(f"  fehlend:       {report.missing_after}")
    print(f"  verwaist:      {report.orphaned_after}")
    print("\nStand:", "synchron" if report.in_sync else "ABWEICHUNG – erneut ausführen")
    return 0 if report.in_sync else 1


async def cmd_search(query: str) -> int:
    passages = await retrieve.search(query)
    if not passages:
        print("Kein Treffer über der Mindest-Ähnlichkeit.")
        return 0
    for passage in passages:
        print(f"\n--- {passage.title}  (Ähnlichkeit {passage.score:.3f}) ---")
        print(passage.content.strip()[:400])
    return 0


async def cmd_create_user(email: str) -> int:
    from app.security import hash_password

    password = getpass.getpass("Passwort: ")
    if len(password) < 10:
        print("Das Passwort sollte mindestens zehn Zeichen haben.")
        return 1
    if password != getpass.getpass("Wiederholen: "):
        print("Die Eingaben stimmen nicht überein.")
        return 1

    async with SessionFactory() as session:
        existing = (
            (await session.execute(select(User).where(User.email == email))).scalars().first()
        )
        if existing is not None:
            existing.password_hash = hash_password(password)
            await session.commit()
            print(f"Passwort für {email} aktualisiert.")
            return 0
        session.add(User(email=email, password_hash=hash_password(password)))
        await session.commit()
    print(f"Benutzer {email} angelegt.")
    return 0


async def cmd_set_oncall(name: str, email: str, phone: str | None) -> int:
    async with SessionFactory() as session:
        for contact in (await session.execute(select(OnCallContact))).scalars().all():
            contact.active = False
        session.add(OnCallContact(name=name, email=email, phone=phone, active=True))
        await session.commit()
    print(f"Bereitschaft: {name} <{email}>" + (f", {phone}" if phone else ""))
    return 0


async def cmd_say(text: str, out: str) -> int:
    """Sprachausgabe isoliert prüfen, bevor die Oberfläche dranhängt."""
    client = ElevenLabsTTS()
    ok, detail = await client.health()
    print(f"Stimme:  {'ok' if ok else 'FEHLER'} – {detail}")
    if not ok:
        return 1

    import time

    async with SessionFactory() as session:
        service = VoiceService(session, client)
        start = time.monotonic()
        audio, cached = await service.speak(text)
        dauer = time.monotonic() - start

    Path(out).write_bytes(audio)
    quelle = "aus dem Cache" if cached else "neu erzeugt"
    print(f"Audio:   {len(audio)} Bytes, {quelle}, {dauer * 1000:.0f} ms")
    print(f"Datei:   {out}")
    return 0


async def cmd_status() -> int:
    async with SessionFactory() as session:
        documents = (await session.execute(select(Document))).scalars().all()
    store = VectorStore()
    try:
        vectors = await store.count()
    finally:
        await store.close()

    print(f"Dokumente:  {len(documents)}")
    for document in documents:
        print(f"  [{document.source_type.value:6}] {document.title}")
    print(f"Vektoren:   {vectors}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Resa – Betriebsbefehle")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("embed-check", help="Embedding-Dienst und Dimension prüfen")
    p.add_argument("text", nargs="?", default="Wasserschaden im Keller")

    p = sub.add_parser("reindex", help="Wissensverzeichnis einlesen und indizieren")
    p.add_argument("--fresh", action="store_true", help="vorher alles verwerfen")

    sub.add_parser("repair-index", help="Postgres gegen Qdrant abgleichen")
    sub.add_parser("status", help="Dokumente und Vektoren zählen")

    p = sub.add_parser("say", help="Sprachausgabe prüfen")
    p.add_argument("text")
    p.add_argument("--out", default="/app/data/resa-probe.mp3")

    p = sub.add_parser("search", help="Wissensbasis abfragen")
    p.add_argument("query")

    p = sub.add_parser("create-user", help="Dashboard-Benutzer anlegen oder Passwort setzen")
    p.add_argument("email")

    p = sub.add_parser("set-oncall", help="Bereitschaftskontakt setzen")
    p.add_argument("name")
    p.add_argument("email")
    p.add_argument("phone", nargs="?")

    args = parser.parse_args()

    match args.command:
        case "embed-check":
            return asyncio.run(cmd_embed_check(args.text))
        case "reindex":
            return asyncio.run(cmd_reindex(args.fresh))
        case "repair-index":
            return asyncio.run(cmd_repair())
        case "status":
            return asyncio.run(cmd_status())
        case "say":
            return asyncio.run(cmd_say(args.text, args.out))
        case "search":
            return asyncio.run(cmd_search(args.query))
        case "create-user":
            return asyncio.run(cmd_create_user(args.email))
        case "set-oncall":
            return asyncio.run(cmd_set_oncall(args.name, args.email, args.phone))
    return 2


if __name__ == "__main__":
    sys.exit(main())
