"""Dependencias y helpers compartidos por los routers (sobrescribibles en tests vía app.state)."""

from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..metadata import MetadataClient
from ..models import Language, UserVideoSettings, Video
from ..segments import ReconstructionRules
from ..stats import StatsConfig, VideoContext, VideoWatchStats, video_contexts, video_watch_stats


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


def reconstruction_rules(settings: Settings) -> ReconstructionRules:
    return ReconstructionRules(
        max_gap_seconds=settings.max_sample_gap_seconds,
        tolerance_seconds=settings.position_tolerance_seconds,
        rate_slack=settings.rate_slack,
    )


def stats_config(settings: Settings) -> StatsConfig:
    return StatsConfig(
        tz=settings.tz,
        native_language=settings.native_language,
        channel_comprehensibility_fallback=settings.channel_comprehensibility_fallback,
    )


def contexts_and_stats(db: Session, settings: Settings) -> tuple[dict[int, VideoContext], dict[int, VideoWatchStats]]:
    config = stats_config(settings)
    contexts = video_contexts(db, config.channel_comprehensibility_fallback)
    return contexts, video_watch_stats(db, config, contexts)


def find_video(db: Session, source: str, source_video_id: str) -> Video:
    video = db.scalar(
        select(Video)
        .where(Video.source == source, Video.source_video_id == source_video_id)
        .options(selectinload(Video.settings).selectinload(UserVideoSettings.language))
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def find_language(db: Session, code: str) -> Language:
    language = db.scalar(select(Language).where(Language.code == code.lower()))
    if language is None:
        raise HTTPException(status_code=422, detail=f"Unknown language code '{code}'. See GET /languages.")
    return language
