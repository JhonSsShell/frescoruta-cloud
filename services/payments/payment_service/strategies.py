"""Patrón Strategy (GoF) para los medios de pago.

Cada medio de pago encapsula sus propias reglas de autorización detrás de
la interfaz ``PaymentStrategy``. Agregar un medio nuevo (p. ej. Nequi) no
requiere modificar el servicio: basta con registrar otra estrategia
(principio Abierto/Cerrado).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentDecision:
    approved: bool
    reason: str | None = None
    reference: str | None = None


class PaymentStrategy(ABC):
    method: str
    label: str

    @abstractmethod
    def authorize(self, order_id: str, amount: int) -> PaymentDecision: ...

    def _reference(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


class CardPaymentStrategy(PaymentStrategy):
    method = "CARD"
    label = "Tarjeta débito/crédito"
    LIMIT = 5_000_000

    def authorize(self, order_id: str, amount: int) -> PaymentDecision:
        if amount > self.LIMIT:
            return PaymentDecision(False, "Fondos insuficientes en la tarjeta (simulado)")
        return PaymentDecision(True, reference=self._reference("CARD"))


class PsePaymentStrategy(PaymentStrategy):
    method = "PSE"
    label = "Transferencia PSE"
    LIMIT = 20_000_000

    def authorize(self, order_id: str, amount: int) -> PaymentDecision:
        if amount > self.LIMIT:
            return PaymentDecision(False, "Supera el límite transaccional del banco (simulado)")
        return PaymentDecision(True, reference=self._reference("PSE"))


class CashOnDeliveryStrategy(PaymentStrategy):
    method = "CASH_ON_DELIVERY"
    label = "Pago contraentrega"
    LIMIT = 2_000_000

    def authorize(self, order_id: str, amount: int) -> PaymentDecision:
        if amount > self.LIMIT:
            return PaymentDecision(False, "El monto supera el tope para pago contraentrega")
        return PaymentDecision(True, "Se cobrará al momento de la entrega", self._reference("COD"))


class UnsupportedPaymentMethodError(ValueError):
    pass


class PaymentStrategyFactory:
    """Fábrica/registro de estrategias por código de medio de pago."""

    def __init__(self, strategies: list[PaymentStrategy] | None = None) -> None:
        self._strategies: dict[str, PaymentStrategy] = {}
        for strategy in strategies or [
            CardPaymentStrategy(),
            PsePaymentStrategy(),
            CashOnDeliveryStrategy(),
        ]:
            self.register(strategy)

    def register(self, strategy: PaymentStrategy) -> None:
        self._strategies[strategy.method] = strategy

    def get(self, method: str) -> PaymentStrategy:
        try:
            return self._strategies[method.upper()]
        except KeyError as exc:
            raise UnsupportedPaymentMethodError(f"Medio de pago no soportado: {method}") from exc

    def methods(self) -> list[dict]:
        return [{"method": s.method, "label": s.label} for s in self._strategies.values()]
