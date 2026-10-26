"""Patrón Saga orquestada para crear un pedido distribuido.

No existe una transacción ACID que abarque las bases de datos de Catálogo,
Pagos y Pedidos (Database per Service). La saga coordina transacciones
locales y, si un paso falla, ejecuta transacciones de compensación en orden
inverso:

    T1 reservar stock  ──▶ T2 cobrar ──▶ T3 confirmar reserva ──▶ pedido CONFIRMED
         ▲                    │                 │
         └── C1 liberar ◀─────┘                 │
         └── C1 liberar ◀── C2 reembolsar ◀─────┘

Cada paso queda registrado en la bitácora de la saga (tabla ``saga_steps``),
lo que da trazabilidad completa del proceso.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from frescoruta_common.events import Event, EventBus
from frescoruta_common.http import ServiceUnavailableError
from frescoruta_common.resilience import CircuitBreakerOpenError
from sqlalchemy.orm import sessionmaker

from .clients import BusinessRejectionError, CatalogPort, InsufficientStockError, PaymentPort
from .models import Order, OrderStatus, StepStatus
from .repository import OrderRepository
from .schemas import OrderOut

log = logging.getLogger("order-service.saga")
SOURCE = "order-service"
TECHNICAL_ERRORS = (ServiceUnavailableError, CircuitBreakerOpenError)

RESERVE_STOCK = "reserve_stock"
PROCESS_PAYMENT = "process_payment"
COMMIT_STOCK = "commit_stock"
RELEASE_STOCK = "release_stock"
REFUND_PAYMENT = "refund_payment"
CONFIRM_ORDER = "confirm_order"
REJECT_ORDER = "reject_order"


def _technical_reason(exc: Exception) -> str:
    if isinstance(exc, CircuitBreakerOpenError):
        return f"circuito abierto ({exc.name}); se falló rápido sin invocar el servicio"
    return str(exc)


class CreateOrderSaga:
    def __init__(
        self,
        session_factory: sessionmaker,
        catalog: CatalogPort,
        payments: PaymentPort,
        event_bus: EventBus,
    ) -> None:
        self.session_factory = session_factory
        self.catalog = catalog
        self.payments = payments
        self.event_bus = event_bus

    # -- utilidades de persistencia (cada paso es una transacción local) ---------
    def _mutate(self, order_id: str, fn: Callable[[Order, OrderRepository], None]) -> OrderOut:
        with self.session_factory() as session, session.begin():
            repo = OrderRepository(session)
            order = repo.get(order_id)
            fn(order, repo)
            session.flush()
            return OrderOut.model_validate(order)

    def _step(self, order_id: str, step: str, status: str, detail: str | None = None) -> OrderOut:
        return self._mutate(order_id, lambda o, r: r.add_step(o, step, status, detail))

    def _publish(self, event_type: str, order: OrderOut, **extra) -> None:
        payload = {
            "order_id": order.id,
            "customer_name": order.customer_name,
            "customer_email": order.customer_email,
            "delivery_city": order.delivery_city,
            "payment_method": order.payment_method,
            "total": order.total,
            "items": len(order.items),
            **extra,
        }
        self.event_bus.publish(Event(type=event_type, source=SOURCE, payload=payload))

    # -- ejecución -----------------------------------------------------------------
    def run(self, order_id: str) -> OrderOut:
        order = self._mutate(order_id, lambda o, r: None)
        self._publish("order.created", order)

        # T1: reservar inventario --------------------------------------------------
        items = [{"product_id": i.product_id, "quantity": i.quantity} for i in order.items]
        try:
            reservation = self.catalog.reserve(order.id, items)
        except InsufficientStockError as exc:
            detail = "; ".join(
                f"{s['name']}: solicitado {s['requested']}, disponible {s['available']}"
                for s in exc.payload.get("shortages", [])
            )
            self._step(order_id, RESERVE_STOCK, StepStatus.FAILED, detail or exc.message)
            return self._reject(order_id, f"Stock insuficiente — {detail or exc.message}")
        except BusinessRejectionError as exc:
            self._step(order_id, RESERVE_STOCK, StepStatus.FAILED, exc.message)
            return self._reject(order_id, f"Pedido inválido — {exc.message}")
        except TECHNICAL_ERRORS as exc:
            self._step(order_id, RESERVE_STOCK, StepStatus.FAILED, _technical_reason(exc))
            return self._reject(order_id, "Catálogo no disponible, intente más tarde")

        def apply_reservation(o: Order, r: OrderRepository) -> None:
            prices = {i["product_id"]: i for i in reservation["items"]}
            for item in o.items:
                item.product_name = prices[item.product_id]["product_name"]
                item.unit_price = prices[item.product_id]["unit_price"]
            o.total = reservation["total"]
            o.reservation_id = reservation["id"]
            r.add_step(o, RESERVE_STOCK, StepStatus.SUCCEEDED, f"Reserva {reservation['id'][:8]}")

        order = self._mutate(order_id, apply_reservation)

        # T2: procesar el pago -----------------------------------------------------
        try:
            payment = self.payments.pay(order.id, order.total, order.payment_method)
        except BusinessRejectionError as exc:
            self._step(order_id, PROCESS_PAYMENT, StepStatus.FAILED, exc.message)
            self._compensate_release(order)
            return self._reject(order_id, f"Pago rechazado — {exc.message}")
        except TECHNICAL_ERRORS as exc:
            self._step(order_id, PROCESS_PAYMENT, StepStatus.FAILED, _technical_reason(exc))
            self._compensate_release(order)
            return self._reject(
                order_id, "Pasarela de pagos no disponible; no se realizó ningún cobro"
            )

        if payment.get("status") != "APPROVED":
            reason = payment.get("reason") or "rechazado por la entidad"
            self._step(order_id, PROCESS_PAYMENT, StepStatus.FAILED, reason)
            self._compensate_release(order)
            return self._reject(order_id, f"Pago rechazado — {reason}")

        def apply_payment(o: Order, r: OrderRepository) -> None:
            o.payment_id = payment["id"]
            r.add_step(o, PROCESS_PAYMENT, StepStatus.SUCCEEDED, f"{payment['id']} aprobado")

        order = self._mutate(order_id, apply_payment)

        # T3: confirmar la salida de inventario ------------------------------------
        try:
            self.catalog.commit(order.reservation_id)
        except (BusinessRejectionError, *TECHNICAL_ERRORS) as exc:
            self._step(order_id, COMMIT_STOCK, StepStatus.FAILED, _technical_reason(exc))
            self._compensate_refund(order)
            self._compensate_release(order)
            return self._reject(order_id, "No se pudo confirmar el inventario; pago reembolsado")
        self._step(order_id, COMMIT_STOCK, StepStatus.SUCCEEDED)

        def confirm(o: Order, r: OrderRepository) -> None:
            o.status = OrderStatus.CONFIRMED
            r.add_step(o, CONFIRM_ORDER, StepStatus.SUCCEEDED)

        order = self._mutate(order_id, confirm)
        self._publish("order.confirmed", order)
        log.info(f"Pedido {order_id} CONFIRMADO por {order.total} COP")
        return order

    # -- compensaciones -----------------------------------------------------------
    def _compensate_release(self, order: OrderOut) -> None:
        if not order.reservation_id:
            return
        try:
            self.catalog.release(order.reservation_id)
            self._step(
                order.id, RELEASE_STOCK, StepStatus.COMPENSATED, "Stock devuelto al inventario"
            )
        except Exception as exc:  # noqa: BLE001
            log.error(f"Compensación pendiente para la reserva {order.reservation_id}: {exc}")
            self._step(
                order.id, RELEASE_STOCK, StepStatus.FAILED, f"Pendiente de reconciliación: {exc}"
            )

    def _compensate_refund(self, order: OrderOut) -> None:
        if not order.payment_id:
            return
        try:
            self.payments.refund(order.payment_id)
            self._step(order.id, REFUND_PAYMENT, StepStatus.COMPENSATED, "Pago reembolsado")
        except Exception as exc:  # noqa: BLE001
            log.error(f"Reembolso pendiente para el pago {order.payment_id}: {exc}")
            self._step(
                order.id, REFUND_PAYMENT, StepStatus.FAILED, f"Pendiente de reconciliación: {exc}"
            )

    def _reject(self, order_id: str, reason: str) -> OrderOut:
        def reject(o: Order, r: OrderRepository) -> None:
            o.status = OrderStatus.REJECTED
            o.rejection_reason = reason[:300]
            r.add_step(o, REJECT_ORDER, StepStatus.SUCCEEDED, reason)

        order = self._mutate(order_id, reject)
        self._publish("order.rejected", order, reason=reason)
        log.warning(f"Pedido {order_id} RECHAZADO: {reason}")
        return order
