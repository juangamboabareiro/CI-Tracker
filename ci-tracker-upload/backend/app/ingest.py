"""Ingesta de eventos: guarda eventos crudos y recalcula los segmentos de cada sesión tocada."""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Video, ViewingSession, WatchedSegment, WatchEvent
from .schemas import WatchEventIn
from .segments import ReconstructionRules, Sample, build_segments, union_length
from .timeutils import to_utc_naive
from .youtube import normalize_language_code

logger = logging.getLogger(__name__)

MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    sessions_updated: int = 0
    videos_needing_metadata: set[int] = field(default_factory=set)


def get_or_create_video(db: Session, source: str, source_video_id: str) -> Video:
    video = db.scalar(select(Video).where(Video.source == source, Video.source_video_id == source_video_id))
    if video is None:
        video = Video(source=source, source_video_id=source_video_id)
        db.add(video)
        db.flush()
    return video


def _get_or_create_session(db: Session, client_session_id: str, video: Video, first_at: datetime) -> ViewingSession | None:
    session = db.scalar(select(ViewingSession).where(ViewingSession.client_session_id == client_session_id))
    if session is None:
        session = ViewingSession(client_session_id=client_session_id, video_id=video.id, started_at=first_at)
        db.add(session)
        db.flush()
    elif session.video_id != video.id:
        return None  # una sesión nunca cambia de video: el cliente está mandando algo inconsistente
    return session


def _subtitle_fields(ev: WatchEventIn) -> tuple[bool | None, str | None]:
    """Normaliza subtítulos: 'fr-FR' -> 'fr'; si están apagados no guardamos idioma."""
    if not ev.subtitles_on:
        return ev.subtitles_on, None
    return True, normalize_language_code(ev.subtitle_language)


def rebuild_session(db: Session, session: ViewingSession, rules: ReconstructionRules) -> None:
    """Recalcula segmentos y totales de la sesión a partir de TODOS sus eventos.

    Recalcular desde cero es simple y hace que el resultado no dependa del orden
    ni de la partición en batches con la que llegaron los eventos.
    """
    events = db.scalars(
        select(WatchEvent).where(WatchEvent.session_id == session.id).order_by(WatchEvent.seq)
    ).all()
    if not events:
        return
    samples = [
        Sample(
            e.seq, e.occurred_at, e.position_seconds, e.playback_rate, e.paused, e.event_type,
            e.subtitles_on, e.subtitle_language,
        )
        for e in events
    ]
    segments = build_segments(samples, rules, max_position=session.video.duration_seconds)

    db.execute(delete(WatchedSegment).where(WatchedSegment.session_id == session.id))
    for seg in segments:
        db.add(
            WatchedSegment(
                session_id=session.id,
                video_id=session.video_id,
                start_second=seg.start,
                end_second=seg.end,
                playback_speed=seg.rate,
                wall_clock_seconds=seg.wall_seconds,
                subtitles_on=seg.subtitles_on,
                subtitle_language=seg.subtitle_language,
                started_at=seg.started_at,
            )
        )

    content_total = sum(s.length for s in segments)
    session.started_at = min(e.occurred_at for e in events)
    session.ended_at = max(e.occurred_at for e in events)
    session.content_seconds = union_length((s.start, s.end) for s in segments)
    session.wall_clock_seconds = sum(s.wall_seconds for s in segments)
    session.playback_speed = (
        sum(s.rate * s.length for s in segments) / content_total if content_total else events[-1].playback_rate
    )


def rebuild_all_sessions(db: Session, rules: ReconstructionRules) -> int:
    """Reprocesa todas las sesiones desde los eventos crudos (tras cambiar el algoritmo)."""
    sessions = db.scalars(select(ViewingSession)).all()
    for session in sessions:
        rebuild_session(db, session, rules)
    db.commit()
    return len(sessions)


def ingest_batch(db: Session, events: list[WatchEventIn], rules: ReconstructionRules, now: datetime) -> IngestResult:
    result = IngestResult()
    by_session: dict[tuple[str, str, str], list[WatchEventIn]] = defaultdict(list)
    for ev in events:
        if to_utc_naive(ev.timestamp) > now + MAX_CLOCK_SKEW:
            result.rejected += 1
            continue
        by_session[(ev.source, ev.video_id, ev.session_id)].append(ev)

    for (source, video_id, client_session_id), group in by_session.items():
        video = get_or_create_video(db, source, video_id)
        if video.metadata_fetched_at is None:
            result.videos_needing_metadata.add(video.id)
        if video.title is None and group[-1].page_title:
            video.title = group[-1].page_title  # fallback hasta tener metadata oficial

        first_at = min(to_utc_naive(e.timestamp) for e in group)
        session = _get_or_create_session(db, client_session_id, video, first_at)
        if session is None:
            logger.warning("Session %s already belongs to another video; rejecting events", client_session_id)
            result.rejected += len(group)
            continue

        existing_seqs = set(db.scalars(select(WatchEvent.seq).where(WatchEvent.session_id == session.id)))
        new_events = {e.seq: e for e in group if e.seq not in existing_seqs}  # dedup también dentro del batch
        result.duplicates += len(group) - len(new_events)
        for e in new_events.values():
            subtitles_on, subtitle_language = _subtitle_fields(e)
            db.add(
                WatchEvent(
                    session_id=session.id,
                    seq=e.seq,
                    event_type=e.event.value,
                    occurred_at=to_utc_naive(e.timestamp),
                    position_seconds=e.current_time,
                    playback_rate=e.playback_rate,
                    paused=e.paused,
                    subtitles_on=subtitles_on,
                    subtitle_language=subtitle_language,
                )
            )
        result.accepted += len(new_events)

        if new_events:
            db.flush()
            rebuild_session(db, session, rules)
            result.sessions_updated += 1

    db.commit()
    return result
