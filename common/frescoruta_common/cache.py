"""Caché para el patrón Cache-Aside.

La aplicación consulta primero la caché; en caso de fallo (*miss*) lee de la
base de datos y guarda el resultado con un TTL. Las escrituras invalidan la
entrada correspondiente.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

import redis

log = logging.getLogger("frescoruta.cache")


class Cache(ABC):
    @abstractmethod
    def get(self, key: str) -> Any | None: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...

    @property
    def kind(self) -> str:
        return self.__class__.__name__


class InMemoryTTLCache(Cache):
    def __init__(self, clock=time.monotonic) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires_at, value = item
            if self._clock() >= expires_at:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._data[key] = (self._clock() + ttl, value)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in [k for k in self._data if k.startswith(prefix)]:
                del self._data[key]


class RedisCache(Cache):
    """Caché distribuida: todas las réplicas del servicio comparten las entradas."""

    def __init__(self, client: redis.Redis, namespace: str = "frescoruta:cache") -> None:
        self.client = client
        self.namespace = namespace

    def _k(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def get(self, key: str) -> Any | None:
        try:
            raw = self.client.get(self._k(key))
        except redis.RedisError:
            log.warning("Caché no disponible; se lee de la base de datos")
            return None
        return json.loads(raw) if raw else None

    def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            self.client.set(self._k(key), json.dumps(value, default=str), ex=ttl)
        except redis.RedisError:
            log.warning("No se pudo escribir en la caché")

    def delete_prefix(self, prefix: str) -> None:
        try:
            keys = list(self.client.scan_iter(match=f"{self._k(prefix)}*"))
            if keys:
                self.client.delete(*keys)
        except redis.RedisError:
            log.warning("No se pudo invalidar la caché")


def create_cache(redis_client: redis.Redis | None, namespace: str) -> Cache:
    return RedisCache(redis_client, namespace) if redis_client is not None else InMemoryTTLCache()
