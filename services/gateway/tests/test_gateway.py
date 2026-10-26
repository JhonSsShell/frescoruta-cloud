import httpx
import pytest
from fastapi.testclient import TestClient
from frescoruta_common.config import Settings
from frescoruta_common.registry import ServiceResolver
from frescoruta_common.runtime import build_runtime
from gateway_service.api import create_app
from gateway_service.ratelimit import TokenBucketRateLimiter

KEY = {"X-API-Key": "frescoruta-demo-key"}
URLS = {
    "catalog-service": "http://catalog",
    "order-service": "http://orders",
    "payment-service": "http://payments",
    "notification-service": "http://notifications",
}


class Upstream:
    def __init__(self):
        self.requests = []
        self.down = set()

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        if request.url.host in self.down:
            raise httpx.ConnectError("caído", request=request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "UP"})
        if request.url.path == "/products":
            return httpx.Response(200, json=[{"id": "FR-001"}], headers={"X-Cache": "HIT"})
        if request.url.path == "/orders" and request.method == "POST":
            return httpx.Response(201, json={"id": "PED-1", "status": "CONFIRMED"})
        return httpx.Response(200, json={"path": request.url.path})


def make_client(upstream, limiter=None):
    settings = Settings(
        service_name="api-gateway", port=8000, public_url="http://gw", internal_token="tok"
    )
    runtime = build_runtime(settings, redis_client=None, resolver=ServiceResolver(overrides=URLS))
    app = create_app(settings, runtime, transport=httpx.MockTransport(upstream), limiter=limiter)
    return TestClient(app)


@pytest.fixture
def upstream():
    return Upstream()


def test_public_read_is_routed_with_internal_headers(upstream):
    with make_client(upstream) as client:
        resp = client.get("/api/products")
    assert resp.status_code == 200
    assert resp.headers["X-Cache"] == "HIT"
    assert resp.headers["X-Upstream-Service"] == "catalog-service"
    sent = upstream.requests[-1]
    assert sent.url.host == "catalog"
    assert sent.headers["X-Internal-Token"] == "tok"
    assert sent.headers["X-Request-ID"] == resp.headers["X-Request-ID"]


def test_write_requires_api_key(upstream):
    with make_client(upstream) as client:
        assert client.post("/api/orders", json={}).status_code == 401
        resp = client.post("/api/orders", json={}, headers={**KEY, "Idempotency-Key": "abc"})
    assert resp.status_code == 201
    assert upstream.requests[-1].headers["Idempotency-Key"] == "abc"


def test_internal_endpoints_are_not_exposed(upstream):
    with make_client(upstream) as client:
        assert client.post("/api/reservations", json={}, headers=KEY).status_code == 404
        assert client.delete("/api/products", headers=KEY).status_code == 405
    assert upstream.requests == []


def test_path_rewrite_for_resilience_routes(upstream):
    with make_client(upstream) as client:
        body = client.get("/api/resilience/chaos", headers=KEY).json()
    assert body["path"] == "/admin/chaos"
    assert upstream.requests[-1].url.host == "payments"


def test_rate_limit_returns_429_with_retry_after(upstream):
    limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=0.01)
    with make_client(upstream, limiter) as client:
        codes = [client.get("/api/products").status_code for _ in range(3)]
        last = client.get("/api/products")
    assert codes == [200, 200, 429]
    assert int(last.headers["Retry-After"]) >= 1


def test_upstream_down_returns_502(upstream):
    upstream.down.add("orders")
    with make_client(upstream) as client:
        resp = client.get("/api/orders", headers=KEY)
    assert resp.status_code == 502
    assert resp.json()["code"] == "UPSTREAM_UNAVAILABLE"


def test_status_aggregates_all_services(upstream):
    upstream.down.add("notifications")
    with make_client(upstream) as client:
        body = client.get("/api/status").json()
    states = {s["service"]: s["status"] for s in body["services"]}
    assert states["catalog-service"] == "UP"
    assert states["notification-service"] == "DOWN"
    assert body["status"] == "DEGRADED"


def test_dashboard_is_served(upstream):
    with make_client(upstream) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert "FrescoRuta Cloud" in resp.text


def test_token_bucket_refills_over_time():
    now = {"t": 0.0}
    limiter = TokenBucketRateLimiter(capacity=1, refill_per_second=1, clock=lambda: now["t"])
    assert limiter.allow("a")[0] is True
    assert limiter.allow("a")[0] is False
    now["t"] = 1.0
    assert limiter.allow("a")[0] is True
    assert limiter.allow("b")[0] is True  # baldes independientes por cliente
