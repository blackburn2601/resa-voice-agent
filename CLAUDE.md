# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & run

```bash
cp .env.example .env          # ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, OLLAMA_BASE_URL
docker compose up -d
docker compose exec api alembic upgrade head
docker compose exec api python -m app.cli reindex
cd frontend && npm install && npm run build   # Caddy serviert frontend/dist
```

`docker-compose.override.yml` gilt nur für die Entwicklung: Bind-Mount statt Rebuild,
`--reload`, Port 8000 direkt, Testwerkzeuge im Image (`INSTALL_DEV=true`). Für einen
produktionsnahen Lauf `docker compose -f docker-compose.yml ...` verwenden.

```bash
docker compose exec api python -m pytest tests/ -q     # 39 Tests
docker compose exec api ruff check app tests tools
docker compose exec api python tools/ws_probe.py       # Durchstich mit echtem Modell und TTS
cd frontend && npm run typecheck
```

Tests laufen gegen eine **eigene Datenbank** (`resa_test`), die `tests/conftest.py` beim
ersten Lauf anlegt. Ohne das sähen sie die eingelesene Wissensbasis der Entwicklungsumgebung.

## Language

Durchgehend deutsch: Systemprompt, Werkzeugbeschreibungen, UI-Texte, Fehlermeldungen,
Kommentare, Testnamen. Bezeichner bleiben englisch. Neue Texte und Kommentare auf Deutsch.

## Architecture

```
Browser ──WS──→ ConversationService ──→ Claude / Qdrant / ElevenLabs
Postgres = Quelle der Wahrheit · Qdrant = Ableitung · Ollama = Embeddings
```

Punkte, die sich über mehrere Dateien erstrecken und leicht falsch gemacht werden:

- **`ConversationService` (`app/core/conversation.py`) kennt keinen Kanal.** Text rein, Text
  raus. Wer Sprachausgabe, WebSocket-Rahmen oder Wiedergabe anfassen will, arbeitet in
  `app/api/channel_web.py` — nicht im Kern. Ein Telefonie-Adapter käme daneben.

- **Die Nutzernachricht wird gespeichert, *bevor* das Modell läuft.** Eine Notfall-Alarmierung
  feuert mitten im Zug und schickt den Verlauf mit; in der alten Reihenfolge fehlten dort
  ausgerechnet die Worte der anrufenden Person. Diese Reihenfolge nicht umdrehen.

- **`messages.blocks` (JSONB) ist der Verlauf fürs Modell, `messages.content` der für
  Menschen.** Werkzeugaufrufe und -ergebnisse haben leeren `content` und erscheinen deshalb
  nicht im Transkript, gehen aber vollständig an die API zurück. Ohne `blocks` wüsste das
  Modell im nächsten Zug nicht, dass es bereits alarmiert hat.

- **Nichts schreibt an der Outbox vorbei nach Qdrant.** `ingest.py` legt Dokument, Chunks und
  `index_outbox`-Einträge in *einer* Transaktion an; `indexer.drain_outbox` ist der einzige
  Weg nach Qdrant. Nur dadurch ist `repair-index` aussagekräftig. Die Qdrant-Punkt-ID *ist*
  die Chunk-UUID — Wiederholen erzeugt deshalb nie Duplikate.

- **`drain_outbox` läuft mit einem ID-Cursor, nicht nur über den Status.** Ein gescheiterter
  Auftrag bleibt `pending`; ohne Cursor zöge derselbe Durchlauf ihn sofort wieder und
  verbrauchte alle fünf Versuche in Sekunden statt über die Zeit.

- **Die Alarmierung ist idempotent über eine Unique-Constraint auf `escalations.conversation_id`.**
  Der Rückgabetext des Werkzeugs nennt nur tatsächlich zugestellte Kanäle — Resa darf keine
  Alarmierung behaupten, die nicht ankam. Beim Ändern von `EscalationOutcome.as_tool_result`
  daran denken.

- **Ein Werkzeug-Zug braucht zwei Modellaufrufe.** `ToolStarted` wird beim
  `content_block_start` des Streams ausgelöst, nicht nach `get_final_message()` — das ist der
  Unterschied zwischen 3,3 s und 1,2 s bis zum ersten Ton. `TOOL_FILLERS` in `prompts.py`
  überbrückt die Lücke und ist bewusst immer gleich formuliert, damit der Audio-Cache greift.

- **Enums werden viermal berührt.** Ein neues Feld in `log_intake` braucht: das Schema in
  `core/tools.py`, das Pydantic-Modell `IntakeArgs`, das ORM-Modell `IntakeRecord`, eine
  Alembic-Migration und die Ausgabe in `api/conversations.py` plus Frontend-Typ.

## Model & retrieval

`claude-opus-5`, `thinking={"type": "adaptive"}`, `output_config={"effort": "low"}` über
`extra_body`. **Thinking nicht abschalten** — ohne Thinking schreibt das Modell
Werkzeugaufrufe gelegentlich als sichtbaren Text, der Aufruf läuft dann nie. `max_tokens`
großzügig, weil Thinking mit hineinzählt. `cache_control` auf dem System-Block; über ein
Gespräch mit zehn Turns spart das den Großteil der Eingabekosten.

Der Ähnlichkeits-Schwellwert in `rag/retrieve.py` steht bewusst auf **0,30**, nicht höher:
Umgangssprachliche Fragen erreichen mit bge-m3 nur um 0,36, während dieselbe Frage förmlich
gestellt bei 0,68 liegt — das Ranking stimmt, die Absolutwerte schwanken. Schwache Treffer
werden markiert, entscheiden soll das Modell. Wer diesen Wert anhebt, sollte vorher gegen
echte Fragen messen.

Embedding-Dimension (1024) ist in der Qdrant-Collection festgeschrieben und nur mit
Neuindizierung änderbar. `app.cli embed-check` prüft das vor dem ersten Indizieren.

## Gotchas

- **Kein pgvector.** Postgres ist Standard-Postgres; Vektoren liegen ausschließlich in Qdrant.
- **`qdrant-client` und das Qdrant-Image zusammen anheben** — der Client warnt bei mehr als
  einer Minor-Version Abstand.
- **ElevenLabs-Schlüssel sind fein granuliert.** `ElevenLabsTTS.health()` prüft deshalb über
  einen echten Sprechaufruf, nicht über `/v1/voices/{id}` — dafür fehlt engen Schlüsseln die
  Berechtigung. Im kostenlosen Tarif sind Bibliotheks-Stimmen per API gesperrt (HTTP 402);
  nutzbar sind einzelne vorinstallierte, etwa `EXAVITQu4vr4xnSDxMaL`.
- **`health()` gehört nicht in `/api/health`** — der läuft als Container-Healthcheck im
  Sekundentakt, und jeder ElevenLabs-Aufruf kostet Zeichen.
- **Web Speech API nur in Chrome und Edge.** Die Oberfläche erkennt das und fällt auf
  Texteingabe zurück; die Sprachausgabe funktioniert überall.
- **Der SSE-Verteiler in `app/events.py` lebt im Prozess.** Mit mehreren Workern sähe jeder
  nur seine eigenen Ereignisse.
