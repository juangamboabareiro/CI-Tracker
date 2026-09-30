"""Progreso de objetivos (v0.5).

Los objetivos son simplemente metas que define el usuario; no se asume que sean
lingüísticamente óptimos.

  * total: acumulado histórico de la métrica vs. objetivo (p. ej. 100 h de francés).
  * daily: lo de hoy vs. objetivo diario (p. ej. 60 min/día), cuántos de los últimos
    30 días se cumplió y el streak de días seguidos cumpliéndolo.
"""

from datetime import date, timedelta

from .models import Goal
from .schemas import GoalMetric, GoalOut, GoalPeriod
from .stats import DailyTotal, filter_language, seconds_by_day
from .streaks import compute_streak

METRIC_FIELDS = {GoalMetric.content.value: "content_seconds", GoalMetric.effective_ci.value: "effective_ci_seconds"}


def goal_progress(goal: Goal, totals: list[DailyTotal], today: date) -> GoalOut:
    language = goal.language.code if goal.language else None
    by_day = seconds_by_day(filter_language(totals, language), METRIC_FIELDS[goal.metric])

    days_met = streak = None
    if goal.period == GoalPeriod.total.value:
        current = sum(v for d, v in by_day.items() if d <= today)
    else:
        current = by_day.get(today, 0.0)
        window = [today - timedelta(days=i) for i in range(30)]
        days_met = sum(by_day.get(d, 0.0) >= goal.target_seconds for d in window)
        streak = compute_streak(by_day, today, goal.target_seconds, language)

    return GoalOut(
        id=goal.id,
        language=language,
        period=goal.period,
        metric=goal.metric,
        target_seconds=goal.target_seconds,
        current_seconds=current,
        progress=current / goal.target_seconds,
        met=current >= goal.target_seconds,
        days_met_last_30=days_met,
        streak=streak,
    )
