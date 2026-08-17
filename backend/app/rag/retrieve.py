"""Abruf aus der Wissensbasis."""

import logging
from dataclasses import dataclass

from app.rag.embeddings import EmbeddingError, get_embeddings
from app.rag.vectorstore import VectorStore

logger = logging.getLogger(__name__)

TOP_K = 4

# Gemessen an echten Anfragen: bge-m3 rankt zuverlässig richtig, aber die
# absoluten Werte hängen stark an der Formulierung. "Wann ist das Büro
# erreichbar?" landet bei 0,68, das umgangssprachliche "Wann habt ihr auf?" bei
# 0,36 – beide auf demselben, richtigen Dokument. Ein Schwellwert von 0,45 hätte
# die zweite Frage unbeantwortet gelassen.
#
# Deshalb: die Grenze filtert nur offensichtlichen Müll. Über die Brauchbarkeit
# entscheidet das Modell, das den Text tatsächlich liest – dafür werden schwache
# Treffer im Ergebnis ausdrücklich als solche gekennzeichnet.
SCORE_THRESHOLD = 0.30
WEAK_MATCH = 0.45


@dataclass(slots=True)
class Passage:
    title: str
    content: str
    score: float


async def search(query: str, limit: int = TOP_K) -> list[Passage]:
    store = VectorStore()
    try:
        vector = await get_embeddings().embed_query(query)
        hits = await store.search(vector, limit=limit, score_threshold=SCORE_THRESHOLD)
    except EmbeddingError:
        logger.exception("Embedding für die Suchanfrage fehlgeschlagen")
        raise
    finally:
        await store.close()

    return [Passage(title=h.title, content=h.content, score=h.score) for h in hits]


def format_passages(passages: list[Passage]) -> str:
    """Aufbereitung für das tool_result – mit Quellenangabe, damit Resa sie nennen kann."""
    if not passages:
        return (
            "Kein passender Eintrag in der Wissensbasis. "
            "Sage der Person offen, dass du das nicht beantworten kannst, und biete einen "
            "Rückruf durch das Team an."
        )

    parts = []
    for passage in passages:
        marker = " (schwacher Treffer)" if passage.score < WEAK_MATCH else ""
        parts.append(f"[{passage.title}{marker}]\n{passage.content.strip()}")

    hinweis = (
        "\n\nPrüfe, ob die Textstellen die Frage wirklich beantworten. Wenn nicht - "
        "besonders bei schwachen Treffern - sage offen, dass du es nicht weißt, "
        "statt etwas zusammenzureimen."
    )
    return "\n\n---\n\n".join(parts) + hinweis
