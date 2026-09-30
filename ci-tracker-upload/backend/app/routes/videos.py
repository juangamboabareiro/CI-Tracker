"""Videos: listado, detalle, ajustes manuales y metadata."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..ingest import get_or_create_video
from ..metadata import MetadataClient, refresh_video_metadata
from ..models import Language, UserVideoSettings, Video, ViewingSession
from ..schemas import (
    LanguageOut,
    SegmentOut,
    SessionOut,
    VideoDetailOut,
    VideoOut,
    VideoSettingsIn,
    VideoSettingsOut,
)
from ..stats import NO_CONTEXT, UNASSIGNED, VideoContext, VideoWatchStats
from .deps import contexts_and_stats, find_language, find_video, get_db, get_metadata_client, get_settings_dep

router = APIRouter()


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
        caption_language=video.caption_language,
        text_language=video.text_language,
        language=ctx.language.code,
        language_source=ctx.language.source,
        content_type=ctx.content_type,
        content_type_source=ctx.content_type_source,
        settings=_settings_out(video.settings),
        comprehensibility=ctx.comprehensibility,
        comprehensibility_source=ctx.comprehensibility_source,
        content_seconds=stats.content_seconds,
        wall_clock_seconds=stats.wall_seconds,
        effective_ci_seconds=(
            stats.content_seconds * ctx.comprehensibility if ctx.comprehensibility is not None else None
        ),
        coverage_seconds=stats.coverage_seconds,
        completion=completion,
        last_watched_at=stats.last_watched_at,
    )


@router.get("/languages", response_model=list[LanguageOut])
def list_languages(db: Session = Depends(get_db)) -> list[Language]:
    return list(db.scalars(select(Language).order_by(Language.name)))


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
    video = find_video(db, source, video_id)
    contexts, stats = contexts_and_stats(db, settings)
    return _video_out(video, contexts.get(video.id, NO_CONTEXT), stats.get(video.id))


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
    contexts, stats = contexts_and_stats(db, settings)
    items = [_video_out(v, contexts.get(v.id, NO_CONTEXT), stats.get(v.id)) for v in videos]
    if language:
        items = [i for i in items if (i["language"] or UNASSIGNED) == language]
    items.sort(key=lambda i: i["last_watched_at"] or datetime.min, reverse=True)
    return items[:limit]


@router.get("/videos/{video_id}", response_model=VideoDetailOut)
def get_video(
    video_id: str,
    source: str = "youtube",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    video = find_video(db, source, video_id)
    sessions = db.scalars(
        select(ViewingSession)
        .where(ViewingSession.video_id == video.id)
        .options(selectinload(ViewingSession.segments))
        .order_by(ViewingSession.started_at)
    ).all()
    contexts, all_stats = contexts_and_stats(db, settings)
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
    """Sólo cambian los campos enviados. `null` borra el valor manual (vuelve a lo automático)."""
    video = find_video(db, source, video_id)
    settings = video.settings or UserVideoSettings(video_id=video.id)
    changes = payload.model_dump(exclude_unset=True, mode="json")  # enums -> strings

    if "language" in changes:
        code = changes.pop("language")
        settings.language_id = None if code is None else find_language(db, code).id
    for key, value in changes.items():
        if key == "subtitle_mode" and value is None:
            value = "unknown"
        setattr(settings, key, value)

    db.add(settings)
    db.commit()
    db.refresh(settings)
    return _settings_out(settings)
