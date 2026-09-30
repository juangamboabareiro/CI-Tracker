"""Cacheo de metadata: sólo se consulta YouTube si el video nunca se consultó (o si se fuerza)."""

import logging
from typing import Protocol

from sqlalchemy.orm import Session

from .models import Video
from .timeutils import utcnow
from .youtube import VideoMetadata, YouTubeError

logger = logging.getLogger(__name__)


class MetadataClient(Protocol):
    def fetch_video(self, video_id: str) -> VideoMetadata | None: ...


def apply_metadata(video: Video, meta: VideoMetadata) -> None:
    video.title = meta.title or video.title
    video.channel_id = meta.channel_id
    video.channel_name = meta.channel_name
    video.duration_seconds = meta.duration_seconds
    video.published_at = meta.published_at
    video.thumbnail_url = meta.thumbnail_url
    video.description = meta.description
    video.detected_language = meta.language


def refresh_video_metadata(db: Session, video: Video, client: MetadataClient | None, force: bool = False) -> bool:
    """Devuelve True si se actualizó la metadata."""
    if video.source != "youtube" or client is None:
        return False
    if video.metadata_fetched_at is not None and not force:
        return False
    try:
        meta = client.fetch_video(video.source_video_id)
    except YouTubeError as exc:
        logger.warning("%s", exc)
        return False
    if meta is not None:
        apply_metadata(video, meta)
    else:
        logger.info("Video %s not found on YouTube (private/deleted?)", video.source_video_id)
    video.metadata_fetched_at = utcnow()
    db.commit()
    return meta is not None
