"""Punto de entrada ASGI: ``uvicorn notification_service.main:app``."""

from .api import create_app

app = create_app()
