"""Cliente HTTP del backend. El dashboard nunca toca la base de datos directamente."""

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
_client = httpx.Client(base_url=BACKEND_URL, timeout=10.0)


class BackendError(RuntimeError):
    pass


def _request(method: str, path: str, **kwargs):
    try:
        response = _client.request(method, path, **kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise BackendError(f"{exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise BackendError(f"No se pudo conectar con el backend en {BACKEND_URL} ({type(exc).__name__})") from exc
    return response.json() if response.content else None


def _lang(language: str | None) -> dict | None:
    return {"language": language} if language else None


# ---------- Estadísticas ----------

def get_summary() -> dict:
    return _request("GET", "/stats/summary")


def get_daily(days: int, language: str | None = None) -> list[dict]:
    return _request("GET", "/stats/daily", params={"days": days} | (_lang(language) or {}))


def get_subtitle_stats(language: str | None = None) -> list[dict]:
    return _request("GET", "/stats/subtitles", params=_lang(language))


def get_comprehensibility_stats(language: str | None = None) -> list[dict]:
    return _request("GET", "/stats/comprehensibility", params=_lang(language))


def get_streaks() -> list[dict]:
    return _request("GET", "/stats/streaks")


def get_channel_stats(language: str | None = None) -> list[dict]:
    return _request("GET", "/stats/channels", params=_lang(language))


def get_content_type_stats(language: str | None = None) -> list[dict]:
    return _request("GET", "/stats/content-types", params=_lang(language))


def get_speed_stats(language: str | None = None) -> list[dict]:
    return _request("GET", "/stats/speeds", params=_lang(language))


# ---------- Videos ----------

def get_videos() -> list[dict]:
    return _request("GET", "/videos")


def get_video(video_id: str) -> dict:
    return _request("GET", f"/videos/{video_id}")


def get_languages() -> list[dict]:
    return _request("GET", "/languages")


def update_video_settings(video_id: str, **changes) -> dict:
    return _request("PATCH", f"/videos/{video_id}/settings", json=changes)


def refresh_metadata(video_id: str) -> dict:
    return _request("POST", f"/videos/{video_id}/metadata")


# ---------- Objetivos ----------

def get_goals() -> list[dict]:
    return _request("GET", "/goals")


def create_goal(language: str | None, period: str, metric: str, target_seconds: float) -> dict:
    payload = {"language": language, "period": period, "metric": metric, "target_seconds": target_seconds}
    return _request("POST", "/goals", json=payload)


def delete_goal(goal_id: int) -> None:
    _request("DELETE", f"/goals/{goal_id}")
