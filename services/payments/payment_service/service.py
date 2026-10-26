"""Procesamiento de pagos idempotente (Idempotency Key)."""

from __future__ import annotations

import logging

from frescoruta_common.events import Event, EventBus
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .chaos import ChaosMonkey
from .models import Payment, PaymentStatus
from .schemas import PaymentOut, PaymentRequest
from .strategies import PaymentStrategyFactory

log = logging.getLogger("payment-service")
SOURCE = "payment-service"


class PaymentError(Exception):
    code = "PAYMENT_ERROR"
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PaymentNotFoundError(PaymentError):
    code = "PAYMENT_NOT_FOUND"
    status_code = 404


class IdempotencyConflictError(PaymentError):
    code = "IDEMPOTENCY_CONFLICT"
    status_code = 409


class InvalidPaymentStateError(PaymentError):
    code = "INVALID_PAYMENT_STATE"
    status_code = 409


class PaymentService:
    def __init__(
        self,
        session_factory: sessionmaker,
        strategies: PaymentStrategyFactory,
        chaos: ChaosMonkey,
        event_bus: EventBus,
    ) -> None:
        self.session_factory = session_factory
        self.strategies = strategies
        self.chaos = chaos
        self.event_bus = event_bus

    def process(self, request: PaymentRequest, idempotency_key: str) -> tuple[PaymentOut, bool]:
        """Autoriza un pago. Devuelve ``(pago, replay)``.

        Si la misma ``Idempotency-Key`` ya fue procesada se devuelve el
        resultado original sin cobrar dos veces: esto hace seguros los
        reintentos automáticos del servicio de pedidos.
        """
        self.chaos.maybe_fail()  # puede lanzar ChaosFailure -> HTTP 503
        strategy = self.strategies.get(request.method)

        with self.session_factory() as session, session.begin():
            existing = session.scalars(
                select(Payment).where(Payment.idempotency_key == idempotency_key)
            ).first()
            if existing:
                if existing.order_id != request.order_id or existing.amount != request.amount:
                    raise IdempotencyConflictError(
                        "La Idempotency-Key ya se usó con datos diferentes"
                    )
                return PaymentOut.model_validate(existing), True

            decision = strategy.authorize(request.order_id, request.amount)
            payment = Payment(
                order_id=request.order_id,
                idempotency_key=idempotency_key,
                amount=request.amount,
                method=strategy.method,
                status=PaymentStatus.APPROVED if decision.approved else PaymentStatus.DECLINED,
                reason=decision.reason,
                reference=decision.reference,
            )
            session.add(payment)
            session.flush()
            dto = PaymentOut.model_validate(payment)

        event_type = "payment.approved" if dto.status == PaymentStatus.APPROVED else "payment.declined"
        self.event_bus.publish(Event(type=event_type, source=SOURCE, payload=dto.model_dump()))
        log.info(f"Pago {dto.id} del pedido {dto.order_id}: {dto.status}")
        return dto, False

    def refund(self, payment_id: str) -> PaymentOut:
        """Compensación de la saga: reembolsa un pago aprobado. Idempotente."""
        with self.session_factory() as session, session.begin():
            payment = session.get(Payment, payment_id)
            if not payment:
                raise PaymentNotFoundError(f"Pago {payment_id} no existe")
            if payment.status == PaymentStatus.DECLINED:
                raise InvalidPaymentStateError("No se puede reembolsar un pago rechazado")
            changed = payment.status == PaymentStatus.APPROVED
            payment.status = PaymentStatus.REFUNDED
            dto = PaymentOut.model_validate(payment)
        if changed:
            self.event_bus.publish(Event(type="payment.refunded", source=SOURCE, payload=dto.model_dump()))
        return dto

    def get(self, payment_id: str) -> PaymentOut:
        with self.session_factory() as session:
            payment = session.get(Payment, payment_id)
            if not payment:
                raise PaymentNotFoundError(f"Pago {payment_id} no existe")
            return PaymentOut.model_validate(payment)

    def list(self, limit: int = 50) -> list[PaymentOut]:
        with self.session_factory() as session:
            rows = session.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(limit))
            return [PaymentOut.model_validate(p) for p in rows]
