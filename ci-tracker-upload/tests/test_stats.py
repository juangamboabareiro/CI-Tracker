from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.app.segments import Segment
from backend.app.stats import (
    ResolvedLanguage,
    VideoContext,
    classify_subtitles,
    comprehensibility_bucket,
    daily_series,
    daily_totals_from_segments,
    in_period,
    overall_stats,
    stats_by_comprehensibility,
    stats_by_subtitles,
)

UTC = ZoneInfo("UTC")
TODAY = date(2026, 9, 30)
T = datetime(2026, 9, 30, 12, 0)


def ctx(code: str | None, override: str | None = None) -> VideoContext:
    return VideoContext(ResolvedLanguage(code, "manual" if code else None), override)


def test_periods():
    assert in_period(TODAY, "today", TODAY)
    assert in_period(TODAY - timedelta(days=6), "last_7_days", TODAY)
    assert not in_period(TODAY - timedelta(days=7), "last_7_days", TODAY)
    assert in_period(TODAY - timedelta(days=29), "last_30_days", TODAY)
    assert not in_period(date(2025, 12, 31), "this_year", TODAY)


def test_same_video_same_day_is_deduplicated_but_other_day_counts():
    segments = {
        1: [
            Segment(0, 600, 1.0, T, 600),
            Segment(0, 600, 1.0, T + timedelta(hours=2), 600),  # refresh y lo vuelvo a ver: no suma
            Segment(0, 600, 1.0, T - timedelta(days=1), 600),  # ayer: suma
        ]
    }
    totals = daily_totals_from_segments(segments, {1: ctx("fr")}, UTC)
    stats = overall_stats(totals, TODAY)
    assert stats.today.content_seconds == pytest.approx(600)
    assert stats.all_time.content_seconds == pytest.approx(1200)


def test_day_boundary_uses_configured_timezone():
    # 02:00 UTC del 30/9 es todavía 29/9 en Buenos Aires (UTC-3).
    segments = {1: [Segment(0, 60, 1.0, datetime(2026, 9, 30, 2, 0), 60)]}
    totals = daily_totals_from_segments(segments, {}, ZoneInfo("America/Argentina/Buenos_Aires"))
    assert totals[0].day == date(2026, 9, 29)
    assert totals[0].language == "unassigned"


def test_daily_series_fills_missing_days_with_zero():
    segments = {1: [Segment(0, 60, 1.0, datetime(2026, 9, 28, 12), 60)]}
    totals = daily_totals_from_segments(segments, {1: ctx("ru")}, UTC)
    points = daily_series(totals, date(2026, 9, 27), TODAY)
    assert [p.content_seconds for p in points] == [0, 60, 0, 0]


# ---------- Subtítulos (v0.2) ----------

@pytest.mark.parametrize(
    ("on", "sub_lang", "video_lang", "expected"),
    [
        (None, None, "fr", "unknown"),
        (False, None, "fr", "none"),
        (True, "fr", "fr", "target_language"),
        (True, "es", "fr", "native_language"),
        (True, "en", "fr", "other_language"),
        (True, None, "fr", "unknown"),  # encendidos pero sin idioma detectado
        (True, "en", None, "unknown"),  # sin idioma del video no se puede clasificar
        (True, "es", None, "native_language"),
        (True, "es", "es", "target_language"),  # contenido en español con subs en español
    ],
)
def test_classify_subtitles(on, sub_lang, video_lang, expected):
    assert classify_subtitles(on, sub_lang, video_lang, native_language="es") == expected


def test_subtitle_breakdown_per_language():
    segments = {
        1: [
            Segment(0, 600, 1.0, T, 600, subtitles_on=False),
            Segment(600, 900, 1.0, T + timedelta(minutes=10), 300, subtitles_on=True, subtitle_language="fr"),
            Segment(900, 1000, 1.0, T + timedelta(minutes=15), 100, subtitles_on=True, subtitle_language="es"),
        ]
    }
    totals = daily_totals_from_segments(segments, {1: ctx("fr")}, UTC, native_language="es")
    by_mode = {s.subtitle_mode.value: s.all_time.content_seconds for s in stats_by_subtitles(totals, TODAY, "fr")}
    assert by_mode == {"none": 600, "target_language": 300, "native_language": 100, "other_language": 0, "unknown": 0}


def test_rewatch_with_subtitles_is_attributed_to_first_viewing():
    # Veo 0-600 sin subs, después repito 0-600 con subs: cuenta 600 "none", nada más.
    segments = {
        1: [
            Segment(0, 600, 1.0, T, 600, subtitles_on=False),
            Segment(0, 600, 1.0, T + timedelta(minutes=20), 600, subtitles_on=True, subtitle_language="fr"),
        ]
    }
    totals = daily_totals_from_segments(segments, {1: ctx("fr")}, UTC)
    assert [(t.subtitle_mode, t.content_seconds) for t in totals] == [("none", 600)]


@pytest.mark.parametrize(
    ("score", "bucket"),
    [(None, "unrated"), (1.0, "90-100"), (0.9, "90-100"), (0.89, "80-90"), (0.8, "80-90"), (0.7, "70-80"), (0.69, "<70"), (0.0, "<70")],
)
def test_comprehensibility_bucket(score, bucket):
    assert comprehensibility_bucket(score) == bucket


def test_comprehensibility_distribution():
    segments = {
        1: [Segment(0, 600, 1.0, T, 600)],
        2: [Segment(0, 300, 1.0, T, 300)],
        3: [Segment(0, 100, 1.0, T, 100)],
    }
    contexts = {
        1: VideoContext(ResolvedLanguage("fr", "manual"), comprehensibility=0.95),
        2: VideoContext(ResolvedLanguage("fr", "manual"), comprehensibility=0.6),
        3: VideoContext(ResolvedLanguage("ru", "manual")),
    }
    totals = daily_totals_from_segments(segments, contexts, UTC)
    fr = {s.bucket.value: s.all_time.content_seconds for s in stats_by_comprehensibility(totals, TODAY, "fr")}
    assert fr == {"90-100": 600, "80-90": 0, "70-80": 0, "<70": 300, "unrated": 0}
    everything = {s.bucket.value: s.all_time.content_seconds for s in stats_by_comprehensibility(totals, TODAY)}
    assert everything["unrated"] == 100


def test_effective_ci_is_content_times_score_and_excludes_unrated():
    segments = {
        1: [Segment(0, 3600, 1.5, T, 2400)],  # 60 min de contenido a 1.5x
        2: [Segment(0, 600, 1.0, T, 600)],  # sin puntaje
    }
    contexts = {
        1: VideoContext(ResolvedLanguage("fr", "manual"), comprehensibility=0.8),
        2: VideoContext(ResolvedLanguage("fr", "manual")),
    }
    totals = daily_totals_from_segments(segments, contexts, UTC)
    today = overall_stats(totals, TODAY).today
    assert today.content_seconds == pytest.approx(4200)
    assert today.effective_ci_seconds == pytest.approx(3600 * 0.8)  # 48 min: se usa contenido, no tiempo real
    assert today.rated_content_seconds == pytest.approx(3600)

    points = daily_series(totals, TODAY, TODAY)
    assert points[0].effective_ci_seconds == pytest.approx(2880)


def test_manual_override_wins_over_detection():
    segments = {1: [Segment(0, 300, 1.0, T, 300, subtitles_on=False)]}
    totals = daily_totals_from_segments(segments, {1: ctx("fr", override="target_language")}, UTC)
    assert totals[0].subtitle_mode == "target_language"
