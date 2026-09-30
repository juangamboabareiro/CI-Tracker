"""Streaks (v0.5). Lógica pura.

Un día "cuenta" si la métrica de ese día alcanza el umbral (por defecto, 1 minuto visto).
El streak actual sigue vivo si hoy todavía no se cumplió pero ayer sí: se muestra hasta
ayer y `today_counts` indica que falta el de hoy.
"""

from datetime import date, timedelta

from .schemas import StreakOut


def _longest_run(days: list[date]) -> int:
    longest = run = 0
    previous: date | None = None
    for day in days:
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    return longest


def compute_streak(
    seconds_by_day: dict[date, float], today: date, threshold_seconds: float, language: str | None = None
) -> StreakOut:
    active = sorted(d for d, secs in seconds_by_day.items() if secs >= threshold_seconds and d <= today)
    active_set = set(active)
    today_counts = today in active_set

    current = 0
    day = today if today_counts else today - timedelta(days=1)
    while day in active_set:
        current += 1
        day -= timedelta(days=1)

    return StreakOut(
        language=language,
        threshold_seconds=threshold_seconds,
        current_days=current,
        longest_days=_longest_run(active),
        today_counts=today_counts,
        last_active_day=active[-1] if active else None,
    )
