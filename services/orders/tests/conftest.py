import pytest
from fastapi.testclient import TestClient
from frescoruta_common.config import Settings
from frescoruta_common.events import InMemoryEventBus
from frescoruta_common.http import ServiceUnavailableError
from frescoruta_common.resilience import CircuitBreaker
from frescoruta_common.runtime import build_runtime
from order_service.api import create_app
from order_service.clients import CatalogPort, InsufficientStockError, PaymentPort

PRICES = {"FR-001": ("Tomate chonto", 4200), "FR-005": ("Aguacate Hass", 9500)}


class FakeCatalog(CatalogPort):
    def __init__(self):
        self.stock = {"FR-001": 100, "FR-005": 10}
        self.reservations = {}
        self.released, self.committed = [], []
        self.fail_commit = False

    def reserve(self, order_id, items):
        shortages = [
            {"product_id": i["product_id"], "name": PRICES[i["product_id"]][0],
             "requested": i["quantity"], "available": self.stock[i["product_id"]]}
            for i in items if self.stock[i["product_id"]] < i["quantity"]
        ]
        if shortages:
            raise InsufficientStockError("Stock insuficiente", {"shortages": shortages})
        for i in items:
            self.stock[i["product_id"]] -= i["quantity"]
        rid = f"res-{len(self.reservations) + 1}"
        lines = [
            {"product_id": i["product_id"], "product_name": PRICES[i["product_id"]][0],
             "unit_price": PRICES[i["product_id"]][1], "quantity": i["quantity"]}
            for i in items
        ]
        self.reservations[rid] = items
        return {"id": rid, "items": lines, "total": sum(x["unit_price"] * x["quantity"] for x in lines)}

    def commit(self, reservation_id):
        if self.fail_commit:
            raise ServiceUnavailableError("catalog-service", "HTTP 503")
        self.committed.append(reservation_id)
        return {}

    def release(self, reservation_id):
        for i in self.reservations[reservation_id]:
            self.stock[i["product_id"]] += i["quantity"]
        self.released.append(reservation_id)
        return {}


class FakePayments(PaymentPort):
    def __init__(self, breaker):
        self.breaker = breaker
        self.mode = "approve"
        self.calls = 0
        self.refunded = []

    def _do(self, amount):
        self.calls += 1
        if self.mode == "down":
            raise ServiceUnavailableError("payment-service", "HTTP 503")
        if self.mode == "decline":
            return {"id": "PAY-2", "status": "DECLINED", "reason": "Fondos insuficientes"}
        return {"id": "PAY-1", "status": "APPROVED"}

    def pay(self, order_id, amount, method):
        return self.breaker.call(lambda: self._do(amount))

    def refund(self, payment_id):
        self.refunded.append(payment_id)
        return {}


@pytest.fixture
def env():
    bus = InMemoryEventBus()
    breakers = {
        "catalog-service": CircuitBreaker("catalog-service", failure_threshold=2),
        "payment-service": CircuitBreaker("payment-service", failure_threshold=2, recovery_timeout=60),
    }
    catalog = FakeCatalog()
    payments = FakePayments(breakers["payment-service"])
    settings = Settings(
        service_name="order-service", port=8002, public_url="http://test", database_url="sqlite://"
    )
    runtime = build_runtime(settings, redis_client=None, event_bus=bus)
    app = create_app(settings, runtime, catalog, payments, breakers)
    with TestClient(app) as client:
        yield {"client": client, "bus": bus, "catalog": catalog, "payments": payments, "breakers": breakers}
