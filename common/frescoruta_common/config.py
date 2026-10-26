"""Configuración externalizada (12-Factor App, factor III: Config).

Toda la configuración se lee de variables de entorno, de modo que el mismo
artefacto (imagen Docker) se despliega sin cambios en local, Docker Compose
o Render.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    return int(value) if value is not None else default


def env_float(name: str, default: float) -> float:
    value = env_str(name)
    return float(value) if value is not None else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "on"}


@dataclass(frozen=True)
class Settings:
    """Parámetros comunes a todos los microservicios."""

    service_name: str
    port: int
    public_url: str
    database_url: str | None = None
    redis_url: str | None = None
    internal_token: str | None = None
    environment: str = "local"
    log_level: str = "INFO"
    version: str = "1.0.0"

    @classmethod
    def from_env(
        cls,
        service_name: str,
        default_port: int,
        default_database_url: str | None = None,
    ) -> Settings:
        port = env_int("PORT", default_port)
        # Render expone RENDER_EXTERNAL_URL; en Docker Compose se usa SERVICE_URL.
        public_url = (
            env_str("SERVICE_URL") or env_str("RENDER_EXTERNAL_URL") or f"http://localhost:{port}"
        )
        return cls(
            service_name=service_name,
            port=port,
            public_url=public_url.rstrip("/"),
            database_url=env_str("DATABASE_URL", default_database_url),
            redis_url=env_str("REDIS_URL"),
            internal_token=env_str("INTERNAL_TOKEN"),
            environment=env_str("APP_ENV", "render" if env_str("RENDER") else "local"),
            log_level=env_str("LOG_LEVEL", "INFO"),
            version=env_str("RENDER_GIT_COMMIT", "1.0.0")[:12],
        )
