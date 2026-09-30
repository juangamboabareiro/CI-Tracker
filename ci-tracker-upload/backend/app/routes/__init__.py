"""Routers de la API, uno por tema."""

from . import events, goals, stats, videos

ROUTERS = [events.router, videos.router, stats.router, goals.router]
