"""Punto de entrada ASGI: ``uvicorn catalog_service.main:app``."""

from .api import create_app

app = create_app()
