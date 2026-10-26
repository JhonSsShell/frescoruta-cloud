import pytest
from fastapi.testclient import TestClient
from frescoruta_common.config import Settings
from frescoruta_common.events import Event, InMemoryEventBus
from frescoruta_common.runtime import build_runtime
from notification_service.api import create_app
from notification_service.handlers import cop

CONFIRMED = {
    "order_id": "PED-ABC12345",
    "customer_name": "Hotel Andino",
    "customer_email": "compras@hotelandino.co",
    "delivery_city": "Cali",
    "payment_method": "PSE",
    "total": 1250000,
    "items": 3,
}


@pytest.fixture
def env():
    bus = InMemoryEventBus()
    settings = Settings(
        service_name="notification-service",
        port=8004,
        public_url="http://t",
        database_url="sqlite://",
    )
    runtime = build_runtime(settings, redis_client=None, event_bus=bus)
    with TestClient(create_app(settings, runtime)) as client:
        yield client, bus


def test_order_confirmed_sends_email_and_whatsapp(env):
    client, bus = env
    bus.publish(Event(type="order.confirmed", source="order-service", payload=CONFIRMED))
    notifications = client.get("/notifications").json()
    assert {n["channel"] for n in notifications} == {"EMAIL", "WHATSAPP"}
    email = next(n for n in notifications if n["channel"] == "EMAIL")
    assert email["recipient"] == "compras@hotelandino.co"
    assert "$ 1.250.000" in email["message"]


def test_duplicate_event_is_processed_once(env):
    client, bus = env
    event = Event(type="order.confirmed", source="order-service", payload=CONFIRMED)
    bus.publish(event)
    bus.publish(event)  # entrega duplicada (at-least-once)
    assert client.get("/notifications/stats").json()["total"] == 2
    assert client.get("/health").json()["duplicates_ignored"] == 2


def test_stock_low_notifies_purchasing(env):
    client, bus = env
    bus.publish(
        Event(
            type="stock.low",
            source="catalog-service",
            payload={
                "product_id": "FR-009",
                "name": "Mora de Castilla",
                "stock": 15,
                "threshold": 20,
            },
        )
    )
    [notification] = client.get("/notifications", params={"event_type": "stock.low"}).json()
    assert notification["recipient"] == "compras@frescoruta.co"


def test_unknown_events_are_ignored(env):
    client, bus = env
    bus.publish(Event(type="order.created", source="order-service", payload=CONFIRMED))
    assert client.get("/notifications").json() == []


def test_cop_format():
    assert cop(61000) == "$ 61.000"
