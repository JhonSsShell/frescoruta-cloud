"""Conexión perezosa y tolerante a fallos con Redis / Render Key Value."""

from __future__ import annotations

import logging

import redis

log = logging.getLogger("frescoruta.redis")


def connect_redis(url: str | None) -> redis.Redis | None:
    """Devuelve un cliente Redis o ``None`` si no hay URL configurada.

    La conexión real es perezosa: si Redis no está disponible el servicio
    arranca igualmente y degrada a implementaciones en memoria.
    """
    if not url:
        log.warning("REDIS_URL no configurada: se usarán implementaciones en memoria")
        return None
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
        health_check_interval=30,
        retry_on_timeout=True,
    )


def redis_is_up(client: redis.Redis | None) -> bool:
    if client is None:
        return False
    try:
        return bool(client.ping())
    except redis.RedisError:
        return False
