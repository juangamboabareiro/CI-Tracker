"""Configuración centralizada. Todo lo configurable sale de variables de entorno / .env."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "ci_tracker.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # SecretStr evita que la key aparezca en logs o reprs por accidente.
    youtube_api_key: SecretStr = SecretStr("")
    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    timezone: str = "UTC"
    log_level: str = "INFO"
    # Tu idioma nativo: permite distinguir "subtítulos en mi idioma" de "otro idioma".
    native_language: str = "es"
    # Si un video no tiene puntaje, usar el promedio de los puntajes manuales de su canal.
    channel_comprehensibility_fallback: bool = True
    # Un día cuenta para el streak si se vio al menos esto (v0.5).
    streak_threshold_seconds: float = 60.0

    # Reglas para reconstruir segmentos a partir de eventos (ver segments.py).
    max_sample_gap_seconds: float = 120.0
    position_tolerance_seconds: float = 2.0
    rate_slack: float = 1.25

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
