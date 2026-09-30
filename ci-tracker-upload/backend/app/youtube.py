"""Cliente mínimo de YouTube Data API v3 (sólo metadata; NO provee tiempo visto)."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

from .timeutils import to_utc_naive

logger = logging.getLogger(__name__)

VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
MAX_DESCRIPTION_CHARS = 2000
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass(frozen=True)
class VideoMetadata:
    title: str
    channel_id: str | None
    channel_name: str | None
    duration_seconds: int | None
    published_at: datetime | None
    thumbnail_url: str | None
    description: str | None
    language: str | None


class YouTubeError(RuntimeError):
    pass


def parse_iso8601_duration(value: str) -> int | None:
    """'PT1H2M3S' -> 3723. Devuelve None si el formato no es reconocible (p. ej. 'P0D' en vivo)."""
    match = _DURATION_RE.match(value or "")
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def normalize_language_code(code: str | None) -> str | None:
    """'fr-FR' -> 'fr', 'zh-Hans' -> 'zh', 'zxx'/'und' -> None."""
    if not code:
        return None
    base = code.replace("_", "-").split("-")[0].lower()
    return None if base in {"zxx", "und", "mul"} else base


def _best_thumbnail(thumbnails: dict) -> str | None:
    for key in ("high", "medium", "default"):
        if key in thumbnails:
            return thumbnails[key].get("url")
    return None


def parse_video_item(item: dict) -> VideoMetadata:
    snippet = item.get("snippet", {})
    details = item.get("contentDetails", {})
    published = snippet.get("publishedAt")
    return VideoMetadata(
        title=snippet.get("title", ""),
        channel_id=snippet.get("channelId"),
        channel_name=snippet.get("channelTitle"),
        duration_seconds=parse_iso8601_duration(details.get("duration", "")),
        published_at=to_utc_naive(datetime.fromisoformat(published.replace("Z", "+00:00"))) if published else None,
        thumbnail_url=_best_thumbnail(snippet.get("thumbnails", {})),
        description=(snippet.get("description") or "")[:MAX_DESCRIPTION_CHARS] or None,
        # defaultAudioLanguage es el más confiable; defaultLanguage es el del título.
        language=normalize_language_code(snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage")),
    )


class YouTubeClient:
    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        if not api_key:
            raise ValueError("YouTube API key is empty")
        self._api_key = api_key
        self._timeout = timeout

    def fetch_video(self, video_id: str) -> VideoMetadata | None:
        """Devuelve None si el video no existe o es privado."""
        params = {"part": "snippet,contentDetails", "id": video_id, "key": self._api_key}
        try:
            response = httpx.get(VIDEOS_URL, params=params, timeout=self._timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # No logueamos la URL completa: contiene la API key.
            raise YouTubeError(f"YouTube API request failed for {video_id}: {type(exc).__name__}") from exc
        items = response.json().get("items", [])
        return parse_video_item(items[0]) if items else None
