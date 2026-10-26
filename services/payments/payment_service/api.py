"""API REST del microservicio de Pagos."""

from __future__ import annotations

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from frescoruta_common.config import Settings, env_float, env_int
from frescoruta_common.db import make_engine, make_session_factory
from frescoruta_common.health import build_health_router
from frescoruta_common.runtime import Runtime, build_runtime, create_service_app
from sqlalchemy import text

from .chaos import ChaosFailure, ChaosMonkey
from .models import Base
from .schemas import ChaosSettings, PaymentOut, PaymentRequest
from .service import PaymentError, PaymentService
from .strategies import PaymentStrategyFactory, UnsupportedPaymentMethodError

SERVICE_NAME = "payment-service"


def create_app(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    chaos: ChaosMonkey | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env(SERVICE_NAME, 8003, "sqlite:///./data/payments.db")
    runtime = runtime or build_runtime(settings)
    engine = make_engine(settings.database_url or "sqlite://")
    Base.metadata.create_all(engine)
    chaos = chaos or ChaosMonkey(
        failure_rate=env_float("PAYMENT_FAILURE_RATE", 0.0),
        latency_ms=env_int("PAYMENT_LATENCY_MS", 0),
    )
    strategies = PaymentStrategyFactory()
    service = PaymentService(make_session_factory(engine), strategies, chaos, runtime.event_bus)

    app = create_service_app(
        runtime,
        title="FrescoRuta · Pagos",
        description="Autorización de pagos (Strategy), idempotencia e inyección de fallas.",
    )

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
            info=lambda: {"chaos": chaos.snapshot(), "event_bus": runtime.event_bus.kind},
        )
    )

    @app.exception_handler(PaymentError)
    async def payment_error_handler(_request: Request, exc: PaymentError):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status_code)

    @app.exception_handler(UnsupportedPaymentMethodError)
    async def unsupported_handler(_request: Request, exc: UnsupportedPaymentMethodError):
        return JSONResponse({"code": "UNSUPPORTED_METHOD", "message": str(exc)}, status_code=422)

    @app.exception_handler(ChaosFailure)
    async def chaos_handler(_request: Request, exc: ChaosFailure):
        return JSONResponse({"code": "GATEWAY_UNAVAILABLE", "message": str(exc)}, status_code=503)

    @app.get("/payment-methods", tags=["pagos"])
    def payment_methods():
        return strategies.methods()

    @app.post("/payments", response_model=PaymentOut, tags=["pagos"])
    def create_payment(
        body: PaymentRequest,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        payment, replayed = service.process(body, idempotency_key or f"order:{body.order_id}")
        response.status_code = 200 if replayed else 201
        response.headers["Idempotent-Replayed"] = str(replayed).lower()
        return payment

    @app.get("/payments", response_model=list[PaymentOut], tags=["pagos"])
    def list_payments(limit: int = 50):
        return service.list(min(limit, 200))

    @app.get("/payments/{payment_id}", response_model=PaymentOut, tags=["pagos"])
    def get_payment(payment_id: str):
        return service.get(payment_id)

    @app.post("/payments/{payment_id}/refund", response_model=PaymentOut, tags=["pagos"])
    def refund(payment_id: str):
        """Transacción de compensación de la saga."""
        return service.refund(payment_id)

    @app.get("/admin/chaos", tags=["administración"])
    def get_chaos():
        return chaos.snapshot()

    @app.put("/admin/chaos", tags=["administración"])
    def set_chaos(body: ChaosSettings):
        chaos.configure(body.failure_rate, body.latency_ms)
        return chaos.snapshot()

    return app
