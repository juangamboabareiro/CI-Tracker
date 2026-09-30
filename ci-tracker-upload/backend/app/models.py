"""Modelo de datos (SQLAlchemy 2.0).

Flujo de datos:
    WatchEvent (crudo, append-only, lo que manda la extensión)
        -> se reprocesa por sesión ->
    WatchedSegment (tramos [start, end] del video efectivamente reproducidos)
        -> se agregan ->
    ViewingSession (totales de la sesión) y estadísticas.

Guardar los eventos crudos permite recalcular segmentos si mejoramos el algoritmo.
Todo es SQL estándar (strings en lugar de ENUMs nativos) para migrar fácil a PostgreSQL.
"""

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .timeutils import utcnow


class Language(Base):
    __tablename__ = "languages"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # ISO 639-1 cuando existe
    name: Mapped[str] = mapped_column(String(64))


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("source", "source_video_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="youtube")
    source_video_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(300))
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    channel_name: Mapped[str | None] = mapped_column(String(200))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None]
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    detected_language: Mapped[str | None] = mapped_column(String(16))  # metadata de YouTube
    caption_language: Mapped[str | None] = mapped_column(String(16))  # subtítulos automáticos = audio (v0.6)
    text_language: Mapped[str | None] = mapped_column(String(16))  # detector por título/descr. (v0.6)
    text_language_confidence: Mapped[float | None] = mapped_column(Float)
    metadata_fetched_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    settings: Mapped["UserVideoSettings | None"] = relationship(back_populates="video")
    sessions: Mapped[list["ViewingSession"]] = relationship(back_populates="video")


class ViewingSession(Base):
    """Una sesión = un video en una carga de página (la genera la extensión)."""

    __tablename__ = "viewing_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_session_id: Mapped[str] = mapped_column(String(64), unique=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    wall_clock_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    content_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    playback_speed: Mapped[float] = mapped_column(Float, default=1.0)  # promedio ponderado

    video: Mapped[Video] = relationship(back_populates="sessions")
    segments: Mapped[list["WatchedSegment"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="WatchedSegment.started_at"
    )


class WatchEvent(Base):
    __tablename__ = "watch_events"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)  # idempotencia de reintentos

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("viewing_sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime]
    position_seconds: Mapped[float] = mapped_column(Float)
    playback_rate: Mapped[float] = mapped_column(Float)
    paused: Mapped[bool] = mapped_column(Boolean)
    subtitles_on: Mapped[bool | None] = mapped_column(Boolean)  # None = no detectado (v0.2)
    subtitle_language: Mapped[str | None] = mapped_column(String(16))
    received_at: Mapped[datetime] = mapped_column(default=utcnow)


class WatchedSegment(Base):
    __tablename__ = "watched_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("viewing_sessions.id"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    start_second: Mapped[float] = mapped_column(Float)
    end_second: Mapped[float] = mapped_column(Float)
    playback_speed: Mapped[float] = mapped_column(Float)
    wall_clock_seconds: Mapped[float] = mapped_column(Float)
    # Dato crudo; el modo (target/native/...) se calcula en stats.py según el idioma del video.
    subtitles_on: Mapped[bool | None] = mapped_column(Boolean)
    subtitle_language: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(index=True)  # cuándo empezó este tramo
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    session: Mapped[ViewingSession] = relationship(back_populates="segments")


class UserVideoSettings(Base):
    __tablename__ = "user_video_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), unique=True)
    language_id: Mapped[int | None] = mapped_column(ForeignKey("languages.id"))
    comprehensibility_score: Mapped[float | None] = mapped_column(Float)  # 0.0 - 1.0 (v0.3)
    # Override manual. "unknown" = sin override: se usa la detección automática.
    subtitle_mode: Mapped[str] = mapped_column(String(32), default="unknown")
    content_type: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)

    video: Mapped[Video] = relationship(back_populates="settings")
    language: Mapped[Language | None] = relationship()


class Goal(Base):
    """Objetivo definido por el usuario (v0.5). No pretende ser lingüísticamente óptimo."""

    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    language_id: Mapped[int | None] = mapped_column(ForeignKey("languages.id"))  # None = todos los idiomas
    period: Mapped[str] = mapped_column(String(16))  # total | daily
    metric: Mapped[str] = mapped_column(String(16), default="content")  # content | effective_ci
    target_seconds: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    language: Mapped[Language | None] = relationship()
