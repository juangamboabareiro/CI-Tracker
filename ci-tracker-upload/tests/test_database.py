from sqlalchemy import create_engine, inspect, text

from backend.app.database import init_db


def test_init_db_adds_columns_missing_from_older_schema(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    init_db(engine)
    # Simulamos una base de v0.1, que no tenía columnas de subtítulos.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE watched_segments DROP COLUMN subtitle_language"))
        conn.execute(text("ALTER TABLE watch_events DROP COLUMN subtitles_on"))

    init_db(engine)

    assert "subtitle_language" in {c["name"] for c in inspect(engine).get_columns("watched_segments")}
    assert "subtitles_on" in {c["name"] for c in inspect(engine).get_columns("watch_events")}
    engine.dispose()
