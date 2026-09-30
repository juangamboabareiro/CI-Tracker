"""Generador de eventos que imita lo que manda la extensión (una muestra cada `step` segundos)."""

from datetime import datetime, timedelta

from backend.app.segments import Sample


class Player:
    def __init__(self, start: datetime, step: float = 5.0) -> None:
        self.now = start
        self.position = 0.0
        self.rate = 1.0
        self.step = step
        self.subtitles_on: bool | None = None
        self.subtitle_language: str | None = None
        self.samples: list[Sample] = []

    def _emit(self, kind: str, paused: bool) -> None:
        self.samples.append(
            Sample(
                len(self.samples), self.now, self.position, self.rate, paused, kind,
                self.subtitles_on, self.subtitle_language,
            )
        )

    def play(self, seconds_of_content: float) -> "Player":
        """Reproduce `seconds_of_content` segundos de video a la velocidad actual."""
        self._emit("play", paused=False)
        remaining = seconds_of_content
        while remaining > 0:
            chunk = min(self.step * self.rate, remaining)
            self.position += chunk
            self.now += timedelta(seconds=chunk / self.rate)
            remaining -= chunk
            self._emit("progress", paused=False)
        return self

    def pause(self, wall_seconds: float = 0) -> "Player":
        self._emit("pause", paused=True)
        self.now += timedelta(seconds=wall_seconds)
        return self

    def seek(self, position: float) -> "Player":
        """Seek estando pausado (el siguiente play arranca en la nueva posición)."""
        self.position = position
        return self

    def set_rate(self, rate: float) -> "Player":
        self.rate = rate
        return self

    def set_subtitles(self, on: bool | None, language: str | None = None) -> "Player":
        self.subtitles_on = on
        self.subtitle_language = language
        return self

    def as_events(self, video_id: str, session_id: str) -> list[dict]:
        return [
            {
                "video_id": video_id,
                "session_id": session_id,
                "seq": s.seq,
                "timestamp": s.at.isoformat() + "Z",
                "current_time": s.position,
                "playback_rate": s.rate,
                "paused": s.paused,
                "event": s.kind,
                "subtitles_on": s.subtitles_on,
                "subtitle_language": s.subtitle_language,
            }
            for s in self.samples
        ]
