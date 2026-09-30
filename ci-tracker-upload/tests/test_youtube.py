import pytest

from backend.app.youtube import normalize_language_code, parse_iso8601_duration, parse_video_item


@pytest.mark.parametrize(
    ("value", "expected"),
    [("PT1H2M3S", 3723), ("PT34M21S", 2061), ("PT45S", 45), ("P1DT1S", 86401), ("P0D", 0), ("garbage", None)],
)
def test_parse_duration(value, expected):
    assert parse_iso8601_duration(value) == expected


@pytest.mark.parametrize(("code", "expected"), [("fr-FR", "fr"), ("RU", "ru"), ("zh-Hans", "zh"), ("zxx", None), (None, None)])
def test_normalize_language_code(code, expected):
    assert normalize_language_code(code) == expected


def test_parse_video_item():
    item = {
        "snippet": {
            "title": "Easy French 123",
            "channelId": "UC123",
            "channelTitle": "Easy French",
            "publishedAt": "2024-05-01T10:00:00Z",
            "description": "desc",
            "defaultAudioLanguage": "fr-FR",
            "thumbnails": {"high": {"url": "https://i.ytimg.com/x.jpg"}},
        },
        "contentDetails": {"duration": "PT34M21S"},
    }
    meta = parse_video_item(item)
    assert meta.title == "Easy French 123"
    assert meta.duration_seconds == 2061
    assert meta.language == "fr"
    assert meta.published_at.year == 2024 and meta.published_at.tzinfo is None
