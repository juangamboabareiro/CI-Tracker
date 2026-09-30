"""Estadísticas.

Política de deduplicación: cada segundo de un video se cuenta como máximo UNA vez
por día (local). Esto cubre pausas, rewinds, refresh de página y reaperturas del
mismo video en el día. Volver a ver el mismo video otro día sí suma (es nueva exposición).

Resolución de idioma de un video (el primero que exista):
    1. manual   -> UserVideoSettings.language
    2. youtube  -> defaultAudioLanguage de la metadata
    3. captions -> idioma de los subtítulos automáticos (ASR) = idioma del audio (v0.6)
    4. channel  -> idioma manual más usado en otros videos del mismo canal
    5. text     -> detector liviano sobre título + descripción (v0.6)

Modo de subtítulos de cada tramo (v0.2):
    1. override manual del video (UserVideoSettings.subtitle_mode != "unknown")
    2. detección automática, clasificada respecto del idioma del video y NATIVE_LANGUAGE.
Se calcula acá (no al ingerir) para que reasignar el idioma de un video reclasifique todo.

Comprensibilidad (v0.3) — evaluación SUBJETIVA del usuario, 0.0 a 1.0:
    1. manual  -> UserVideoSettings.comprehensibility_score
    2. channel -> promedio de los puntajes manuales del mismo canal (estimación, desactivable)

CI efectivo (v0.4) — métrica DERIVADA y APROXIMADA, no una medida de adquisición:
    effective_ci = content_seconds × comprehensibility
El tiempo sin puntaje no aporta; `rated_content_seconds` indica sobre cuánto se calculó.

Tipo de contenido (v0.7): manual -> tipo manual más usado en el canal.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
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
    GroupStats,
    LanguageStats,
    PeriodTotals,
    SubtitleMode,
    SubtitleStats,
)
from .segments import Segment, dedup_segments, union_length
from .timeutils import local_date

UNASSIGNED = "unassigned"
UNKNOWN_CHANNEL = "unknown-channel"
UNCLASSIFIED = "unclassified"
PERIODS = ("today", "last_7_days", "last_30_days", "this_year", "all_time")


@dataclass(frozen=True)
class StatsConfig:
    tz: ZoneInfo
    native_language: str | None = None
    channel_comprehensibility_fallback: bool = True


@dataclass(frozen=True)
class ResolvedLanguage:
    code: str | None
    source: str | None  # manual | youtube | captions | channel | text


@dataclass(frozen=True)
class VideoContext:
    """Lo que hace falta saber de cada video para agregar sus segmentos."""

    language: ResolvedLanguage
    subtitle_override: str | None = None
    comprehensibility: float | None = None
    comprehensibility_source: str | None = None  # manual | channel
    channel_id: str | None = None
    channel_name: str | None = None
    content_type: str | None = None
    content_type_source: str | None = None  # manual | channel


NO_CONTEXT = VideoContext(ResolvedLanguage(None, None))


@dataclass(frozen=True)
class DailyTotal:
    """Contenido deduplicado de un video en un día, para una combinación (subtítulos, velocidad)."""

    day: date
    video_id: int
    language: str  # código o UNASSIGNED
    subtitle_mode: str
    content_seconds: float
    wall_seconds: float
    comprehensibility: float | None = None
    rate: float = 1.0

    @property
    def rated_content_seconds(self) -> float:
        return self.content_seconds if self.comprehensibility is not None else 0.0

    @property
    def effective_ci_seconds(self) -> float:
        """ESTIMADO (v0.4): contenido × comprensibilidad. Sin puntaje no aporta (no se inventa un valor)."""
        return self.content_seconds * self.comprehensibility if self.comprehensibility is not None else 0.0


def _accumulate(target: PeriodTotals | DailyPoint, t: DailyTotal) -> None:
    target.content_seconds += t.content_seconds
    target.wall_clock_seconds += t.wall_seconds
    target.effective_ci_seconds += t.effective_ci_seconds
    target.rated_content_seconds += t.rated_content_seconds


@dataclass(frozen=True)
class VideoWatchStats:
    content_seconds: float
    wall_seconds: float
    coverage_seconds: float
    last_watched_at: datetime | None
    subtitle_breakdown: dict[str, float] = field(default_factory=dict)


# ---------- Contexto de cada video ----------

def _most_common(counter: Counter[str]) -> str:
    return counter.most_common(1)[0][0]


def video_contexts(db: Session, channel_comprehensibility_fallback: bool = True) -> dict[int, VideoContext]:
    rows = db.execute(
        select(
            Video.id,
            Video.channel_id,
            Video.channel_name,
            Video.detected_language,
            Video.caption_language,
            Video.text_language,
            Language.code,
            UserVideoSettings.subtitle_mode,
            UserVideoSettings.comprehensibility_score,
            UserVideoSettings.content_type,
        )
        .outerjoin(UserVideoSettings, UserVideoSettings.video_id == Video.id)
        .outerjoin(Language, Language.id == UserVideoSettings.language_id)
    ).all()

    # Lo que el usuario asignó a mano en cada canal sirve de valor por defecto para el resto.
    channel_languages: dict[str, Counter[str]] = defaultdict(Counter)
    channel_types: dict[str, Counter[str]] = defaultdict(Counter)
    channel_scores: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if not row.channel_id:
            continue
        if row.code:
            channel_languages[row.channel_id][row.code] += 1
        if row.content_type:
            channel_types[row.channel_id][row.content_type] += 1
        if row.comprehensibility_score is not None:
            channel_scores[row.channel_id].append(row.comprehensibility_score)

    contexts: dict[int, VideoContext] = {}
    for row in rows:
        if row.code:
            language = ResolvedLanguage(row.code, "manual")
        elif row.detected_language:
            language = ResolvedLanguage(row.detected_language, "youtube")
        elif row.caption_language:
            language = ResolvedLanguage(row.caption_language, "captions")
        elif row.channel_id in channel_languages:
            language = ResolvedLanguage(_most_common(channel_languages[row.channel_id]), "channel")
        elif row.text_language:
            language = ResolvedLanguage(row.text_language, "text")
        else:
            language = ResolvedLanguage(None, None)

        if row.comprehensibility_score is not None:
            score, score_source = row.comprehensibility_score, "manual"
        elif channel_comprehensibility_fallback and row.channel_id in channel_scores:
            scores = channel_scores[row.channel_id]
            score, score_source = sum(scores) / len(scores), "channel"
        else:
            score, score_source = None, None

        if row.content_type:
            content_type, type_source = row.content_type, "manual"
        elif row.channel_id in channel_types:
            content_type, type_source = _most_common(channel_types[row.channel_id]), "channel"
        else:
            content_type, type_source = None, None

        subtitle_mode = row.subtitle_mode
        override = subtitle_mode if subtitle_mode and subtitle_mode != SubtitleMode.unknown.value else None
        contexts[row.id] = VideoContext(
            language=language,
            subtitle_override=override,
            comprehensibility=score,
            comprehensibility_source=score_source,
            channel_id=row.channel_id,
            channel_name=row.channel_name,
            content_type=content_type,
            content_type_source=type_source,
        )
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
    """Una fila por (día, video, modo de subtítulos, velocidad) con el contenido deduplicado."""
    totals: list[DailyTotal] = []
    for video_id, segments in segments_by_video.items():
        ctx = contexts.get(video_id, NO_CONTEXT)
        by_day: dict[date, list[Segment]] = defaultdict(list)
        for seg in segments:
            by_day[local_date(seg.started_at, tz)].append(seg)

        for day, day_segments in by_day.items():
            acc: dict[tuple[str, float], list[float]] = defaultdict(lambda: [0.0, 0.0])
            for seg, content, wall in dedup_segments(day_segments):
                mode = ctx.subtitle_override or classify_subtitles(
                    seg.subtitles_on, seg.subtitle_language, ctx.language.code, native_language
                )
                acc[(mode, seg.rate)][0] += content
                acc[(mode, seg.rate)][1] += wall
            for (mode, rate), (content, wall) in acc.items():
                totals.append(
                    DailyTotal(
                        day=day,
                        video_id=video_id,
                        language=ctx.language.code or UNASSIGNED,
                        subtitle_mode=mode,
                        content_seconds=content,
                        wall_seconds=wall,
                        comprehensibility=ctx.comprehensibility,
                        rate=rate,
                    )
                )
    return totals


def compute_daily_totals(
    db: Session, config: StatsConfig, contexts: dict[int, VideoContext] | None = None
) -> list[DailyTotal]:
    contexts = contexts if contexts is not None else video_contexts(db, config.channel_comprehensibility_fallback)
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
                _accumulate(periods[p], t)
    return periods


def filter_language(totals: list[DailyTotal], language: str | None) -> list[DailyTotal]:
    return [t for t in totals if language is None or t.language == language]


def seconds_by_day(totals: list[DailyTotal], metric: str = "content_seconds") -> dict[date, float]:
    """{día: segundos} de la métrica ('content_seconds' o 'effective_ci_seconds')."""
    by_day: dict[date, float] = defaultdict(float)
    for t in totals:
        by_day[t.day] += getattr(t, metric)
    return dict(by_day)


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
    selected = filter_language(totals, language)
    return [
        SubtitleStats(subtitle_mode=mode, **_period_totals([t for t in selected if t.subtitle_mode == mode.value], today))
        for mode in SubtitleMode
    ]


def stats_by_comprehensibility(
    totals: list[DailyTotal], today: date, language: str | None = None
) -> list[ComprehensibilityStats]:
    """Tiempo visto por rango de comprensibilidad (siempre los 5 rangos, en orden fijo)."""
    selected = filter_language(totals, language)
    return [
        ComprehensibilityStats(
            bucket=bucket,
            **_period_totals([t for t in selected if comprehensibility_bucket(t.comprehensibility) == bucket.value], today),
        )
        for bucket in ComprehensibilityBucket
    ]


def stats_by_group(
    totals: list[DailyTotal],
    today: date,
    key_of: Callable[[DailyTotal], str],
    names: dict[str, str] | None = None,
    language: str | None = None,
) -> list[GroupStats]:
    """Desglose genérico por una clave (canal, tipo de contenido, velocidad...). Ordenado por total."""
    grouped: dict[str, list[DailyTotal]] = defaultdict(list)
    for t in filter_language(totals, language):
        grouped[key_of(t)].append(t)
    result = [
        GroupStats(
            key=key,
            name=(names or {}).get(key, key),
            videos=len({t.video_id for t in items}),
            **_period_totals(items, today),
        )
        for key, items in grouped.items()
    ]
    return sorted(result, key=lambda s: s.all_time.content_seconds, reverse=True)


def speed_label(rate: float) -> str:
    return f"{round(rate, 2):g}x"


def daily_series(
    totals: list[DailyTotal], start: date, end: date, language: str | None = None
) -> list[DailyPoint]:
    """Serie diaria con ceros en los días sin actividad (por idioma presente en el rango)."""
    selected = [t for t in filter_language(totals, language) if start <= t.day <= end]
    languages = sorted({t.language for t in selected}) or ([language] if language else [])
    points: dict[tuple[date, str], DailyPoint] = {}
    day = start
    while day <= end:
        for lang in languages:
            points[(day, lang)] = DailyPoint(date=day, language=lang, content_seconds=0.0, wall_clock_seconds=0.0)
        day += timedelta(days=1)
    for t in selected:
        _accumulate(points[(t.day, t.language)], t)
    return list(points.values())


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
