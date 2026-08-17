"""Embeddings über Ollama.

Bewusst hinter einem schmalen Protokoll: Der Wechsel auf einen anderen Anbieter
ist damit eine neue Klasse und eine Zeile in der Konfiguration, kein Umbau.

Modell ist bge-m3 – mehrsprachig mit gutem Deutsch, 1024 Dimensionen, 8192
Token Kontext. Anders als die e5-Familie braucht es *keine* `query:`/`passage:`-
Präfixe, Anfrage und Textabschnitt werden identisch eingebettet.
"""

import logging
from typing import Protocol, runtime_checkable

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Ollama verarbeitet eine Liste in einem Aufruf. Der Schnitt bei 32 hält die
# Anfragen klein genug, dass ein Fehlschlag nicht den ganzen Ingest kostet.
BATCH_SIZE = 32


@runtime_checkable
class EmbeddingProvider(Protocol):
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class EmbeddingError(RuntimeError):
    """Der Embedding-Dienst war nicht erreichbar oder hat unerwartet geantwortet."""


class OllamaEmbeddings:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.embedding_model
        self.dim = dim or settings.embedding_dim
        self._timeout = timeout

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": texts},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Ollama unter {self.base_url} nicht erreichbar: {exc}") from exc

        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError(
                f"Unerwartete Antwort von Ollama: {len(vectors or [])} Vektoren "
                f"für {len(texts)} Texte."
            )

        # Eine falsche Dimension würde in Qdrant erst beim Schreiben auffallen –
        # hier ist die Fehlermeldung noch aussagekräftig.
        actual = len(vectors[0])
        if actual != self.dim:
            raise EmbeddingError(
                f"Modell {self.model} liefert {actual} Dimensionen, konfiguriert sind "
                f"{self.dim}. EMBEDDING_DIM anpassen und neu indizieren."
            )
        return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            out.extend(await self._embed(batch))
            logger.debug("Embeddings: %d/%d", min(start + BATCH_SIZE, len(texts)), len(texts))
        return out

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text])
        return vectors[0]

    async def health(self) -> tuple[bool, str]:
        """Prüft Erreichbarkeit *und* dass das konfigurierte Modell vorhanden ist."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                names = {m.get("name") for m in response.json().get("models", [])}
        except httpx.HTTPError as exc:
            return False, f"nicht erreichbar: {exc}"

        if self.model not in names:
            return (
                False,
                f"Modell {self.model} fehlt (vorhanden: {', '.join(sorted(names)) or '–'})",
            )
        return True, f"{self.model}, {self.dim} Dimensionen"


def get_embeddings() -> EmbeddingProvider:
    return OllamaEmbeddings()
