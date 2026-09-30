"""Simula lo que manda la extensión, para probar backend + dashboard sin abrir YouTube.

    python scripts/simulate_watch.py --video dQw4w9WgXcQ --minutes 12 --rate 1.25 --subs fr

Los eventos arrancan `--minutes` atrás, así que el tiempo cae en "hoy".
"""

import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx


def parse_subs(value: str | None) -> tuple[bool | None, str | None]:
    """None -> no detectado · 'off' -> apagados · 'fr' -> encendidos en francés."""
    if value is None:
        return None, None
    return (False, None) if value == "off" else (True, value)


def build_events(
    video_id: str, minutes: float, rate: float, start_at: float, subs: str | None = None, step: float = 5.0
) -> list[dict]:
    subtitles_on, subtitle_language = parse_subs(subs)
    session_id = str(uuid.uuid4())
    wall_total = minutes * 60
    now = datetime.now(timezone.utc) - timedelta(seconds=wall_total)
    position = start_at
    events: list[dict] = []

    def emit(kind: str, paused: bool) -> None:
        events.append(
            {
                "video_id": video_id,
                "session_id": session_id,
                "seq": len(events),
                "timestamp": now.isoformat(),
                "current_time": round(position, 3),
                "playback_rate": rate,
                "paused": paused,
                "event": kind,
                "subtitles_on": subtitles_on,
                "subtitle_language": subtitle_language,
            }
        )

    emit("play", False)
    elapsed = 0.0
    while elapsed < wall_total:
        dt = min(step, wall_total - elapsed)
        elapsed += dt
        now += timedelta(seconds=dt)
        position += dt * rate
        emit("progress", False)
    emit("pause", True)
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, help="YouTube video id")
    parser.add_argument("--minutes", type=float, default=10, help="minutos reales mirando")
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--start-at", type=float, default=0.0, help="posición inicial en segundos")
    parser.add_argument("--subs", default=None, help="'off', o código de idioma de los subtítulos (p. ej. fr)")
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    events = build_events(args.video, args.minutes, args.rate, args.start_at, args.subs)
    response = httpx.post(f"{args.backend}/events/watch", json={"events": events}, timeout=10)
    print(response.status_code, response.json())
    return 0 if response.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
