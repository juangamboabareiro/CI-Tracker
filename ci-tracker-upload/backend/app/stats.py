"""Estadísticas.

Política de deduplicación: cada segundo de un video se cuenta como máximo UNA vez
por día (local). Esto cubre pausas, rewinds, refresh de página y reaperturas del
mismo video en el día. Volver a ver el mismo video otro día sí suma (es nueva exposición).

Resolución de idioma de un video (el primero que exista):
    1. manual  -> UserVideoSettings.language
    2. youtube -> defaultAudioLanguage de la metadata
    3. channel -> idioma manual más usado en otros videos del mismo canal
Esto permite que, tras asignar el idioma una vez a un canal, el resto sea automático.

Modo de subtítulos de cada tramo (v0.2):
    1. override manual del video (UserVideoSettings.subtitle_mode != "unknown")
    2. detección automática, clasificada respecto del idioma del video y NATIVE_LANGUAGE.
Se calcula acá (no al ingerir) para que reasignar el idioma de un video reclasifique todo.

Comprensibilidad (v0.3) — evaluación SUBJETIVA del usuario, 0.0 a 1.0:
    1. manual  -> UserVideoSettings.comprehensibility_score
    2. channel -> promedio de los puntajes manuales del mismo canal (estimación, desactivable)
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Language, UserVideoSettings, Video, WatchedSegment
from .schemas import (
    ComprehensibilityBucket,
    ComprehensibilityStats,
    DailyPoint,
    LanguageStats,
    PeriodTotals,
    SubtitleMode,
    SubtitleStats,
)
from .segments import Segment, dedup_segments, union_length
from .timeutils import local_date

UNASSIGNED = "unassigned"
PERIODS = ("today", "last_7_days", "last_30_days", "this_year", "all_time")


@dataclass(frozen=True)
class StatsConfig:
    tz: ZoneInfo
    native_language: str | None = None
    channel_comprehensibility_fallback: bool = True


@dataclass(frozen=True)
class ResolvedLanguage:
    code: str | None
    source: str | None  # manual | youtube | channel


@dataclass(frozen=True)
class VideoContext:
    """Lo que hace falta saber de cada video para agregar sus segmentos."""

    language: ResolvedLanguage
    subtitle_override: str | None = None
    comprehensibility: float | None = None
    comprehensibility_source: str | None = None  # manual | channel


@dataclass(frozen=True)
class DailyTotal:
    day: date
    video_id: int
    language: str  # código o UNASSIGNED
    subtitle_mode: str
    content_seconds: float
    wall_seconds: float
    comprehensibility: float | None = None


@dataclass(frozen=True)
class VideoWatchStats:
    content_seconds: float
    wall_seconds: float
    coverage_seconds: float
    last_watched_at: datetime | None
    subtitle_breakdown: dict[str, float] = field(default_factory=dict)


# ---------- Contexto de cada video (idioma, subtítulos, comprensibilidad) ----------

def video_contexts(db: Session, channel_comprehensibility_fallback: bool = True) -> dict[int, VideoContext]:
    rows = db.execute(
        select(
            Video.id,
            Video.channel_id,
            Video.detected_language,
            Language.code,
            UserVideoSettings.subtitle_mode,
            UserVideoSettings.comprehensibility_score,
        )
        .outerjoin(UserVideoSettings, UserVideoSettings.video_id == Video.id)
        .outerjoin(Language, Language.id == UserVideoSettings.language_id)
    ).all()

    channel_languages: dict[str, Counter[str]] = defaultdict(Counter)
    channel_scores: dict[str, list[float]] = defaultdict(list)
    for _vid, channel_id, _detected, manual_lang, _subs, score in rows:
        if channel_id and manual_lang:
            channel_languages[channel_id][manual_lang] += 1
        if channel_id and score is not None:
            channel_scores[channel_id].append(score)

    contexts: dict[int, VideoContext] = {}
    for vid, channel_id, detected, manual_lang, subtitle_mode, score in rows:
        if manual_lang:
            language = ResolvedLanguage(manual_lang, "manual")
        elif detected:
            language = ResolvedLanguage(detected, "youtube")
        elif channel_id in channel_languages:
            language = ResolvedLanguage(channel_languages[channel_id].most_common(1)[0][0], "channel")
        else:
            language = ResolvedLanguage(None, None)

        if score is not None:
            comprehensibility, source = score, "manual"
        elif channel_comprehensibility_fallback and channel_id in channel_scores:
            scores = channel_scores[channel_id]
            comprehensibility, source = sum(scores) / len(scores), "channel"
        else:
            comprehensibility, source = None, None

        override = subtitle_mode if subtitle_mode and subtitle_mode != SubtitleMode.unknown.value else None
        contexts[vid] = VideoContext(language, override, comprehensibility, source)
    return contexts


def classify_subtitles(
    subtitles_on: bool | None, subtitle_language: str | None, video_language: str | None, native_language: str | None
) -> str:
    if subtitles_on is None:
        return SubtitleMode.unknown.value
    if not subtitles_on:
        return SubtitleMode.none.value
    if subtitle_language is None:
        return SubtitleMode.unknown.value
    if video_language and subtitle_language == video_language:
        return SubtitleMode.target_language.value
    if native_language and subtitle_language == native_language:
        return SubtitleMode.native_language.value
    # Sin idioma del video no sabemos si es "target" u "other".
    return SubtitleMode.other_language.value if video_language else SubtitleMode.unknown.value


def comprehensibility_bucket(score: float | None) -> str:
    if score is None:
        return ComprehensibilityBucket.unrated.value
    if score >= 0.9:
        return ComprehensibilityBucket.p90_100.value
    if score >= 0.8:
        return ComprehensibilityBucket.p80_90.value
    if score >= 0.7:
        return ComprehensibilityBucket.p70_80.value
    return ComprehensibilityBucket.below_70.value


def language_names(db: Session) -> dict[str, str]:
    names = {code: name for code, name in db.execute(select(Language.code, Language.name))}
    names[UNASSIGNED] = "Unassigned"
    return names


# ---------- Agregación ----------

def _load_segments(db: Session) -> dict[int, list[Segment]]:
    by_video: dict[int, list[Segment]] = defaultdict(list)
    for row in db.scalars(select(WatchedSegment)):
        by_video[row.video_id].append(
            Segment(
                row.start_second, row.end_second, row.playback_speed, row.started_at, row.wall_clock_seconds,
                row.subtitles_on, row.subtitle_language,
            )
        )
    return by_video


def daily_totals_from_segments(
    segments_by_video: dict[int, list[Segment]],
    contexts: dict[int, VideoContext],
    tz: ZoneInfo,
    native_language: str | None = None,
) -> list[DailyTotal]:
    """Una fila por (día, video, modo de subtítulos) con el contenido deduplicado."""
    totals: list[DailyTotal] = []
    for video_id, segments in segments_by_video.items():
        ctx = contexts.get(video_id, VideoContext(ResolvedLanguage(None, None)))
        by_day: dict[date, list[Segment]] = defaultdict(list)
        for seg in segments:
            by_day[local_date(seg.started_at, tz)].append(seg)

        for day, day_segments in by_day.items():
            acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
            for seg, content, wall in dedup_segments(day_segments):
                mode = ctx.subtitle_override or classify_subtitles(
                    seg.subtitles_on, seg.subtitle_language, ctx.language.code, native_language
                )
                acc[mode][0] += content
                acc[mode][1] += wall
            for mode, (content, wall) in acc.items():
                totals.append(
                    DailyTotal(
                        day, video_id, ctx.language.code or UNASSIGNED, mode, content, wall, ctx.comprehensibility
                    )
                )
    return totals


def compute_daily_totals(db: Session, config: StatsConfig) -> list[DailyTotal]:
    contexts = video_contexts(db, config.channel_comprehensibility_fallback)
    return daily_totals_from_segments(_load_segments(db), contexts, config.tz, config.native_language)


def in_period(day: date, period: str, today: date) -> bool:
    match period:
        case "today":
            return day == today
        case "last_7_days":
            return today - timedelta(days=6) <= day <= today
        case "last_30_days":
            return today - timedelta(days=29) <= day <= today
        case "this_year":
            return day.year == today.year
        case "all_time":
            return True
    raise ValueError(f"Unknown period: {period}")


def _period_totals(totals: list[DailyTotal], today: date) -> dict[str, PeriodTotals]:
    periods = {p: PeriodTotals() for p in PERIODS}
    for t in totals:
        for p in PERIODS:
            if in_period(t.day, p, today):
                periods[p].content_seconds += t.content_seconds
                periods[p].wall_clock_seconds += t.wall_seconds
    return periods


def _filter_language(totals: list[DailyTotal], language: str | None) -> list[DailyTotal]:
    return [t for t in totals if language is None or t.language == language]


def stats_by_language(totals: list[DailyTotal], names: dict[str, str], today: date) -> list[LanguageStats]:
    grouped: dict[str, list[DailyTotal]] = defaultdict(list)
    for t in totals:
        grouped[t.language].append(t)
    result = [
        LanguageStats(language=code, name=names.get(code, code), **_period_totals(items, today))
        for code, items in grouped.items()
    ]
    return sorted(result, key=lambda s: s.all_time.content_seconds, reverse=True)


def overall_stats(totals: list[DailyTotal], today: date) -> LanguageStats:
    return LanguageStats(language="all", name="All languages", **_period_totals(totals, today))


def stats_by_subtitles(totals: list[DailyTotal], today: date, language: str | None = None) -> list[SubtitleStats]:
    """Siempre devuelve los 5 modos (en orden fijo), aunque estén en cero."""
    selected = _filter_language(totals, language)
    return [
        SubtitleStats(subtitle_mode=mode, **_period_totals([t for t in selected if t.subtitle_mode == mode.value], today))
        for mode in SubtitleMode
    ]


def stats_by_comprehensibility(
    totals: list[DailyTotal], today: date, language: str | None = None
) -> list[ComprehensibilityStats]:
    """Tiempo visto por rango de comprensibilidad (siempre los 5 rangos, en orden fijo)."""
    selected = _filter_language(totals, language)
    return [
        ComprehensibilityStats(
            bucket=bucket,
            **_period_totals([t for t in selected if comprehensibility_bucket(t.comprehensibility) == bucket.value], today),
        )
        for bucket in ComprehensibilityBucket
    ]


def daily_series(
    totals: list[DailyTotal], start: date, end: date, language: str | None = None
) -> list[DailyPoint]:
    """Serie diaria con ceros en los días sin actividad (por idioma presente en el rango)."""
    selected = [t for t in _filter_language(totals, language) if start <= t.day <= end]
    languages = sorted({t.language for t in selected}) or ([language] if language else [])
    acc: dict[tuple[date, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for t in selected:
        acc[(t.day, t.language)][0] += t.content_seconds
        acc[(t.day, t.language)][1] += t.wall_seconds

    points: list[DailyPoint] = []
    day = start
    while day <= end:
        for lang in languages:
            content, wall = acc.get((day, lang), (0.0, 0.0))
            points.append(DailyPoint(date=day, language=lang, content_seconds=content, wall_clock_seconds=wall))
        day += timedelta(days=1)
    return points


def video_watch_stats(
    db: Session, config: StatsConfig, contexts: dict[int, VideoContext] | None = None
) -> dict[int, VideoWatchStats]:
    segments_by_video = _load_segments(db)
    contexts = contexts if contexts is not None else video_contexts(db, config.channel_comprehensibility_fallback)
    totals = daily_totals_from_segments(segments_by_video, contexts, config.tz, config.native_language)
    content: dict[int, float] = defaultdict(float)
    wall: dict[int, float] = defaultdict(float)
    subtitles: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in totals:
        content[t.video_id] += t.content_seconds
        wall[t.video_id] += t.wall_seconds
        subtitles[t.video_id][t.subtitle_mode] += t.content_seconds
    return {
        vid: VideoWatchStats(
            content_seconds=content[vid],
            wall_seconds=wall[vid],
            coverage_seconds=union_length((s.start, s.end) for s in segs),
            last_watched_at=max(s.started_at for s in segs),
            subtitle_breakdown=dict(subtitles[vid]),
        )
        for vid, segs in segments_by_video.items()
    }
