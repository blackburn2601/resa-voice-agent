"""Zentrale Konfiguration. Alle Werte kommen aus der Umgebung bzw. aus .env."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_env: Literal["dev", "prod"] = "dev"
    secret_key: str = "unsicherer-entwicklungsschluessel"

    # --- PostgreSQL --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://resa:resa@db:5432/resa"

    # --- Qdrant ------------------------------------------------------------
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "resa_knowledge"

    # --- Embeddings --------------------------------------------------------
    ollama_base_url: str = "http://192.168.178.180:11434"
    embedding_model: str = "bge-m3:latest"
    # Gegen die laufende Instanz verifiziert. Eine Änderung erzwingt eine
    # Neuindizierung, weil die Qdrant-Collection auf diese Größe festgelegt ist.
    embedding_dim: int = 1024

    # --- Anthropic ---------------------------------------------------------
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"

    # --- ElevenLabs --------------------------------------------------------
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_model: str = "eleven_flash_v2_5"

    # --- E-Mail ------------------------------------------------------------
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "resa@musterhaus-verwaltung.de"
    smtp_use_tls: bool = False

    # --- Frontend ----------------------------------------------------------
    # NoDecode: sonst versucht pydantic-settings, den Wert als JSON zu lesen,
    # bevor der Validator unten zum Zug kommt.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["https://localhost"]
    )
    public_host: str = "localhost"

    # --- Wissensbasis ------------------------------------------------------
    knowledge_dir: str = "/app/data/knowledge"
    upload_dir: str = "/app/uploads"
    max_upload_bytes: int = 20 * 1024 * 1024

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Erlaubt CORS_ORIGINS als kommaseparierte Liste in der .env."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @staticmethod
    def _is_real(value: str | None) -> bool:
        """Der Platzhalter aus .env.example darf nicht als gesetzt durchgehen –
        sonst meldet der Health-Check „konfiguriert" und der erste Aufruf
        scheitert mit 401."""
        if not value:
            return False
        return not value.strip().endswith("...")

    @property
    def anthropic_configured(self) -> bool:
        return self._is_real(self.anthropic_api_key)

    @property
    def elevenlabs_configured(self) -> bool:
        return self._is_real(self.elevenlabs_api_key) and bool(self.elevenlabs_voice_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
