"""API REST del microservicio de Catálogo e Inventario."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from frescoruta_common.cache import create_cache
from frescoruta_common.config import Settings, env_int
from frescoruta_common.db import make_engine, make_session_factory
from frescoruta_common.health import build_health_router
from frescoruta_common.runtime import Runtime, build_runtime, create_service_app
from sqlalchemy import text

from .models import Base
from .schemas import (
    ProductCreate,
    ProductOut,
    ReservationOut,
    ReservationRequest,
    StockAdjustment,
)
from .seed import PRODUCTS
from .service import CatalogError, CatalogService

SERVICE_NAME = "catalog-service"


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or Settings.from_env(SERVICE_NAME, 8001, "sqlite:///./data/catalog.db")
    runtime = runtime or build_runtime(settings)
    engine = make_engine(settings.database_url or "sqlite://")
    session_factory = make_session_factory(engine)
    cache = create_cache(runtime.redis, namespace="frescoruta:catalog")
    service = CatalogService(
        session_factory, cache, runtime.event_bus, cache_ttl=env_int("CACHE_TTL_SECONDS", 30)
    )

    def init_db() -> None:
        Base.metadata.create_all(engine)
        service.seed_if_empty(PRODUCTS)

    init_db()  # idempotente; también garantiza el esquema en pruebas
    runtime.startup_hooks.append(init_db)

    app = create_service_app(
        runtime,
        title="FrescoRuta · Catálogo e Inventario",
        description="Productos, stock y reservas de inventario (paso 1 de la saga de pedidos).",
    )
    app.state.catalog = service

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
            info=lambda: {"cache": cache.kind, "event_bus": runtime.event_bus.kind},
        )
    )

    @app.exception_handler(CatalogError)
    async def catalog_error_handler(_request: Request, exc: CatalogError):
        return JSONResponse(
            {"code": exc.code, "message": exc.message, **exc.details}, status_code=exc.status_code
        )

    @app.get("/products", response_model=list[ProductOut], tags=["productos"])
    def list_products(response: Response, category: str | None = None):
        """Lista productos aplicando Cache-Aside; el encabezado ``X-Cache`` indica HIT o MISS."""
        data, hit = service.list_products(category)
        response.headers["X-Cache"] = "HIT" if hit else "MISS"
        return data

    @app.get("/products/{product_id}", response_model=ProductOut, tags=["productos"])
    def get_product(product_id: str):
        return service.get_product(product_id)

    @app.post("/products", response_model=ProductOut, status_code=201, tags=["productos"])
    def create_product(body: ProductCreate):
        return service.create_product(body)

    @app.patch("/products/{product_id}/stock", response_model=ProductOut, tags=["productos"])
    def adjust_stock(product_id: str, body: StockAdjustment):
        return service.adjust_stock(product_id, body.delta)

    @app.post("/reservations", response_model=ReservationOut, tags=["reservas"])
    def reserve(body: ReservationRequest, response: Response):
        reservation, created = service.reserve(body.order_id, body.items)
        response.status_code = 201 if created else 200
        return reservation

    @app.post(
        "/reservations/{reservation_id}/commit", response_model=ReservationOut, tags=["reservas"]
    )
    def commit(reservation_id: str):
        return service.commit(reservation_id)

    @app.post(
        "/reservations/{reservation_id}/release", response_model=ReservationOut, tags=["reservas"]
    )
    def release(reservation_id: str):
        """Transacción de compensación de la saga."""
        return service.release(reservation_id)

    return app
