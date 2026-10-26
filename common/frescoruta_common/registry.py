"""Service Registry con auto-registro (Self-Registration) y descubrimiento del lado del cliente.

Problema que resuelve: en el plan gratuito de Render los servicios no pueden
recibir tráfico por la red privada y su URL pública (``*.onrender.com``) solo
se conoce después del despliegue. Cada servicio publica su propia URL
(``RENDER_EXTERNAL_URL``) en Redis al arrancar y la renueva periódicamente
(latido). Los consumidores resuelven la URL por nombre lógico.

Orden de resolución (``ServiceResolver``):
1. Registro en Redis (dinámico).
2. Variable de entorno ``<NOMBRE>_URL`` (p. ej. ``CATALOG_SERVICE_URL``).
3. Valor por defecto para desarrollo local (``http://localhost:<puerto>``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import UTC, datetime

import redis

log = logging.getLogger("frescoruta.registry")

REGISTRY_KEY = "frescoruta:registry"

LOCAL_DEFAULTS = {
    "api-gateway": "http://localhost:8000",
    "catalog-service": "http://localhost:8001",
    "order-service": "http://localhost:8002",
    "payment-service": "http://localhost:8003",
    "notification-service": "http://localhost:8004",
}


def env_var_for(service_name: str) -> str:
    return service_name.upper().replace("-", "_") + "_URL"


class ServiceRegistry:
    """Registro de servicios respaldado por un hash de Redis."""

    def __init__(self, client: redis.Redis | None) -> None:
        self.client = client

    def register(self, name: str, url: str, version: str = "") -> None:
        if self.client is None:
            return
        entry = {
            "url": url.rstrip("/"),
            "version": version,
            "last_seen": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.client.hset(REGISTRY_KEY, name, json.dumps(entry))

    def lookup(self, name: str) -> str | None:
        if self.client is None:
            return None
        try:
            raw = self.client.hget(REGISTRY_KEY, name)
        except redis.RedisError:
            return None
        return json.loads(raw)["url"] if raw else None

    def all(self) -> dict[str, dict]:
        if self.client is None:
            return {}
        try:
            return {k: json.loads(v) for k, v in self.client.hgetall(REGISTRY_KEY).items()}
        except redis.RedisError:
            return {}


class ServiceResolver:
    """Descubrimiento del lado del cliente con caché local de corta duración."""

    def __init__(
        self,
        registry: ServiceRegistry | None = None,
        overrides: dict[str, str] | None = None,
        cache_ttl: float = 10.0,
    ) -> None:
        self.registry = registry
        self.overrides = overrides or {}
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, str]] = {}

    def resolve(self, name: str) -> str:
        if name in self.overrides:
            return self.overrides[name].rstrip("/")
        cached = self._cache.get(name)
        if cached and time.monotonic() - cached[0] < self.cache_ttl:
            return cached[1]
        url = (
            (self.registry.lookup(name) if self.registry else None)
            or os.getenv(env_var_for(name))
            or LOCAL_DEFAULTS.get(name)
        )
        if not url:
            raise LookupError(f"Servicio desconocido: {name}")
        url = url.rstrip("/")
        self._cache[name] = (time.monotonic(), url)
        return url

    def invalidate(self, name: str) -> None:
        self._cache.pop(name, None)


class RegistrationAgent:
    """Hilo en segundo plano que registra el servicio y envía latidos."""

    def __init__(
        self,
        registry: ServiceRegistry,
        name: str,
        url: str,
        version: str = "",
        interval: float = 30,
    ) -> None:
        self.registry = registry
        self.name = name
        self.url = url
        self.version = version
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _beat(self) -> None:
        try:
            self.registry.register(self.name, self.url, self.version)
        except redis.RedisError as exc:
            log.warning(f"No se pudo registrar {self.name} en el Service Registry: {exc}")

    def start(self) -> None:
        if self.registry.client is None:
            return
        self._beat()
        log.info(f"Servicio registrado: {self.name} -> {self.url}")

        def loop() -> None:
            while not self._stop.wait(self.interval):
                self._beat()

        self._thread = threading.Thread(target=loop, name="registry-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
