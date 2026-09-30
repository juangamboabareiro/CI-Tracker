"""Schemas Pydantic: contrato de la API (entrada validada / salida documentada)."""

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    play = "play"
    progress = "progress"
    pause = "pause"
    seek = "seek"
    ended = "ended"


class SubtitleMode(str, Enum):
    none = "none"
    target_language = "target_language"
    native_language = "native_language"
    other_language = "other_language"
    unknown = "unknown"


class ComprehensibilityBucket(str, Enum):
    p90_100 = "90-100"
    p80_90 = "80-90"
    p70_80 = "70-80"
    below_70 = "<70"
    unrated = "unrated"


class ContentType(str, Enum):
    conversation = "conversation"
    podcast = "podcast"
    news = "news"
    vlog = "vlog"
    documentary = "documentary"
    gaming = "gaming"
    education = "education"
    music = "music"
    movie = "movie"
    series = "series"
    other = "other"


# ---------- Eventos ----------

class WatchEventIn(BaseModel):
    source: str = Field(default="youtube", max_length=32)
    video_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    session_id: str = Field(min_length=8, max_length=64)
    seq: int = Field(ge=0, description="Contador por sesión; permite reintentos idempotentes.")
    timestamp: datetime
    current_time: float = Field(ge=0, le=172_800)
    playback_rate: float = Field(gt=0, le=16)
    paused: bool
    event: EventType
    page_title: str | None = Field(default=None, max_length=300)
    subtitles_on: bool | None = Field(default=None, description="None = no se pudo detectar.")
    subtitle_language: str | None = Field(default=None, max_length=35, pattern=r"^[A-Za-z0-9_-]+$")
    audio_language_hint: str | None = Field(
        default=None, max_length=35, pattern=r"^[A-Za-z0-9_-]+$",
        description="Idioma de los subtítulos automáticos (ASR) de YouTube = idioma del audio (v0.6).",
    )


class WatchBatchIn(BaseModel):
    events: list[WatchEventIn] = Field(min_length=1, max_length=1000)


class WatchBatchOut(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    sessions_updated: int


# ---------- Videos ----------

class LanguageOut(BaseModel):
    code: str
    name: str


class VideoSettingsIn(BaseModel):
    """PATCH: sólo se modifican los campos enviados. `language: null` borra el idioma manual."""

    language: str | None = Field(default=None, max_length=16)
    comprehensibility_score: float | None = Field(default=None, ge=0, le=1)
    subtitle_mode: SubtitleMode | None = None
    content_type: ContentType | None = None
    notes: str | None = Field(default=None, max_length=5000)


class VideoSettingsOut(BaseModel):
    language: str | None
    comprehensibility_score: float | None
    subtitle_mode: str
    content_type: str | None
    notes: str | None


class VideoOut(BaseModel):
    source: str
    source_video_id: str
    title: str | None
    channel_name: str | None
    duration_seconds: int | None
    thumbnail_url: str | None
    published_at: datetime | None
    detected_language: str | None
    caption_language: str | None
    text_language: str | None
    language: str | None = Field(description="Idioma efectivo usado en estadísticas.")
    language_source: str | None = Field(description="manual | youtube | captions | channel | text")
    content_type: str | None = Field(description="Tipo de contenido efectivo.")
    content_type_source: str | None = Field(description="manual | channel")
    settings: VideoSettingsOut | None
    comprehensibility: float | None = Field(description="Puntaje efectivo 0-1 (subjetivo).")
    comprehensibility_source: str | None = Field(description="manual | channel (estimado)")
    content_seconds: float = Field(description="Contenido consumido (deduplicado por día).")
    wall_clock_seconds: float
    effective_ci_seconds: float | None = Field(description="ESTIMADO; None si el video no tiene puntaje.")
    coverage_seconds: float = Field(description="Porción distinta del video vista alguna vez.")
    completion: float | None
    last_watched_at: datetime | None


class SegmentOut(BaseModel):
    start_second: float
    end_second: float
    playback_speed: float
    subtitles_on: bool | None
    subtitle_language: str | None
    started_at: datetime


class SessionOut(BaseModel):
    started_at: datetime
    ended_at: datetime | None
    wall_clock_seconds: float
    content_seconds: float
    playback_speed: float
    segments: list[SegmentOut]


class VideoDetailOut(VideoOut):
    sessions: list[SessionOut]
    subtitles: dict[str, float] = Field(description="Contenido (s) por subtitle_mode.")


# ---------- Estadísticas ----------

class PeriodTotals(BaseModel):
    content_seconds: float = 0.0
    wall_clock_seconds: float = 0.0
    effective_ci_seconds: float = Field(
        default=0.0, description="ESTIMADO: contenido × comprensibilidad. Métrica personal, no científica."
    )
    rated_content_seconds: float = Field(
        default=0.0, description="Parte de content_seconds con puntaje (sobre la que se calcula el CI efectivo)."
    )


class PeriodBreakdown(BaseModel):
    today: PeriodTotals
    last_7_days: PeriodTotals
    last_30_days: PeriodTotals
    this_year: PeriodTotals
    all_time: PeriodTotals


class LanguageStats(PeriodBreakdown):
    language: str = Field(description="Código ISO, 'all' o 'unassigned'.")
    name: str


class SubtitleStats(PeriodBreakdown):
    subtitle_mode: SubtitleMode


class ComprehensibilityStats(PeriodBreakdown):
    bucket: ComprehensibilityBucket


class GroupStats(PeriodBreakdown):
    """Desglose genérico (canal, tipo de contenido, velocidad) (v0.7)."""

    key: str
    name: str
    videos: int


# ---------- Streaks y objetivos (v0.5) ----------

class StreakOut(BaseModel):
    language: str | None
    threshold_seconds: float
    current_days: int = Field(description="Días seguidos que cumplen el umbral, hasta hoy (o ayer si hoy aún no).")
    longest_days: int
    today_counts: bool
    last_active_day: date | None


class GoalPeriod(str, Enum):
    total = "total"
    daily = "daily"


class GoalMetric(str, Enum):
    content = "content"
    effective_ci = "effective_ci"


class GoalIn(BaseModel):
    language: str | None = Field(default=None, max_length=16, description="None = todos los idiomas.")
    period: GoalPeriod
    metric: GoalMetric = GoalMetric.content
    target_seconds: float = Field(gt=0, le=10_000 * 3600)


class GoalOut(BaseModel):
    id: int
    language: str | None
    period: GoalPeriod
    metric: GoalMetric
    target_seconds: float
    current_seconds: float = Field(description="Total acumulado (total) o lo de hoy (daily).")
    progress: float = Field(description="current / target (puede superar 1).")
    met: bool
    days_met_last_30: int | None = Field(description="Sólo objetivos diarios.")
    streak: StreakOut | None = Field(description="Sólo objetivos diarios: días seguidos cumpliéndolo.")


class SummaryOut(BaseModel):
    timezone: str
    today: date
    total: LanguageStats
    languages: list[LanguageStats]
    videos_watched: int


class DailyPoint(BaseModel):
    date: date
    language: str
    content_seconds: float
    wall_clock_seconds: float
    effective_ci_seconds: float = 0.0
    rated_content_seconds: float = 0.0
