from datetime import datetime, timedelta

import pytest

from backend.app.segments import Sample, Segment, build_segments, dedup_coverage, merge_intervals, union_length
from tests.helpers import Player

T0 = datetime(2026, 9, 30, 15, 0, 0)


def content(samples) -> float:
    return union_length((s.start, s.end) for s in build_segments(samples))


def test_partial_watch_counts_only_watched_seconds():
    # Video de 60 min, veo 0 -> 15 min y cierro.
    p = Player(T0).play(900).pause()
    assert content(p.samples) == pytest.approx(900)


def test_two_separate_ranges_are_summed():
    # 0 -> 15 min, luego 30 -> 40 min = 25 min (no 40).
    p = Player(T0).play(900).pause(10).seek(1800).play(600).pause()
    segments = build_segments(p.samples)
    assert [(s.start, s.end) for s in segments] == [(0, 900), (1800, 2400)]
    assert content(p.samples) == pytest.approx(1500)


def test_overlapping_ranges_are_not_double_counted():
    # 0 -> 10 min, pausa, vuelvo a 5 -> 15 min = 15 min (no 20).
    p = Player(T0).play(600).pause(60).seek(300).play(600).pause()
    segments = build_segments(p.samples)
    assert [(s.start, s.end) for s in segments] == [(0, 600), (300, 900)]
    assert content(p.samples) == pytest.approx(900)


def test_playback_speed_separates_wall_clock_from_content():
    # 60 min de video a 1.5x = 40 min reales.
    p = Player(T0).set_rate(1.5).play(3600).pause()
    segments = build_segments(p.samples)
    assert sum(s.length for s in segments) == pytest.approx(3600)
    assert sum(s.wall_seconds for s in segments) == pytest.approx(2400)


def test_rate_change_splits_segments():
    p = Player(T0).play(60).set_rate(2.0).play(120).pause()
    segments = build_segments(p.samples)
    assert [s.rate for s in segments] == [1.0, 2.0]
    assert sum(s.wall_seconds for s in segments) == pytest.approx(60 + 60)


def test_paused_time_is_not_counted():
    p = Player(T0).play(100).pause(3600).play(100).pause()
    assert content(p.samples) == pytest.approx(200)


def test_forward_jump_without_seek_marker_is_treated_as_seek():
    # Dos muestras "playing" separadas 5 s pero la posición salta 10 minutos.
    samples = [
        Sample(0, T0, 0, 1.0, False, "play"),
        Sample(1, T0 + timedelta(seconds=5), 5, 1.0, False, "progress"),
        Sample(2, T0 + timedelta(seconds=10), 605, 1.0, False, "progress"),
        Sample(3, T0 + timedelta(seconds=15), 610, 1.0, True, "pause"),
    ]
    assert content(samples) == pytest.approx(10)


def test_explicit_seek_breaks_continuity():
    samples = [
        Sample(0, T0, 0, 1.0, False, "play"),
        Sample(1, T0 + timedelta(seconds=3), 3, 1.0, False, "progress"),
        Sample(2, T0 + timedelta(seconds=4), 4.5, 1.0, False, "seek"),  # salto pequeño pero explícito
        Sample(3, T0 + timedelta(seconds=9), 9.5, 1.0, True, "pause"),
    ]
    assert content(samples) == pytest.approx(3 + 5)


def test_long_gap_between_samples_is_ignored():
    # PC suspendida: dos muestras "playing" separadas 1 hora no suman nada.
    samples = [
        Sample(0, T0, 100, 1.0, False, "progress"),
        Sample(1, T0 + timedelta(hours=1), 3700, 1.0, False, "progress"),
    ]
    assert content(samples) == 0


def test_positions_are_clamped_to_video_duration():
    samples = [
        Sample(0, T0, 50, 1.0, False, "play"),
        Sample(1, T0 + timedelta(seconds=5), 55, 1.0, False, "progress"),
    ]
    segments = build_segments(samples, max_position=52)
    assert [(s.start, s.end) for s in segments] == [(50, 52)]


def test_out_of_order_samples_are_sorted_by_seq():
    p = Player(T0).play(60).pause()
    assert content(list(reversed(p.samples))) == pytest.approx(60)


def test_subtitle_change_splits_segments():
    p = Player(T0).set_subtitles(False).play(60).set_subtitles(True, "fr").play(60).pause()
    segments = build_segments(p.samples)
    assert [(s.start, s.end, s.subtitles_on, s.subtitle_language) for s in segments] == [
        (0, 60, False, None),
        (60, 120, True, "fr"),
    ]


def test_merge_intervals():
    assert merge_intervals([(300, 900), (0, 600), (1000, 1100), (1100, 1200)]) == [(0, 900), (1000, 1200)]


def test_dedup_coverage_first_viewing_wins():
    first = Segment(0, 600, 2.0, T0, 300)
    rewatch = Segment(300, 900, 1.0, T0 + timedelta(minutes=30), 600)
    content_s, wall_s = dedup_coverage([rewatch, first])
    assert content_s == pytest.approx(900)
    assert wall_s == pytest.approx(600 / 2.0 + 300 / 1.0)
