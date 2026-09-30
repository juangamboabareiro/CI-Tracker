"""Engine, sesiones y creación del esquema."""

import logging
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(url, connect_args=connect_args)
    if is_sqlite:
        # SQLite no aplica foreign keys salvo que se lo pidamos en cada conexión.
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


# Idiomas precargados. Se pueden agregar más desde la DB o el endpoint de settings.
DEFAULT_LANGUAGES: dict[str, str] = {
    "fr": "French",
    "ru": "Russian",
    "it": "Italian",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "nl": "Dutch",
    "ar": "Arabic",
}


def add_missing_columns(engine: Engine) -> None:
    """Mini-migración: agrega a tablas existentes las columnas nuevas (nullable) del modelo.

    create_all crea tablas que faltan pero no modifica las existentes. Esto alcanza
    para agregar columnas opcionales entre versiones; para renombrar/borrar/cambiar
    tipos hay que pasar a Alembic.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    logger.error("Cannot auto-add NOT NULL column %s.%s; use a real migration", table.name, column.name)
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
                logger.info("Added column %s.%s", table.name, column.name)


def init_db(engine: Engine) -> None:
    """Crea tablas, agrega columnas nuevas y precarga idiomas."""
    from . import models  # noqa: F401  (registra los modelos en Base.metadata)

    if engine.url.get_backend_name() == "sqlite" and engine.url.database not in (None, "", ":memory:"):
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    with Session(engine) as db:
        existing = set(db.scalars(select(models.Language.code)))
        for code, name in DEFAULT_LANGUAGES.items():
            if code not in existing:
                db.add(models.Language(code=code, name=name))
        db.commit()
    logger.info("Database ready at %s", engine.url.render_as_string(hide_password=True))
