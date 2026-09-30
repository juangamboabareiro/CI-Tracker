"""Ingesta de eventos de la extensión + utilidades de administración."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from ..config import Settings
from ..ingest import ingest_batch, rebuild_all_sessions
from ..metadata import MetadataClient, refresh_video_metadata
from ..models import Video
from ..schemas import WatchBatchIn, WatchBatchOut
from ..timeutils import utcnow
from .deps import get_db, get_metadata_client, get_settings_dep, reconstruction_rules

logger = logging.getLogger(__name__)
router = APIRouter()


def _fetch_metadata_task(session_factory, client: MetadataClient | None, video_ids: set[int]) -> None:
    with session_factory() as db:
        for video_id in video_ids:
            video = db.get(Video, video_id)
            if video is not None:
                refresh_video_metadata(db, video, client)


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
    result = ingest_batch(db, batch.events, reconstruction_rules(settings), now=utcnow())
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
    """Recalcula segmentos (desde eventos crudos) e idioma por texto. Útil tras actualizar la app."""
    return {"sessions_rebuilt": rebuild_all_sessions(db, reconstruction_rules(settings))}
