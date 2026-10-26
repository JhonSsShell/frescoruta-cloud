"""Ensamblado común de un microservicio (Microservice Chassis).

Crea la aplicación FastAPI con middlewares, logging, conexión a Redis,
auto-registro en el Service Registry y ciclo de vida del bus de eventos.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import redis
from fastapi import FastAPI

from .config import Settings, env_str
from .events import EventBus, create_event_bus
from .logging_setup import configure_logging
from .middleware import CorrelationIdMiddleware, InternalAuthMiddleware
from .redis_conn import connect_redis, redis_is_up
from .registry import RegistrationAgent, ServiceRegistry, ServiceResolver

_UNSET = object()


@dataclass
class Runtime:
    settings: Settings
    redis: redis.Redis | None
    registry: ServiceRegistry
    resolver: ServiceResolver
    event_bus: EventBus
    agent: RegistrationAgent
    startup_hooks: list[Callable[[], None]] = field(default_factory=list)
    shutdown_hooks: list[Callable[[], None]] = field(default_factory=list)

    def redis_check(self) -> str:
        if self.redis is None:
            return "N/A"
        return "UP" if redis_is_up(self.redis) else "DOWN"


def build_runtime(
    settings: Settings,
    *,
    redis_client: redis.Redis | None | object = _UNSET,
    event_bus: EventBus | None = None,
    resolver: ServiceResolver | None = None,
) -> Runtime:
    client = connect_redis(settings.redis_url) if redis_client is _UNSET else redis_client
    registry = ServiceRegistry(client)  # type: ignore[arg-type]
    bus = event_bus or create_event_bus(
        client,  # type: ignore[arg-type]
        group=settings.service_name,
        consumer=env_str("CONSUMER_NAME", "consumer-1"),
    )
    return Runtime(
        settings=settings,
        redis=client,  # type: ignore[arg-type]
        registry=registry,
        resolver=resolver or ServiceResolver(registry),
        event_bus=bus,
        agent=RegistrationAgent(
            registry, settings.service_name, settings.public_url, settings.version
        ),
    )


def create_service_app(
    runtime: Runtime, *, title: str, description: str, internal_auth: bool = True
) -> FastAPI:
    settings = runtime.settings
    log = configure_logging(settings.service_name, settings.log_level)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        for hook in runtime.startup_hooks:
            hook()
        runtime.agent.start()
        runtime.event_bus.start()
        log.info(
            f"{settings.service_name} iniciado en el puerto {settings.port} "
            f"(entorno={settings.environment}, bus={runtime.event_bus.kind})"
        )
        yield
        runtime.event_bus.stop()
        runtime.agent.stop()
        for hook in runtime.shutdown_hooks:
            hook()
        logging.getLogger(settings.service_name).info("Servicio detenido")

    app = FastAPI(title=title, description=description, version="1.0.0", lifespan=lifespan)
    # El último middleware agregado es el más externo: la correlación envuelve todo.
    if internal_auth:
        app.add_middleware(InternalAuthMiddleware, token=settings.internal_token)
    app.add_middleware(CorrelationIdMiddleware)
    app.state.runtime = runtime
    return app
