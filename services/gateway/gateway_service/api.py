"""API Gateway: punto único de entrada a FrescoRuta Cloud.

Patrones aplicados:
* Gateway Routing: enruta ``/api/*`` al microservicio correspondiente.
* Gateway Offloading: autenticación por API key, rate limiting, CORS,
  correlación y métricas se resuelven aquí y no en cada servicio.
* Gateway Aggregation: ``/api/status`` y ``/api/overview`` combinan varias
  llamadas internas en una sola respuesta para el cliente.
* Service Discovery: las URL internas se resuelven con el Service Registry.
"""

from __future__ import annotations

import asyncio
import hmac
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from frescoruta_common.config import Settings, env_float, env_int, env_str
from frescoruta_common.context import REQUEST_ID_HEADER, get_request_id
from frescoruta_common.health import build_health_router
from frescoruta_common.middleware import INTERNAL_TOKEN_HEADER
from frescoruta_common.runtime import Runtime, build_runtime, create_service_app

from .metrics import GatewayMetrics
from .ratelimit import TokenBucketRateLimiter
from .routes import BACKEND_SERVICES, find_route, path_exists

SERVICE_NAME = "api-gateway"
API_KEY_HEADER = "X-API-Key"
FORWARDED_REQUEST_HEADERS = ("content-type", "accept", "idempotency-key")
FORWARDED_RESPONSE_HEADERS = ("content-type", "x-cache", "idempotent-replayed")
STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    limiter: TokenBucketRateLimiter | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env(SERVICE_NAME, 8000)
    runtime = runtime or build_runtime(settings)
    api_key = env_str("GATEWAY_API_KEY", "frescoruta-demo-key")
    timeout = env_float("PROXY_TIMEOUT_S", 15.0)
    limiter = limiter or TokenBucketRateLimiter(
        capacity=env_int("RATE_LIMIT_BURST", 40),
        refill_per_second=env_int("RATE_LIMIT_PER_MINUTE", 240) / 60,
    )
    metrics = GatewayMetrics()
    http = httpx.AsyncClient(timeout=timeout, transport=transport)

    app = create_service_app(
        runtime,
        title="FrescoRuta · API Gateway",
        description="Punto único de entrada: enrutamiento, seguridad, rate limiting y agregación.",
        internal_auth=False,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=(env_str("CORS_ORIGINS", "*") or "*").split(","),
        allow_methods=["GET", "POST", "PUT", "PATCH"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "X-Cache", "X-RateLimit-Remaining"],
    )
    app.state.limiter = limiter
    app.state.metrics = metrics

    app.include_router(
        build_health_router(
            SERVICE_NAME,
            settings.version,
            checks={"redis": runtime.redis_check},
            info=lambda: {"registry": runtime.registry.all()},
        )
    )

    def internal_headers(extra: dict | None = None) -> dict:
        headers = dict(extra or {})
        if request_id := get_request_id():
            headers[REQUEST_ID_HEADER] = request_id
        if settings.internal_token:
            headers[INTERNAL_TOKEN_HEADER] = settings.internal_token
        return headers

    async def fetch_json(service: str, path: str) -> tuple[int, dict | list | None, float]:
        start = time.perf_counter()
        try:
            url = runtime.resolver.resolve(service) + path
            resp = await http.get(url, headers=internal_headers(), timeout=min(timeout, 10.0))
            body = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else None
            )
            return resp.status_code, body, (time.perf_counter() - start) * 1000
        except (httpx.HTTPError, LookupError, ValueError):
            runtime.resolver.invalidate(service)
            return 0, None, (time.perf_counter() - start) * 1000

    # -- agregación -------------------------------------------------------------
    @app.get("/api/status", tags=["agregación"])
    async def status():
        """Salud agregada de todos los microservicios (consultas en paralelo)."""
        results = await asyncio.gather(*(fetch_json(s, "/health") for s in BACKEND_SERVICES))
        services = [
            {
                "service": SERVICE_NAME,
                "status": "UP",
                "url": settings.public_url,
                "latency_ms": 0,
                "details": {"redis": runtime.redis_check()},
            }
        ]
        for name, (code, body, latency) in zip(BACKEND_SERVICES, results, strict=True):
            services.append(
                {
                    "service": name,
                    "status": (body or {}).get("status", "DOWN") if code else "DOWN",
                    "url": runtime.resolver.resolve(name),
                    "latency_ms": round(latency, 1),
                    "details": body,
                }
            )
        overall = "UP" if all(s["status"] == "UP" for s in services) else "DEGRADED"
        return {"status": overall, "environment": settings.environment, "services": services}

    @app.get("/api/overview", tags=["agregación"])
    async def overview(request: Request):
        """Resumen para el panel: una sola llamada del cliente, cuatro internas."""
        if denied := check_api_key(request):
            return denied
        stats, breakers, chaos, notif = await asyncio.gather(
            fetch_json("order-service", "/orders/stats"),
            fetch_json("order-service", "/circuit-breakers"),
            fetch_json("payment-service", "/admin/chaos"),
            fetch_json("notification-service", "/notifications/stats"),
        )
        return {
            "orders": stats[1],
            "circuit_breakers": breakers[1],
            "chaos": chaos[1],
            "notifications": notif[1],
            "gateway": {"rate_limited": limiter.rejected, **metrics.snapshot()},
        }

    @app.get("/api/metrics", tags=["observabilidad"])
    def gateway_metrics():
        return {"rate_limited": limiter.rejected, **metrics.snapshot()}

    # -- seguridad --------------------------------------------------------------
    def check_api_key(request: Request) -> JSONResponse | None:
        provided = request.headers.get(API_KEY_HEADER, "")
        if not hmac.compare_digest(provided, api_key):
            return JSONResponse(
                {
                    "code": "UNAUTHORIZED",
                    "message": f"Encabezado {API_KEY_HEADER} inválido o ausente",
                },
                status_code=401,
            )
        return None

    # -- enrutamiento genérico --------------------------------------------------
    @app.api_route(
        "/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], tags=["proxy"]
    )
    async def proxy(path: str, request: Request):
        sub_path = "/" + path
        client_id = request.headers.get(API_KEY_HEADER) or (
            request.client.host if request.client else "anon"
        )
        allowed, remaining, retry_after = limiter.allow(client_id)
        if not allowed:
            return JSONResponse(
                {"code": "RATE_LIMITED", "message": "Demasiadas solicitudes, intente más tarde"},
                status_code=429,
                headers={"Retry-After": str(max(1, round(retry_after)))},
            )

        route, match = find_route(request.method, sub_path)
        if route is None:
            if path_exists(sub_path):
                return JSONResponse(
                    {"code": "METHOD_NOT_ALLOWED", "message": "Método no permitido"},
                    status_code=405,
                )
            return JSONResponse(
                {"code": "NOT_FOUND", "message": f"Ruta /api{sub_path} no existe"}, status_code=404
            )
        if route.protected and (denied := check_api_key(request)):
            return denied

        headers = internal_headers(
            {k: v for k, v in request.headers.items() if k.lower() in FORWARDED_REQUEST_HEADERS}
        )
        headers["X-Forwarded-For"] = request.client.host if request.client else ""
        start = time.perf_counter()
        try:
            url = runtime.resolver.resolve(route.service) + route.target_path(match)
            upstream = await http.request(
                request.method,
                url,
                params=request.query_params,
                content=await request.body(),
                headers=headers,
            )
        except httpx.TimeoutException:
            metrics.record(route.service, 504, (time.perf_counter() - start) * 1000)
            return JSONResponse(
                {"code": "UPSTREAM_TIMEOUT", "message": f"{route.service} no respondió a tiempo"},
                status_code=504,
            )
        except (httpx.TransportError, LookupError):
            runtime.resolver.invalidate(route.service)
            metrics.record(route.service, 502, (time.perf_counter() - start) * 1000)
            return JSONResponse(
                {"code": "UPSTREAM_UNAVAILABLE", "message": f"{route.service} no disponible"},
                status_code=502,
            )
        metrics.record(route.service, upstream.status_code, (time.perf_counter() - start) * 1000)
        response_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() in FORWARDED_RESPONSE_HEADERS
        }
        response_headers["X-RateLimit-Remaining"] = str(remaining)
        response_headers["X-Upstream-Service"] = route.service
        return Response(
            upstream.content, status_code=upstream.status_code, headers=response_headers
        )

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="panel")

    return app
