"""
Application settings, loaded from the .env file via pydantic-settings.

Import get_settings() wherever you need config values.  The Settings object
is constructed lazily on first call and cached for the process lifetime.

All environment variable names match the keys in .env.example exactly.
Twilio credentials are Optional because the pipeline can run in
ingest-only mode without WhatsApp delivery configured.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently drop unknown env vars (e.g. COLOSSUS_EMAIL)
    )

    # AI APIs — Gemini is the active summariser; Anthropic kept for future use
    gemini_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None

    # Optional — delivery layer only
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_whatsapp_from: Optional[str] = None
    my_whatsapp_number: Optional[str] = None

    # Optional — Taddy podcast API
    taddy_user_id: Optional[str] = None
    taddy_api_key: Optional[str] = None

    # Optional — Groq cloud transcription (whisper-large-v3).
    # When set, the transcriber uses Groq as the primary backend and falls back
    # to local Whisper only if Groq fails after retries.  Leave empty to run
    # local Whisper exclusively.
    groq_api_key: str = ""

    # Optional — Spotify export script
    spotify_client_id: Optional[str] = None
    spotify_client_secret: Optional[str] = None

    # Optional — Telegram bot notifications (post-pipeline summaries)
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # Tunable defaults
    anthropic_model: str = "claude-sonnet-4-6"
    db_path: str = "./data/digest.db"

    # CORS allowed origins for the web API (comma-separated)
    cors_origins: str = "http://localhost:5173,http://localhost:3000"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application settings singleton."""
    return Settings()
