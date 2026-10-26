"""Puertos y adaptadores hacia los servicios de Catálogo y Pagos.

La saga depende de las interfaces (``CatalogPort``, ``PaymentPort``) y no de
HTTP: en pruebas se reemplazan por dobles en memoria (inversión de
dependencias / arquitectura hexagonal).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from frescoruta_common.http import ServiceClient


class BusinessRejectionError(Exception):
    """Respuesta 4xx de un servicio: es una decisión de negocio, no una falla técnica."""

    def __init__(self, message: str, payload: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.payload = payload or {}


class InsufficientStockError(BusinessRejectionError):
    pass


class CatalogPort(ABC):
    @abstractmethod
    def reserve(self, order_id: str, items: list[dict]) -> dict: ...

    @abstractmethod
    def commit(self, reservation_id: str) -> dict: ...

    @abstractmethod
    def release(self, reservation_id: str) -> dict: ...


class PaymentPort(ABC):
    @abstractmethod
    def pay(self, order_id: str, amount: int, method: str) -> dict: ...

    @abstractmethod
    def refund(self, payment_id: str) -> dict: ...


def _raise_for_business(response, default_message: str) -> None:
    if response.status_code < 400:
        return
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    message = payload.get("message") or payload.get("detail") or default_message
    if payload.get("code") == "INSUFFICIENT_STOCK":
        raise InsufficientStockError(str(message), payload)
    raise BusinessRejectionError(str(message), payload)


class HttpCatalogClient(CatalogPort):
    def __init__(self, client: ServiceClient) -> None:
        self.client = client

    def reserve(self, order_id: str, items: list[dict]) -> dict:
        resp = self.client.request(
            "POST", "/reservations", json={"order_id": order_id, "items": items}
        )
        _raise_for_business(resp, "Reserva rechazada por el catálogo")
        return resp.json()

    def commit(self, reservation_id: str) -> dict:
        resp = self.client.request("POST", f"/reservations/{reservation_id}/commit")
        _raise_for_business(resp, "No se pudo confirmar la reserva")
        return resp.json()

    def release(self, reservation_id: str) -> dict:
        resp = self.client.request("POST", f"/reservations/{reservation_id}/release")
        _raise_for_business(resp, "No se pudo liberar la reserva")
        return resp.json()


class HttpPaymentClient(PaymentPort):
    def __init__(self, client: ServiceClient) -> None:
        self.client = client

    def pay(self, order_id: str, amount: int, method: str) -> dict:
        # La misma clave en cada reintento evita cobros duplicados.
        resp = self.client.request(
            "POST",
            "/payments",
            json={"order_id": order_id, "amount": amount, "method": method},
            headers={"Idempotency-Key": f"order:{order_id}"},
        )
        _raise_for_business(resp, "Pago rechazado")
        return resp.json()

    def refund(self, payment_id: str) -> dict:
        resp = self.client.request("POST", f"/payments/{payment_id}/refund")
        _raise_for_business(resp, "No se pudo reembolsar el pago")
        return resp.json()
