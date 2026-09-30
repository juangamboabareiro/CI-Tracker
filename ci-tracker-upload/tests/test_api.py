from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.youtube import VideoMetadata
from tests.helpers import Player


class FakeYouTube:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_video(self, video_id: str) -> VideoMetadata | None:
        self.calls.append(video_id)
        return VideoMetadata(
            title=f"Video {video_id}",
            channel_id="UC_easyfrench",
            channel_name="Easy French",
            duration_seconds=3600,
            published_at=None,
            thumbnail_url=None,
            description=None,
            language=None,  # sin idioma: se asigna manualmente / por canal
        )


@pytest.fixture
def youtube() -> FakeYouTube:
    return FakeYouTube()


@pytest.fixture
def client(youtube: FakeYouTube):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    app = create_app(settings=Settings(timezone="UTC"), engine=engine, metadata_client=youtube)
    with TestClient(app) as c:
        yield c


def recent_start() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)


def post(client: TestClient, events: list[dict]):
    response = client.post("/events/watch", json={"events": events})
    assert response.status_code == 200, response.text
    return response.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_end_to_end_watch_assign_language_and_stats(client, youtube):
    events = Player(recent_start()).play(900).pause().as_events("vid00000001", "session-aaaa")
    result = post(client, events)
    assert result["accepted"] == len(events)
    assert youtube.calls == ["vid00000001"]  # metadata pedida en background

    video = client.get("/videos/vid00000001").json()
    assert video["title"] == "Video vid00000001"
    assert video["content_seconds"] == pytest.approx(900)
    assert video["completion"] == pytest.approx(0.25)
    assert video["language"] is None

    r = client.patch("/videos/vid00000001/settings", json={"language": "fr"})
    assert r.status_code == 200 and r.json()["language"] == "fr"

    summary = client.get("/stats/summary").json()
    french = next(lang for lang in summary["languages"] if lang["language"] == "fr")
    assert french["all_time"]["content_seconds"] == pytest.approx(900)
    assert summary["total"]["all_time"]["content_seconds"] == pytest.approx(900)


def test_resending_a_batch_is_idempotent(client):
    events = Player(recent_start()).play(300).pause().as_events("vid00000002", "session-bbbb")
    post(client, events)
    again = post(client, events)
    assert again["accepted"] == 0 and again["duplicates"] == len(events)
    assert client.get("/videos/vid00000002").json()["content_seconds"] == pytest.approx(300)


def test_events_split_across_batches_give_same_result(client):
    events = Player(recent_start()).play(600).pause().as_events("vid00000003", "session-cccc")
    half = len(events) // 2
    post(client, events[half:])  # llegan desordenados
    post(client, events[:half])
    assert client.get("/videos/vid00000003").json()["content_seconds"] == pytest.approx(600)


def test_refresh_same_day_does_not_double_count(client):
    start = recent_start()
    post(client, Player(start).play(600).pause().as_events("vid00000004", "session-dddd"))
    # Refresh de página: nueva sesión, vuelvo a ver 0 -> 10 min.
    post(client, Player(start + timedelta(minutes=20)).play(600).pause().as_events("vid00000004", "session-eeee"))
    video = client.get("/videos/vid00000004").json()
    assert video["content_seconds"] == pytest.approx(600)
    assert len(video["sessions"]) == 2


def test_channel_language_is_inherited(client):
    post(client, Player(recent_start()).play(60).pause().as_events("vid00000005", "session-ffff"))
    post(client, Player(recent_start()).play(60).pause().as_events("vid00000006", "session-gggg"))
    client.patch("/videos/vid00000005/settings", json={"language": "fr"})
    other = client.get("/videos/vid00000006").json()
    assert other["language"] == "fr" and other["language_source"] == "channel"


def test_future_events_are_rejected(client):
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    result = post(client, Player(future).play(30).pause().as_events("vid00000007", "session-hhhh"))
    assert result["accepted"] == 0 and result["rejected"] > 0


def test_invalid_payload_returns_422(client):
    r = client.post("/events/watch", json={"events": [{"video_id": "x"}]})
    assert r.status_code == 422


def test_unknown_language_code_is_rejected(client):
    post(client, Player(recent_start()).play(30).pause().as_events("vid00000008", "session-iiii"))
    r = client.patch("/videos/vid00000008/settings", json={"language": "xx"})
    assert r.status_code == 422


def test_subtitles_are_detected_and_reclassified_when_language_is_assigned(client):
    p = (
        Player(recent_start())
        .set_subtitles(False).play(600)
        .set_subtitles(True, "fr-FR").play(300)  # se normaliza a "fr"
        .set_subtitles(True, "es").play(120)
        .pause()
    )
    post(client, p.as_events("vid00000010", "session-kkkk"))

    # Sin idioma asignado, "fr" no se puede clasificar como target.
    before = {s["subtitle_mode"]: s["all_time"]["content_seconds"] for s in client.get("/stats/subtitles").json()}
    assert before["none"] == pytest.approx(600)
    assert before["unknown"] == pytest.approx(300)
    assert before["native_language"] == pytest.approx(120)

    client.patch("/videos/vid00000010/settings", json={"language": "fr"})
    after = {
        s["subtitle_mode"]: s["all_time"]["content_seconds"]
        for s in client.get("/stats/subtitles", params={"language": "fr"}).json()
    }
    assert after["target_language"] == pytest.approx(300)
    assert after["unknown"] == 0

    video = client.get("/videos/vid00000010").json()
    assert video["subtitles"]["none"] == pytest.approx(600)
    langs = [(g["subtitles_on"], g["subtitle_language"]) for g in video["sessions"][0]["segments"]]
    assert langs == [(False, None), (True, "fr"), (True, "es")]


def test_manual_subtitle_mode_overrides_and_can_be_reset(client):
    post(client, Player(recent_start()).play(300).pause().as_events("vid00000011", "session-llll"))
    client.patch("/videos/vid00000011/settings", json={"language": "ru", "subtitle_mode": "target_language"})
    modes = {s["subtitle_mode"]: s["all_time"]["content_seconds"] for s in client.get("/stats/subtitles").json()}
    assert modes["target_language"] == pytest.approx(300)

    client.patch("/videos/vid00000011/settings", json={"subtitle_mode": None})  # vuelve a automático
    modes = {s["subtitle_mode"]: s["all_time"]["content_seconds"] for s in client.get("/stats/subtitles").json()}
    assert modes["unknown"] == pytest.approx(300)


def test_comprehensibility_manual_channel_estimate_and_clear(client):
    # Los tres videos son del mismo canal (FakeYouTube devuelve siempre UC_easyfrench).
    for n in (13, 14, 15):
        post(client, Player(recent_start()).play(600).pause().as_events(f"vid000000{n}", f"session-cmp{n}"))
    client.patch("/videos/vid00000013/settings", json={"language": "fr", "comprehensibility_score": 0.9})
    client.patch("/videos/vid00000014/settings", json={"comprehensibility_score": 0.7})

    rated = client.get("/videos/vid00000013").json()
    assert rated["comprehensibility"] == pytest.approx(0.9) and rated["comprehensibility_source"] == "manual"
    estimated = client.get("/videos/vid00000015").json()
    assert estimated["comprehensibility"] == pytest.approx(0.8)  # promedio del canal
    assert estimated["comprehensibility_source"] == "channel"

    buckets = {b["bucket"]: b["all_time"]["content_seconds"] for b in client.get("/stats/comprehensibility").json()}
    assert buckets["90-100"] == pytest.approx(600)
    assert buckets["80-90"] == pytest.approx(600)  # el estimado
    assert buckets["70-80"] == pytest.approx(600)

    client.patch("/videos/vid00000013/settings", json={"comprehensibility_score": None})
    cleared = client.get("/videos/vid00000013").json()
    assert cleared["comprehensibility"] == pytest.approx(0.7)  # ahora hereda el único puntaje manual del canal
    assert cleared["comprehensibility_source"] == "channel"


def test_comprehensibility_score_is_validated(client):
    post(client, Player(recent_start()).play(60).pause().as_events("vid00000016", "session-cmp16"))
    assert client.patch("/videos/vid00000016/settings", json={"comprehensibility_score": 1.5}).status_code == 422


def test_admin_rebuild_is_stable(client):
    post(client, Player(recent_start()).play(240).pause().as_events("vid00000012", "session-mmmm"))
    assert client.post("/admin/rebuild").json()["sessions_rebuilt"] == 1
    assert client.get("/videos/vid00000012").json()["content_seconds"] == pytest.approx(240)


def test_daily_endpoint(client):
    post(client, Player(recent_start()).play(120).pause().as_events("vid00000009", "session-jjjj"))
    points = client.get("/stats/daily", params={"days": 7}).json()
    assert len(points) == 7
    assert sum(p["content_seconds"] for p in points) == pytest.approx(120)
