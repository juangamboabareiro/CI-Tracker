"""Estadísticas agregadas."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import Settings
from ..schemas import (
    ComprehensibilityStats,
    ContentType,
    DailyPoint,
    GroupStats,
    LanguageStats,
    StreakOut,
    SubtitleStats,
    SummaryOut,
)
from ..stats import (
    UNCLASSIFIED,
    UNKNOWN_CHANNEL,
    compute_daily_totals,
    daily_series,
    filter_language,
    language_names,
    overall_stats,
    seconds_by_day,
    speed_label,
    stats_by_comprehensibility,
    stats_by_group,
    stats_by_language,
    stats_by_subtitles,
    video_contexts,
)
from ..streaks import compute_streak
from ..timeutils import local_today
from .deps import get_db, get_settings_dep, stats_config

router = APIRouter(prefix="/stats")


@router.get("/summary", response_model=SummaryOut)
def stats_summary(db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> SummaryOut:
    today = local_today(settings.tz)
    totals = compute_daily_totals(db, stats_config(settings))
    return SummaryOut(
        timezone=settings.timezone,
        today=today,
        total=overall_stats(totals, today),
        languages=stats_by_language(totals, language_names(db), today),
        videos_watched=len({t.video_id for t in totals}),
    )


@router.get("/languages", response_model=list[LanguageStats])
def stats_languages(db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> list[LanguageStats]:
    totals = compute_daily_totals(db, stats_config(settings))
    return stats_by_language(totals, language_names(db), local_today(settings.tz))


@router.get("/subtitles", response_model=list[SubtitleStats])
def stats_subtitles(
    language: str | None = None, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> list[SubtitleStats]:
    """Tiempo por modo de subtítulos (none / target_language / native_language / ...)."""
    totals = compute_daily_totals(db, stats_config(settings))
    return stats_by_subtitles(totals, local_today(settings.tz), language)


@router.get("/comprehensibility", response_model=list[ComprehensibilityStats])
def stats_comprehensibility(
    language: str | None = None, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> list[ComprehensibilityStats]:
    """Tiempo visto por rango de comprensibilidad (evaluación subjetiva del usuario)."""
    totals = compute_daily_totals(db, stats_config(settings))
    return stats_by_comprehensibility(totals, local_today(settings.tz), language)


@router.get("/daily", response_model=list[DailyPoint])
def stats_daily(
    days: int = Query(default=30, ge=1, le=3650),
    language: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> list[DailyPoint]:
    today = local_today(settings.tz)
    totals = compute_daily_totals(db, stats_config(settings))
    return daily_series(totals, today - timedelta(days=days - 1), today, language)


@router.get("/streaks", response_model=list[StreakOut])
def stats_streaks(
    threshold_seconds: float | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> list[StreakOut]:
    """Streak global (language = null) y uno por idioma. Umbral: STREAK_THRESHOLD_SECONDS."""
    threshold = threshold_seconds or settings.streak_threshold_seconds
    today = local_today(settings.tz)
    totals = compute_daily_totals(db, stats_config(settings))
    languages = sorted({t.language for t in totals})
    return [compute_streak(seconds_by_day(totals), today, threshold)] + [
        compute_streak(seconds_by_day(filter_language(totals, lang)), today, threshold, lang) for lang in languages
    ]


@router.get("/channels", response_model=list[GroupStats])
def stats_channels(
    language: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> list[GroupStats]:
    """Horas por canal (v0.7)."""
    config = stats_config(settings)
    contexts = video_contexts(db, config.channel_comprehensibility_fallback)
    totals = compute_daily_totals(db, config, contexts)
    names = {c.channel_id: c.channel_name or c.channel_id for c in contexts.values() if c.channel_id}
    names[UNKNOWN_CHANNEL] = "Canal desconocido"
    key_of = lambda t: contexts[t.video_id].channel_id or UNKNOWN_CHANNEL  # noqa: E731
    return stats_by_group(totals, local_today(settings.tz), key_of, names, language)[:limit]


@router.get("/content-types", response_model=list[GroupStats])
def stats_content_types(
    language: str | None = None, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> list[GroupStats]:
    """Horas por tipo de contenido (v0.7)."""
    config = stats_config(settings)
    contexts = video_contexts(db, config.channel_comprehensibility_fallback)
    totals = compute_daily_totals(db, config, contexts)
    names = {ct.value: ct.value for ct in ContentType} | {UNCLASSIFIED: "unclassified"}
    key_of = lambda t: contexts[t.video_id].content_type or UNCLASSIFIED  # noqa: E731
    return stats_by_group(totals, local_today(settings.tz), key_of, names, language)


@router.get("/speeds", response_model=list[GroupStats])
def stats_speeds(
    language: str | None = None, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> list[GroupStats]:
    """Horas por velocidad de reproducción, ordenado de menor a mayor velocidad (v0.7)."""
    totals = compute_daily_totals(db, stats_config(settings))
    groups = stats_by_group(totals, local_today(settings.tz), lambda t: speed_label(t.rate), language=language)
    return sorted(groups, key=lambda g: float(g.key.rstrip("x")))
