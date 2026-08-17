# Resa — KI-Assistentin für Hausverwaltungen

Jemand meldet einen Wasserschaden. Resa erkennt den Notfall, **alarmiert sofort die
Bereitschaft** — bevor sie überhaupt nach dem Namen fragt —, nimmt den Fall strukturiert
auf und schreibt ihn ins Dashboard, wo er noch während des Gesprächs erscheint.

Ein Referenzprojekt: bewusst überschaubar, aber ohne Abkürzungen an den Stellen, die in
echten Systemen wehtun.

## Was es zeigt

- **Claude Opus 5** mit Werkzeugen, Streaming und Prompt-Caching
- **RAG** über **Qdrant** — Auskünfte kommen aus der Wissensbasis, nicht aus dem Prompt
- **Lokale Embeddings** über Ollama (`bge-m3`) — keine Kosten, keine Daten nach außen
- **PostgreSQL** als Quelle der Wahrheit, mit **Outbox-Muster** für den Gleichlauf mit Qdrant
- **ElevenLabs**, satzweise gestreamt und zwischengespeichert
- **Vue 3** mit Live-Aktualisierung über SSE
- **FastAPI** mit Auth, WebSocket-Kanal und Alembic-Migrationen

## Schnellstart

Voraussetzungen: Docker, und eine erreichbare Ollama-Instanz mit `bge-m3`
(oder das mitgelieferte Compose-Profil, siehe unten).

```bash
cp .env.example .env
# ANTHROPIC_API_KEY und ELEVENLABS_API_KEY eintragen,
# OLLAMA_BASE_URL auf die eigene Instanz zeigen lassen
sed -i '' "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env

docker compose up -d
docker compose exec api alembic upgrade head

# Prüfen, bevor irgendetwas indiziert wird — die Vektorgröße ist danach
# nur noch mit Neuindizierung änderbar
docker compose exec api python -m app.cli embed-check

docker compose exec api python -m app.cli reindex
docker compose exec api python -m app.cli create-user dein@name.de
docker compose exec api python -m app.cli set-oncall "Name" bereitschaft@example.de

cd frontend && npm install && npm run build
```

Danach: **https://localhost** (Caddy stellt ein eigenes Zertifikat aus, die Warnung ist
erwartbar). E-Mails landen unter **http://localhost:8025** in Mailpit.

Für die Frontend-Entwicklung stattdessen `npm run dev` — das läuft auf `localhost` und ist
damit ein sicherer Kontext, den die Spracherkennung des Browsers voraussetzt.

### Ohne eigene Ollama-Instanz

```bash
docker compose --profile local-embeddings up -d
docker compose exec ollama ollama pull bge-m3
# in .env: OLLAMA_BASE_URL=http://ollama:11434
```

## Aufbau

```
Browser                                    Backend
  SpeechRecognition ──→ Text ──→ WS ──→ ConversationService
                                              │
                                              ├──→ Claude (Opus 5, streaming)
                                              ├──→ Qdrant (Vektorsuche)
                                              └──→ ElevenLabs (satzweise)
  Audio-Wiedergabe  ←── MP3 ←── WS ←──────────┘

Postgres ── Quelle der Wahrheit: Gespräche, Dokumente, Chunks, Outbox
Qdrant   ── Ableitung: Vektoren + Suchmetadaten
Ollama   ── bge-m3, erzeugt die Embeddings
```

`ConversationService` kennt weder Browser noch Sprachausgabe: Text rein, Text raus. Alles
Kanalspezifische liegt im Adapter — deshalb wäre Telefonie später ein zweiter Adapter, kein
Umbau.

## Entscheidungen, die sich beim Messen ergeben haben

**Der Ähnlichkeits-Schwellwert der Vektorsuche liegt bei 0,30, nicht bei 0,45.** Gemessen an
echten Fragen: „Wann ist das Büro erreichbar?" erreicht 0,68 — das umgangssprachliche „Wann
habt ihr auf?" nur 0,362, auf demselben, richtigen Dokument. bge-m3 rankt zuverlässig, aber
die Absolutwerte hängen stark an der Formulierung. Ein Schwellwert von 0,45 hätte
ausgerechnet die häufigste Frage unbeantwortet gelassen. Die Grenze filtert deshalb nur
noch offensichtlichen Müll; schwache Treffer werden als solche gekennzeichnet, und über die
Brauchbarkeit urteilt das Modell, das den Text liest.

**Zwischenansagen beim Werkzeugstart.** Ein Zug mit Werkzeugaufruf braucht zwei
Modellaufrufe, und der erste liefert keinen Text — im Notfall wären das fünf Sekunden
Stille. Eine kurze, zwischengespeicherte Ansage überbrückt das. Ausgelöst wird sie beim
`content_block_start` des Werkzeugaufrufs, nicht am Ende des Modellaufrufs; das ist der
Unterschied zwischen 3,3 und 1,2 Sekunden bis zum ersten Ton.

| | erster Ton |
|---|---|
| ohne Überbrückung | 5403 ms |
| Ansage beim Werkzeugstart | 3623 ms |
| … aus dem Cache | 3321 ms |
| Werkzeug früh im Stream erkannt | **1208 ms** |

**Thinking bleibt an, `effort` steht auf `low`.** Gemessen 1066 ms bis zum ersten Text gegen
1551 ms bei `medium`. Thinking abzuschalten wäre der falsche Hebel: Ohne Thinking schreibt
das Modell Werkzeugaufrufe gelegentlich als sichtbaren Text — der Aufruf läuft dann nie.

**Die Alarmierung meldet zurück, was wirklich zugestellt wurde.** Resa sagt „Der Notdienst
ist informiert" nur bei bestätigter Zustellung, sonst nennt sie offen das Scheitern und
verweist auf 112. Eine behauptete Alarmierung, die nie ankam, wäre schlimmer als gar keine.

## Betrieb

```bash
docker compose exec api python -m app.cli embed-check   # Embedding-Dienst und Dimension
docker compose exec api python -m app.cli reindex       # Wissensverzeichnis einlesen
docker compose exec api python -m app.cli repair-index  # Postgres gegen Qdrant abgleichen
docker compose exec api python -m app.cli status        # Dokumente und Vektoren zählen
docker compose exec api python -m app.cli search "..."  # Wissensbasis abfragen
docker compose exec api python -m app.cli say "..."     # Sprachausgabe prüfen
```

## Tests

```bash
docker compose exec api python -m pytest tests/ -q      # 39 Tests, gegen eine eigene Testdatenbank
docker compose exec api python tools/ws_probe.py        # Durchstich über den WebSocket
cd frontend && npm run typecheck
```

Der interessanteste Test ist der Ausfall:

```bash
docker compose stop qdrant
docker compose exec api python -m app.cli reindex --fresh   # verliert nichts
docker compose start qdrant
docker compose exec api python -m app.cli repair-index      # holt alles nach
```

## Bewusst nicht enthalten

- **Telefonie.** Die Anbindung an eine echte Rufnummer wäre ein zweiter Adapter neben
  `channel_web.py` — bei einem Anbieter mit Text-rein/Text-raus-Muster (Telnyx Conversation
  Relay, sipgate flow) übernähme dessen Dienst dann auch Spracherkennung und -ausgabe.
- **SMS-Alarmierung.** Die Schnittstelle steht (`escalation/channels.py`), die Umsetzung
  fehlt: Deutsche Festnetznummern versenden in der Regel keine SMS, und ein alphanumerischer
  Absender ist teils registrierungspflichtig.
- **Hybrid-Suche.** Qdrant kann Sparse-Vektoren; über Ollama kommen nur die dichten an.
- **Mehrere Worker.** Der Live-Strom ist ein Verteiler im Prozess. Sobald mehr als ein
  Worker läuft, tritt Postgres LISTEN/NOTIFY an seine Stelle — die Schnittstelle bleibt.

## Lizenz

Frei verwendbar als Portfolio- und Referenzprojekt.
