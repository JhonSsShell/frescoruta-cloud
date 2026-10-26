"""Punto de entrada ASGI: ``uvicorn order_service.main:app``."""

from .api import create_app

app = create_app()
