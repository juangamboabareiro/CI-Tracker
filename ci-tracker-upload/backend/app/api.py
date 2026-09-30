"""Endpoints REST."""

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import Settings
from .ingest import get_or_create_video, ingest_batch, rebuild_all_sessions
from .metadata import MetadataClient, refresh_video_metadata
from .models import Language, UserVideoSettings, Video, ViewingSession
from .schemas import (
    ComprehensibilityStats,
    DailyPoint,
    LanguageOut,
    LanguageStats,
    SegmentOut,
    SessionOut,
    SubtitleStats,
    SummaryOut,
    VideoDetailOut,
    VideoOut,
    VideoSettingsIn,
    VideoSettingsOut,
    WatchBatchIn,
    WatchBatchOut,
)
from .segments import ReconstructionRules
from .stats import (
    ResolvedLanguage,
    StatsConfig,
    VideoContext,
    VideoWatchStats,
    compute_daily_totals,
    daily_series,
    language_names,
    overall_stats,
    stats_by_comprehensibility,
    stats_by_language,
    stats_by_subtitles,
    video_contexts,
    video_watch_stats,
)
from .timeutils import local_today, utcnow

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------- Dependencias (sobrescribibles en tests vía app.state) ----------

def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_metadata_client(request: Request) -> MetadataClient | None:
    return request.app.state.metadata_client


# ---------- Helpers ----------

def _rules(settings: Settings) -> ReconstructionRules:
    return ReconstructionRules(
        max_gap_seconds=settings.max_sample_gap_seconds,
        tolerance_seconds=settings.position_tolerance_seconds,
        rate_slack=settings.rate_slack,
    )


def _stats_config(settings: Settings) -> StatsConfig:
    return StatsConfig(
        tz=settings.tz,
        native_language=settings.native_language,
        channel_comprehensibility_fallback=settings.channel_comprehensibility_fallback,
    )


def _contexts_and_stats(db: Session, settings: Settings) -> tuple[dict[int, VideoContext], dict[int, VideoWatchStats]]:
    config = _stats_config(settings)
    contexts = video_contexts(db, config.channel_comprehensibility_fallback)
    return contexts, video_watch_stats(db, config, contexts)


NO_CONTEXT = VideoContext(ResolvedLanguage(None, None))


def _find_video(db: Session, source: str, source_video_id: str) -> Video:
    video = db.scalar(
        select(Video)
        .where(Video.source == source, Video.source_video_id == source_video_id)
        .options(selectinload(Video.settings).selectinload(UserVideoSettings.language))
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def _settings_out(settings: UserVideoSettings | None) -> VideoSettingsOut | None:
    if settings is None:
        return None
    return VideoSettingsOut(
        language=settings.language.code if settings.language else None,
        comprehensibility_score=settings.comprehensibility_score,
        subtitle_mode=settings.subtitle_mode,
        content_type=settings.content_type,
        notes=settings.notes,
    )


def _video_out(video: Video, ctx: VideoContext, stats: VideoWatchStats | None) -> dict:
    stats = stats or VideoWatchStats(0.0, 0.0, 0.0, None)
    completion = (
        min(1.0, stats.coverage_seconds / video.duration_seconds) if video.duration_seconds else None
    )
    return dict(
        source=video.source,
        source_video_id=video.source_video_id,
        title=video.title,
        channel_name=video.channel_name,
        duration_seconds=video.duration_seconds,
        thumbnail_url=video.thumbnail_url,
        published_at=video.published_at,
        detected_language=video.detected_language,
        language=ctx.language.code,
        language_source=ctx.language.source,
        settings=_settings_out(video.settings),
        comprehensibility=ctx.comprehensibility,
        comprehensibility_source=ctx.comprehensibility_source,
        content_seconds=stats.content_seconds,
        wall_clock_seconds=stats.wall_seconds,
        coverage_seconds=stats.coverage_seconds,
        completion=completion,
        last_watched_at=stats.last_watched_at,
    )


def _fetch_metadata_task(session_factory, client: MetadataClient | None, video_ids: set[int]) -> None:
    with session_factory() as db:
        for video_id in video_ids:
            video = db.get(Video, video_id)
            if video is not None:
                refresh_video_metadata(db, video, client)


# ---------- Endpoints ----------

@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/events/watch", response_model=WatchBatchOut)
def post_watch_events(
    batch: WatchBatchIn,
    background: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    client: MetadataClient | None = Depends(get_metadata_client),
) -> WatchBatchOut:
    result = ingest_batch(db, batch.events, _rules(settings), now=utcnow())
    logger.info(
        "Ingested batch: accepted=%d duplicates=%d rejected=%d",
        result.accepted, result.duplicates, result.rejected,
    )
    if result.videos_needing_metadata and client is not None:
        background.add_task(
            _fetch_metadata_task, request.app.state.session_factory, client, result.videos_needing_metadata
        )
    return WatchBatchOut(
        accepted=result.accepted,
        duplicates=result.duplicates,
        rejected=result.rejected,
        sessions_updated=result.sessions_updated,
    )


@router.post("/admin/rebuild")
def rebuild(db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> dict:
    """Recalcula todos los segmentos desde los eventos crudos (útil tras actualizar el algoritmo)."""
    return {"sessions_rebuilt": rebuild_all_sessions(db, _rules(settings))}


@router.post("/videos/{video_id}/metadata", response_model=VideoOut)
def refresh_metadata(
    video_id: str,
    source: str = "youtube",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    client: MetadataClient | None = Depends(get_metadata_client),
) -> dict:
    """Fuerza la (re)descarga de metadata desde YouTube."""
    if client is None:
        raise HTTPException(status_code=503, detail="YOUTUBE_API_KEY is not configured")
    video = get_or_create_video(db, source, video_id)
    db.commit()
    refresh_video_metadata(db, video, client, force=True)
    video = _find_video(db, source, video_id)
    contexts, stats = _contexts_and_stats(db, settings)
    return _video_out(video, contexts.get(video.id, NO_CONTEXT), stats.get(video.id))


@router.get("/languages", response_model=list[LanguageOut])
def list_languages(db: Session = Depends(get_db)) -> list[Language]:
    return list(db.scalars(select(Language).order_by(Language.name)))


@router.get("/stats/summary", response_model=SummaryOut)
def stats_summary(db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> SummaryOut:
    today = local_today(settings.tz)
    totals = compute_daily_totals(db, _stats_config(settings))
    return SummaryOut(
        timezone=settings.timezone,
        today=today,
        total=overall_stats(totals, today),
        languages=stats_by_language(totals, language_names(db), today),
        videos_watched=len({t.video_id for t in totals}),
    )


@router.get("/stats/languages", response_model=list[LanguageStats])
def stats_languages(db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> list[LanguageStats]:
    totals = compute_daily_totals(db, _stats_config(settings))
    return stats_by_language(totals, language_names(db), local_today(settings.tz))


@router.get("/stats/subtitles", response_model=list[SubtitleStats])
def stats_subtitles(
    language: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> list[SubtitleStats]:
    """Tiempo por modo de subtítulos (none / target_language / native_language / ...)."""
    totals = compute_daily_totals(db, _stats_config(settings))
    return stats_by_subtitles(totals, local_today(settings.tz), language)


@router.get("/stats/comprehensibility", response_model=list[ComprehensibilityStats])
def stats_comprehensibility(
    language: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> list[ComprehensibilityStats]:
    """Tiempo visto por rango de comprensibilidad (evaluación subjetiva del usuario)."""
    totals = compute_daily_totals(db, _stats_config(settings))
    return stats_by_comprehensibility(totals, local_today(settings.tz), language)


@router.get("/stats/daily", response_model=list[DailyPoint])
def stats_daily(
    days: int = Query(default=30, ge=1, le=3650),
    language: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> list[DailyPoint]:
    today = local_today(settings.tz)
    totals = compute_daily_totals(db, _stats_config(settings))
    return daily_series(totals, today - timedelta(days=days - 1), today, language)


@router.get("/videos", response_model=list[VideoOut])
def list_videos(
    language: str | None = None,
    limit: int = Query(default=200, ge=1, le=5000),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> list[dict]:
    videos = db.scalars(
        select(Video).options(selectinload(Video.settings).selectinload(UserVideoSettings.language))
    ).all()
    contexts, stats = _contexts_and_stats(db, settings)
    items = [_video_out(v, contexts.get(v.id, NO_CONTEXT), stats.get(v.id)) for v in videos]
    if language:
        items = [i for i in items if (i["language"] or "unassigned") == language]
    items.sort(key=lambda i: i["last_watched_at"] or datetime.min, reverse=True)
    return items[:limit]


@router.get("/videos/{video_id}", response_model=VideoDetailOut)
def get_video(
    video_id: str,
    source: str = "youtube",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    video = _find_video(db, source, video_id)
    sessions = db.scalars(
        select(ViewingSession)
        .where(ViewingSession.video_id == video.id)
        .options(selectinload(ViewingSession.segments))
        .order_by(ViewingSession.started_at)
    ).all()
    contexts, all_stats = _contexts_and_stats(db, settings)
    stats = all_stats.get(video.id)
    out = _video_out(video, contexts.get(video.id, NO_CONTEXT), stats)
    out["subtitles"] = stats.subtitle_breakdown if stats else {}
    out["sessions"] = [
        SessionOut(
            started_at=s.started_at,
            ended_at=s.ended_at,
            wall_clock_seconds=s.wall_clock_seconds,
            content_seconds=s.content_seconds,
            playback_speed=s.playback_speed,
            segments=[
                SegmentOut(
                    start_second=seg.start_second,
                    end_second=seg.end_second,
                    playback_speed=seg.playback_speed,
                    subtitles_on=seg.subtitles_on,
                    subtitle_language=seg.subtitle_language,
                    started_at=seg.started_at,
                )
                for seg in s.segments
            ],
        )
        for s in sessions
        if s.content_seconds > 0
    ]
    return out


@router.patch("/videos/{video_id}/settings", response_model=VideoSettingsOut)
def patch_video_settings(
    video_id: str,
    payload: VideoSettingsIn,
    source: str = "youtube",
    db: Session = Depends(get_db),
) -> VideoSettingsOut:
    """`subtitle_mode: "unknown"` (o null) vuelve a la detección automática."""
    video = _find_video(db, source, video_id)
    settings = video.settings or UserVideoSettings(video_id=video.id)
    changes = payload.model_dump(exclude_unset=True, mode="json")  # enums -> strings

    if "language" in changes:
        code = changes.pop("language")
        if code is None:
            settings.language_id = None
        else:
            language = db.scalar(select(Language).where(Language.code == code.lower()))
            if language is None:
                raise HTTPException(status_code=422, detail=f"Unknown language code '{code}'. See GET /languages.")
            settings.language_id = language.id
    for key, value in changes.items():
        if key == "subtitle_mode" and value is None:
            value = "unknown"
        setattr(settings, key, value)

    db.add(settings)
    db.commit()
    db.refresh(settings)
    return _settings_out(settings)
