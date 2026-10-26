"""API REST del microservicio de Pedidos."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Response
from frescoruta_common.config import Settings, env_float, env_int
from frescoruta_common.db import make_engine, make_session_factory
from frescoruta_common.health import build_health_router
from frescoruta_common.http import ServiceClient
from frescoruta_common.resilience import CircuitBreaker
from frescoruta_common.runtime import Runtime, build_runtime, create_service_app
from sqlalchemy import text

from .clients import CatalogPort, HttpCatalogClient, HttpPaymentClient, PaymentPort
from .models import Base, Order, OrderItem
from .repository import OrderRepository
from .saga import CreateOrderSaga
from .schemas import OrderCreate, OrderOut

SERVICE_NAME = "order-service"


def create_app(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    catalog: CatalogPort | None = None,
    payments: PaymentPort | None = None,
    breakers: dict[str, CircuitBreaker] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env(SERVICE_NAME, 8002, "sqlite:///./data/orders.db")
    runtime = runtime or build_runtime(settings)
    engine = make_engine(settings.database_url or "sqlite://")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    breakers = breakers or {
        name: CircuitBreaker(
            name,
            failure_threshold=env_int("CB_FAILURE_THRESHOLD", 3),
            recovery_timeout=env_float("CB_RECOVERY_TIMEOUT_S", 20.0),
        )
        for name in ("catalog-service", "payment-service")
    }

    def service_client(name: str) -> ServiceClient:
        return ServiceClient(
            name,
            runtime.resolver,
            timeout=env_float("DOWNSTREAM_TIMEOUT_S", 8.0),
            retries=env_int("DOWNSTREAM_RETRIES", 2),
            breaker=breakers[name],
            internal_token=settings.internal_token,
        )

    catalog = catalog or HttpCatalogClient(service_client("catalog-service"))
    payments = payments or HttpPaymentClient(service_client("payment-service"))
    saga = CreateOrderSaga(session_factory, catalog, payments, runtime.event_bus)

    app = create_service_app(
        runtime,
        title="FrescoRuta · Pedidos",
        description="Creación de pedidos mediante una Saga orquestada con compensaciones.",
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
            info=lambda: {"circuit_breakers": [b.snapshot() for b in breakers.values()]},
        )
    )

    @app.post("/orders", response_model=OrderOut, tags=["pedidos"])
    def create_order(
        body: OrderCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        """Crea un pedido y ejecuta la saga. El resultado final es CONFIRMED o REJECTED."""
        with session_factory() as session, session.begin():
            repo = OrderRepository(session)
            if idempotency_key and (existing := repo.get_by_idempotency_key(idempotency_key)):
                response.status_code = 200
                response.headers["Idempotent-Replayed"] = "true"
                return OrderOut.model_validate(existing)
            order = repo.add(
                Order(
                    customer_name=body.customer_name,
                    customer_email=body.customer_email,
                    delivery_city=body.delivery_city,
                    payment_method=body.payment_method,
                    idempotency_key=idempotency_key,
                    items=[
                        OrderItem(product_id=line.product_id, quantity=line.quantity)
                        for line in body.items
                    ],
                )
            )
            session.flush()
            order_id = order.id
        response.status_code = 201
        return saga.run(order_id)

    @app.get("/orders", response_model=list[OrderOut], tags=["pedidos"])
    def list_orders(status: str | None = None, limit: int = 20):
        with session_factory() as session:
            orders = OrderRepository(session).list(status, min(limit, 100))
            return [OrderOut.model_validate(o) for o in orders]

    @app.get("/orders/stats", tags=["pedidos"])
    def order_stats():
        with session_factory() as session:
            return OrderRepository(session).stats()

    @app.get("/orders/{order_id}", response_model=OrderOut, tags=["pedidos"])
    def get_order(order_id: str):
        with session_factory() as session:
            order = OrderRepository(session).get(order_id)
            if not order:
                raise HTTPException(404, detail=f"Pedido {order_id} no existe")
            return OrderOut.model_validate(order)

    @app.get("/circuit-breakers", tags=["resiliencia"])
    def circuit_breakers():
        return [b.snapshot() for b in breakers.values()]

    @app.post("/circuit-breakers/reset", tags=["resiliencia"])
    def reset_circuit_breakers():
        for breaker in breakers.values():
            breaker.reset()
        return [b.snapshot() for b in breakers.values()]

    return app
