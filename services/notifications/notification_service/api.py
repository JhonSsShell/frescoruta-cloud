"""API del microservicio de Notificaciones."""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from frescoruta_common.config import Settings
from frescoruta_common.db import make_engine, make_session_factory
from frescoruta_common.health import build_health_router
from frescoruta_common.runtime import Runtime, build_runtime, create_service_app
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text

from .handlers import NotificationDispatcher
from .models import Base, Notification

SERVICE_NAME = "notification-service"


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    channel: str
    recipient: str
    subject: str
    message: str
    delivery_id: str
    correlation_id: str | None
    created_at: datetime


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or Settings.from_env(
        SERVICE_NAME, 8004, "sqlite:///./data/notifications.db"
    )
    runtime = runtime or build_runtime(settings)
    engine = make_engine(settings.database_url or "sqlite://")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    dispatcher = NotificationDispatcher(session_factory)

    # Suscripción a los eventos de dominio (Publisher-Subscriber).
    runtime.event_bus.subscribe(dispatcher.event_types, dispatcher.handle)

    app = create_service_app(
        runtime,
        title="FrescoRuta · Notificaciones",
        description="Consume eventos de dominio y notifica por correo y WhatsApp (simulados).",
    )
    app.state.dispatcher = dispatcher

    def db_check() -> bool:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    app.include_router(
        build_health_router(
            SERVICE_NAME,
            settings.version,
            checks={"database": db_check, "redis": runtime.redis_check},
            critical={"database"},
            info=lambda: {
                "event_bus": runtime.event_bus.kind,
                "subscribed_to": dispatcher.event_types,
                "events_processed": getattr(runtime.event_bus, "processed", None),
                "duplicates_ignored": dispatcher.duplicates_ignored,
            },
        )
    )

    @app.get("/notifications", response_model=list[NotificationOut], tags=["notificaciones"])
    def list_notifications(limit: int = 30, event_type: str | None = None):
        with session_factory() as session:
            stmt = (
                select(Notification).order_by(Notification.created_at.desc()).limit(min(limit, 200))
            )
            if event_type:
                stmt = stmt.where(Notification.event_type == event_type)
            return [NotificationOut.model_validate(n) for n in session.scalars(stmt)]

    @app.get("/notifications/stats", tags=["notificaciones"])
    def stats():
        with session_factory() as session:
            rows = session.execute(
                select(Notification.channel, func.count()).group_by(Notification.channel)
            ).all()
            return {"by_channel": {c: n for c, n in rows}, "total": sum(n for _, n in rows)}

    return app
