"""Lógica de negocio del catálogo: Cache-Aside y reservas de inventario.

Las reservas son el paso 1 de la saga de pedidos y exponen operaciones de
compensación idempotentes (``release``) y de confirmación (``commit``).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from frescoruta_common.cache import Cache
from frescoruta_common.events import Event, EventBus
from sqlalchemy.orm import sessionmaker

from .models import Product, Reservation, ReservationItem, ReservationStatus
from .repository import ProductRepository, ReservationRepository
from .schemas import (
    ProductCreate,
    ProductOut,
    ReservationItemOut,
    ReservationLine,
    ReservationOut,
)

log = logging.getLogger("catalog-service")
SOURCE = "catalog-service"


class CatalogError(Exception):
    code = "CATALOG_ERROR"
    status_code = 400

    def __init__(self, message: str, **details) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ProductNotFoundError(CatalogError):
    code = "PRODUCT_NOT_FOUND"
    status_code = 404


class ProductAlreadyExistsError(CatalogError):
    code = "PRODUCT_ALREADY_EXISTS"
    status_code = 409


class InsufficientStockError(CatalogError):
    code = "INSUFFICIENT_STOCK"
    status_code = 409


class ReservationNotFoundError(CatalogError):
    code = "RESERVATION_NOT_FOUND"
    status_code = 404


class InvalidReservationStateError(CatalogError):
    code = "INVALID_RESERVATION_STATE"
    status_code = 409


def reservation_to_dto(reservation: Reservation) -> ReservationOut:
    total = sum(i.unit_price * i.quantity for i in reservation.items)
    return ReservationOut(
        id=reservation.id,
        order_id=reservation.order_id,
        status=reservation.status,
        items=[ReservationItemOut.model_validate(i) for i in reservation.items],
        total=total,
    )


class CatalogService:
    CACHE_PREFIX = "products:"

    def __init__(
        self,
        session_factory: sessionmaker,
        cache: Cache,
        event_bus: EventBus,
        cache_ttl: int = 30,
    ) -> None:
        self.session_factory = session_factory
        self.cache = cache
        self.event_bus = event_bus
        self.cache_ttl = cache_ttl

    # -- consultas (Cache-Aside) -------------------------------------------------
    def list_products(self, category: str | None = None) -> tuple[list[dict], bool]:
        key = f"{self.CACHE_PREFIX}{(category or 'all').lower()}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached, True
        with self.session_factory() as session:
            products = ProductRepository(session).list(category)
            data = [ProductOut.model_validate(p).model_dump() for p in products]
        self.cache.set(key, data, self.cache_ttl)
        return data, False

    def get_product(self, product_id: str) -> ProductOut:
        with self.session_factory() as session:
            product = ProductRepository(session).get(product_id)
            if not product:
                raise ProductNotFoundError(f"Producto {product_id} no existe", product_id=product_id)
            return ProductOut.model_validate(product)

    # -- comandos ---------------------------------------------------------------
    def create_product(self, data: ProductCreate) -> ProductOut:
        with self.session_factory() as session, session.begin():
            repo = ProductRepository(session)
            if repo.get(data.id):
                raise ProductAlreadyExistsError(f"El producto {data.id} ya existe")
            product = repo.add(Product(**data.model_dump()))
            session.flush()
            dto = ProductOut.model_validate(product)
        self._invalidate_cache()
        return dto

    def adjust_stock(self, product_id: str, delta: int) -> ProductOut:
        with self.session_factory() as session, session.begin():
            repo = ProductRepository(session)
            if not repo.get(product_id):
                raise ProductNotFoundError(f"Producto {product_id} no existe")
            if delta < 0 and not repo.try_decrement_stock(product_id, -delta):
                raise InsufficientStockError("El ajuste dejaría el stock en negativo")
            if delta > 0:
                repo.increment_stock(product_id, delta)
        self._invalidate_cache()
        return self.get_product(product_id)

    def reserve(self, order_id: str, lines: list[ReservationLine]) -> tuple[ReservationOut, bool]:
        """Reserva stock para un pedido. Idempotente por ``order_id``.

        Devuelve ``(reserva, creada)``; si la reserva ya existía se devuelve
        tal cual (permite reintentos seguros desde la saga).
        """
        quantities: dict[str, int] = defaultdict(int)
        for line in lines:
            quantities[line.product_id] += line.quantity

        with self.session_factory() as session, session.begin():
            reservations = ReservationRepository(session)
            existing = reservations.get_by_order(order_id)
            if existing:
                return reservation_to_dto(existing), False

            products_repo = ProductRepository(session)
            products = products_repo.get_many(list(quantities))
            missing = sorted(set(quantities) - set(products))
            if missing:
                raise ProductNotFoundError("Productos inexistentes", product_ids=missing)

            shortages = []
            for product_id, qty in quantities.items():
                if not products_repo.try_decrement_stock(product_id, qty):
                    shortages.append(
                        {
                            "product_id": product_id,
                            "name": products[product_id].name,
                            "requested": qty,
                            "available": products[product_id].stock,
                        }
                    )
            if shortages:
                # Al lanzar dentro de session.begin() se revierte todo: no hay reservas parciales.
                raise InsufficientStockError("Stock insuficiente", shortages=shortages)

            reservation = reservations.add(
                Reservation(
                    order_id=order_id,
                    items=[
                        ReservationItem(
                            product_id=pid,
                            product_name=products[pid].name,
                            unit_price=products[pid].price,
                            quantity=qty,
                        )
                        for pid, qty in quantities.items()
                    ],
                )
            )
            session.flush()
            dto = reservation_to_dto(reservation)

        self._invalidate_cache()
        self._publish_low_stock(list(quantities))
        log.info(f"Reserva {dto.id} creada para el pedido {order_id} (total {dto.total})")
        return dto, True

    def release(self, reservation_id: str) -> ReservationOut:
        """Compensación: devuelve el stock reservado. Idempotente."""
        with self.session_factory() as session, session.begin():
            reservation = self._get_reservation(session, reservation_id)
            if reservation.status == ReservationStatus.COMMITTED:
                raise InvalidReservationStateError("La reserva ya fue confirmada")
            if reservation.status == ReservationStatus.RESERVED:
                repo = ProductRepository(session)
                for item in reservation.items:
                    repo.increment_stock(item.product_id, item.quantity)
                reservation.status = ReservationStatus.RELEASED
            dto = reservation_to_dto(reservation)
        self._invalidate_cache()
        log.info(f"Reserva {reservation_id} liberada (compensación)")
        return dto

    def commit(self, reservation_id: str) -> ReservationOut:
        """Confirma definitivamente la salida de inventario. Idempotente."""
        with self.session_factory() as session, session.begin():
            reservation = self._get_reservation(session, reservation_id)
            if reservation.status == ReservationStatus.RELEASED:
                raise InvalidReservationStateError("La reserva fue liberada")
            reservation.status = ReservationStatus.COMMITTED
            return reservation_to_dto(reservation)

    # -- auxiliares -------------------------------------------------------------
    @staticmethod
    def _get_reservation(session, reservation_id: str) -> Reservation:
        reservation = ReservationRepository(session).get(reservation_id)
        if not reservation:
            raise ReservationNotFoundError(f"Reserva {reservation_id} no existe")
        return reservation

    def _invalidate_cache(self) -> None:
        self.cache.delete_prefix(self.CACHE_PREFIX)

    def _publish_low_stock(self, product_ids: list[str]) -> None:
        with self.session_factory() as session:
            for product in ProductRepository(session).get_many(product_ids).values():
                if product.is_low_stock:
                    self.event_bus.publish(
                        Event(
                            type="stock.low",
                            source=SOURCE,
                            payload={
                                "product_id": product.id,
                                "name": product.name,
                                "stock": product.stock,
                                "threshold": product.low_stock_threshold,
                            },
                        )
                    )

    def seed_if_empty(self, products: list[dict]) -> int:
        with self.session_factory() as session, session.begin():
            repo = ProductRepository(session)
            if repo.count() > 0:
                return 0
            for data in products:
                repo.add(Product(**data))
        self._invalidate_cache()
        return len(products)
