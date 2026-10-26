"""Punto de entrada ASGI: ``uvicorn payment_service.main:app``."""

from .api import create_app

app = create_app()
