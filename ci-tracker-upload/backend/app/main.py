"""Punto de entrada FastAPI.

Ejecutar (desde la raíz del proyecto):
    uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from .config import Settings, get_settings
from .database import init_db, make_engine, make_session_factory
from .metadata import MetadataClient
from .routes import ROUTERS
from .youtube import YouTubeClient

logger = logging.getLogger(__name__)


def _default_metadata_client(settings: Settings) -> MetadataClient | None:
    key = settings.youtube_api_key.get_secret_value()
    if not key:
        logger.warning("YOUTUBE_API_KEY not set: videos will be tracked without YouTube metadata")
        return None
    return YouTubeClient(key)


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    metadata_client: MetadataClient | None = None,
) -> FastAPI:
    """Fábrica de la app. Los tests inyectan su propio engine y un cliente de YouTube falso."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    engine = engine or make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Comprehensible Input Tracker", version="0.7.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)
    app.state.metadata_client = metadata_client if metadata_client is not None else _default_metadata_client(settings)
    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
