import pytest
from fastapi.testclient import TestClient
from frescoruta_common.config import Settings
from frescoruta_common.events import InMemoryEventBus
from frescoruta_common.runtime import build_runtime
from payment_service.api import create_app
from payment_service.chaos import ChaosMonkey


@pytest.fixture
def bus():
    return InMemoryEventBus()


@pytest.fixture
def chaos():
    return ChaosMonkey(rng=lambda: 0.5)


@pytest.fixture
def client(bus, chaos):
    settings = Settings(
        service_name="payment-service",
        port=8003,
        public_url="http://test",
        database_url="sqlite://",
    )
    runtime = build_runtime(settings, redis_client=None, event_bus=bus)
    with TestClient(create_app(settings, runtime, chaos)) as test_client:
        yield test_client
