"""Konfiguration. Alle Werte kommen aus Umgebungsvariablen (Präfix GOLF_).

In Production verweigert die App den Start, wenn Secrets fehlen, zu kurz
sind oder einem bekannten Beispielwert entsprechen. Es gibt bewusst keinen
eingebauten Fallback-Secret.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Werte, die in .env.example oder Doku stehen und niemals produktiv sein dürfen.
KNOWN_INSECURE_VALUES = {
    "",
    "changeme",
    "change-me",
    "secret",
    "password",
    "bitte-aendern",
    "dev-only-insecure",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOLF_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://golf:golf@localhost:5432/golf"

    # Pfeffer für Session-Token-Hashes. Pflicht in Production.
    session_secret: str = "dev-only-insecure"
    session_ttl_hours: int = 12
    cookie_secure: bool = True

    # Uploads
    storage_dir: Path = Path("./data/storage")
    upload_max_bytes: int = Field(default=15 * 1024 * 1024, ge=1024)
    upload_chunk_bytes: int = 64 * 1024
    # Temp-Dateien älter als das gelten nach einem Absturz als verwaist.
    orphan_grace_seconds: int = 3600

    # Platzkatalog (handicap.report). Leer = Katalog abgeschaltet.
    course_catalog_url: str = "https://handicap.report/api/v1/courses.php"
    course_catalog_ttl_seconds: int = Field(default=3600, ge=60)

    # Login-Schutz
    login_max_failures: int = 5
    login_lock_minutes: int = 15

    # Hinter einem Reverse Proxy: nur diesen Proxys X-Forwarded-* glauben.
    trusted_proxies: list[str] = ["127.0.0.1"]

    @model_validator(mode="after")
    def _check_production_secrets(self) -> "Settings":
        if self.env == "production":
            if self.session_secret.lower() in KNOWN_INSECURE_VALUES or len(self.session_secret) < 32:
                raise ValueError(
                    "GOLF_SESSION_SECRET muss in Production gesetzt sein (mind. 32 Zeichen, kein Beispielwert)."
                )
            if not self.cookie_secure:
                raise ValueError("GOLF_COOKIE_SECURE darf in Production nicht false sein.")
            if any(f":{v}@" in self.database_url for v in ("golf", "changeme", "password")):
                raise ValueError("Standard-Datenbankpasswort in Production nicht erlaubt.")
        return self

    @property
    def tmp_dir(self) -> Path:
        # Temp liegt im selben Dateisystem wie storage, damit os.replace atomar ist.
        return self.storage_dir / ".tmp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
