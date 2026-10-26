import fakeredis
import httpx
import pytest
from frescoruta_common.cache import InMemoryTTLCache, RedisCache
from frescoruta_common.events import Event, InMemoryEventBus, RedisStreamEventBus
from frescoruta_common.http import ServiceClient, ServiceUnavailableError
from frescoruta_common.registry import ServiceRegistry, ServiceResolver
from frescoruta_common.resilience import CircuitBreaker, CircuitBreakerOpenError


def test_in_memory_bus_dispatches_by_type_and_wildcard():
    bus = InMemoryEventBus()
    seen, all_seen = [], []
    bus.subscribe(["order.confirmed"], seen.append)
    bus.subscribe(["*"], all_seen.append)
    bus.publish(Event(type="order.confirmed", source="t", payload={"id": 1}))
    bus.publish(Event(type="stock.low", source="t", payload={}))
    assert [e.type for e in seen] == ["order.confirmed"]
    assert len(all_seen) == 2


def test_redis_stream_bus_delivers_and_acks():
    client = fakeredis.FakeRedis(decode_responses=True)
    publisher = RedisStreamEventBus(client, group="publisher")
    consumer = RedisStreamEventBus(client, group="notification-service", block_ms=10)
    received = []
    consumer.subscribe(["order.created"], received.append)
    consumer._ensure_group()
    publisher.publish(Event(type="order.created", source="orders", payload={"order_id": "PED-1"}))
    assert consumer.poll_once(">") == 1
    assert received[0].payload["order_id"] == "PED-1"
    pending = client.xpending(consumer.stream, consumer.group)
    assert pending["pending"] == 0


def test_redis_stream_bus_sends_poison_message_to_dlq():
    client = fakeredis.FakeRedis(decode_responses=True)
    bus = RedisStreamEventBus(client, group="g", block_ms=10)

    def failing(_event):
        raise RuntimeError("handler roto")

    bus.subscribe(["*"], failing)
    bus._ensure_group()
    bus.publish(Event(type="x", source="t", payload={}))
    bus.poll_once(">")
    for _ in range(RedisStreamEventBus.MAX_DELIVERIES - 1):
        bus.poll_once("0")
    assert client.xlen(bus.dlq_stream) == 1
    assert client.xpending(bus.stream, bus.group)["pending"] == 0


def test_orphan_messages_are_claimed_by_another_consumer():
    client = fakeredis.FakeRedis(decode_responses=True)
    dead = RedisStreamEventBus(client, group="notification-service", consumer="old", block_ms=10)
    dead._ensure_group()
    dead.publish(Event(type="order.confirmed", source="t", payload={}))
    client.xreadgroup(dead.group, "old", {dead.stream: ">"}, count=10)  # leído pero sin ACK
    alive = RedisStreamEventBus(client, group="notification-service", consumer="new", block_ms=10)
    received = []
    alive.subscribe(["*"], received.append)
    assert alive.claim_orphans(min_idle_ms=0) == 1
    alive.poll_once("0")
    assert len(received) == 1
    assert client.xpending(alive.stream, alive.group)["pending"] == 0


def test_in_memory_cache_expires():
    now = {"t": 0}
    cache = InMemoryTTLCache(clock=lambda: now["t"])
    cache.set("catalog:products", [1, 2], ttl=30)
    assert cache.get("catalog:products") == [1, 2]
    now["t"] = 31
    assert cache.get("catalog:products") is None


def test_redis_cache_roundtrip_and_prefix_invalidation():
    cache = RedisCache(fakeredis.FakeRedis(decode_responses=True), namespace="test")
    cache.set("products:all", {"a": 1}, ttl=30)
    cache.set("products:frutas", {"b": 2}, ttl=30)
    assert cache.get("products:all") == {"a": 1}
    cache.delete_prefix("products:")
    assert cache.get("products:all") is None and cache.get("products:frutas") is None


def test_registry_resolution_order(monkeypatch):
    registry = ServiceRegistry(fakeredis.FakeRedis(decode_responses=True))
    resolver = ServiceResolver(registry, cache_ttl=0)
    assert resolver.resolve("catalog-service") == "http://localhost:8001"
    monkeypatch.setenv("CATALOG_SERVICE_URL", "http://catalog:9000/")
    assert resolver.resolve("catalog-service") == "http://catalog:9000"
    registry.register("catalog-service", "https://catalog.onrender.com")
    assert resolver.resolve("catalog-service") == "https://catalog.onrender.com"
    with pytest.raises(LookupError):
        resolver.resolve("desconocido")


def test_service_client_maps_5xx_to_unavailable_and_opens_breaker():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={"detail": "caído"})

    breaker = CircuitBreaker("payment-service", failure_threshold=3)
    client = ServiceClient(
        "payment-service",
        ServiceResolver(overrides={"payment-service": "http://pagos"}),
        retries=2,
        breaker=breaker,
        transport=httpx.MockTransport(handler),
        retry_base_delay=0,
    )
    with pytest.raises(ServiceUnavailableError):
        client.request("POST", "/payments", json={})
    assert calls["n"] == 3  # 1 intento + 2 reintentos
    with pytest.raises(CircuitBreakerOpenError):
        client.request("POST", "/payments", json={})
    assert calls["n"] == 3  # fail fast


def test_service_client_returns_4xx_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        assert request.headers["X-Internal-Token"] == "secreto"
        return httpx.Response(409, json={"code": "INSUFFICIENT_STOCK"})

    client = ServiceClient(
        "catalog-service",
        ServiceResolver(overrides={"catalog-service": "http://catalogo"}),
        internal_token="secreto",
        transport=httpx.MockTransport(handler),
    )
    assert client.request("POST", "/reservations", json={}).status_code == 409
    assert calls["n"] == 1
