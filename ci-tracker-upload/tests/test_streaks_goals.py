from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from backend.app.goals import goal_progress
from backend.app.stats import DailyTotal
from backend.app.streaks import compute_streak

TODAY = date(2026, 9, 30)


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def test_current_streak_includes_today():
    seconds = {days_ago(0): 120, days_ago(1): 300, days_ago(2): 90, days_ago(4): 600}
    streak = compute_streak(seconds, TODAY, threshold_seconds=60)
    assert streak.current_days == 3 and streak.today_counts


def test_streak_stays_alive_until_today_ends():
    seconds = {days_ago(1): 300, days_ago(2): 300}
    streak = compute_streak(seconds, TODAY, threshold_seconds=60)
    assert streak.current_days == 2 and not streak.today_counts


def test_streak_breaks_after_a_missed_day_and_threshold_applies():
    seconds = {days_ago(0): 30, days_ago(2): 300, days_ago(3): 300, days_ago(4): 300, days_ago(10): 300}
    streak = compute_streak(seconds, TODAY, threshold_seconds=60)
    assert streak.current_days == 0  # hoy 30 s < umbral y ayer nada
    assert streak.longest_days == 3
    assert streak.last_active_day == days_ago(2)


def test_empty_streak():
    streak = compute_streak({}, TODAY, 60)
    assert (streak.current_days, streak.longest_days, streak.last_active_day) == (0, 0, None)


def total(day: date, language: str, content: float, score: float | None = None) -> DailyTotal:
    return DailyTotal(day, 1, language, "none", content, content, score)


def make_goal(
    period: str, target: float, language: str | None = None, metric: str = "content", baseline: float | None = None
):
    lang = SimpleNamespace(code=language) if language else None
    return SimpleNamespace(
        id=1, language=lang, period=period, metric=metric, target_seconds=target, baseline_seconds=baseline
    )


def test_total_goal_progress_filters_language():
    totals = [total(days_ago(3), "fr", 3600), total(days_ago(0), "fr", 1800), total(days_ago(0), "ru", 7200)]
    out = goal_progress(make_goal("total", 100 * 3600, "fr"), totals, TODAY)
    assert out.current_seconds == pytest.approx(5400)
    assert out.progress == pytest.approx(5400 / 360000)
    assert out.days_met_last_30 is None and out.streak is None


def test_total_goal_adds_manual_baseline():
    totals = [total(days_ago(1), "fr", 3600)]
    out = goal_progress(make_goal("total", 100 * 3600, "fr", baseline=40 * 3600), totals, TODAY)
    assert out.baseline_seconds == pytest.approx(40 * 3600)
    assert out.current_seconds == pytest.approx(41 * 3600)
    assert out.progress == pytest.approx(0.41)


def test_daily_goal_progress_days_met_and_streak():
    totals = [total(days_ago(0), "fr", 3000), total(days_ago(1), "fr", 3600), total(days_ago(2), "fr", 4000)]
    out = goal_progress(make_goal("daily", 3600, "fr"), totals, TODAY)
    assert out.current_seconds == pytest.approx(3000) and not out.met
    assert out.days_met_last_30 == 2
    assert out.streak.current_days == 2 and not out.streak.today_counts


def test_goal_on_effective_ci_metric():
    totals = [total(days_ago(0), "it", 3600, score=0.5)]
    out = goal_progress(make_goal("daily", 1800, "it", metric="effective_ci"), totals, TODAY)
    assert out.current_seconds == pytest.approx(1800) and out.met
