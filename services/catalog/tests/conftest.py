import pytest
from catalog_service.api import create_app
from fastapi.testclient import TestClient
from frescoruta_common.config import Settings
from frescoruta_common.events import InMemoryEventBus
from frescoruta_common.runtime import build_runtime


@pytest.fixture
def bus():
    return InMemoryEventBus()


@pytest.fixture
def client(bus):
    settings = Settings(
        service_name="catalog-service", port=8001, public_url="http://test", database_url="sqlite://"
    )
    runtime = build_runtime(settings, redis_client=None, event_bus=bus)
    with TestClient(create_app(settings, runtime)) as test_client:
        yield test_client
