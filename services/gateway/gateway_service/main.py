"""Punto de entrada ASGI: ``uvicorn gateway_service.main:app``."""

from .api import create_app

app = create_app()
