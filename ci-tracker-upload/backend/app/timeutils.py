"""Convención: todas las fechas se guardan en la DB como UTC *naive*.

SQLite no guarda zona horaria, así que normalizamos siempre a UTC al entrar
y convertimos a la zona local sólo al calcular estadísticas por día.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(dt: datetime) -> datetime:
    """Convierte cualquier datetime a UTC naive (asume UTC si viene sin zona)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def local_date(utc_naive: datetime, tz: ZoneInfo) -> date:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(tz).date()


def local_today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()
