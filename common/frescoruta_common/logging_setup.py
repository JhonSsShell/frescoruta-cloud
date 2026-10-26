"""Logging estructurado en JSON con identificador de correlación.

Cada línea incluye el servicio y el ``request_id`` para poder seguir una
petición a través de todos los microservicios (trazabilidad distribuida
básica).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from .context import get_request_id


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "request_id": get_request_id(),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(service_name: str, level: str = "INFO") -> logging.Logger:
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Evita duplicar los logs de acceso de uvicorn: el middleware ya los registra.
    logging.getLogger("uvicorn.access").disabled = True
    return logging.getLogger(service_name)


def log_extra(**fields) -> dict:
    """Atajo para adjuntar campos estructurados: ``log.info("x", extra=log_extra(a=1))``."""
    return {"extra_fields": fields}
