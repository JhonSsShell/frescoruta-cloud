"""Health Endpoint Monitoring.

Cada servicio expone ``GET /health``; Render lo usa como *health check* para
decidir si una instancia recibe tráfico y el API Gateway lo agrega en
``/api/status``.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

Check = Callable[[], bool | str]


def build_health_router(
    service_name: str,
    version: str,
    checks: dict[str, Check] | None = None,
    critical: set[str] | None = None,
    info: Callable[[], dict] | None = None,
) -> APIRouter:
    """Crea el router de salud.

    ``critical`` indica qué comprobaciones hacen que el servicio responda 503
    (por ejemplo, la base de datos). Las no críticas (Redis) solo degradan.
    """
    router = APIRouter(tags=["salud"])
    started = time.monotonic()
    checks = checks or {}
    critical = critical or set()

    @router.get("/health")
    def health():
        results: dict[str, str] = {}
        for name, check in checks.items():
            try:
                outcome = check()
                results[name] = (
                    outcome if isinstance(outcome, str) else ("UP" if outcome else "DOWN")
                )
            except Exception:  # noqa: BLE001
                results[name] = "DOWN"
        failing_critical = [n for n in critical if results.get(n) == "DOWN"]
        degraded = any(v == "DOWN" for v in results.values())
        status = "DOWN" if failing_critical else ("DEGRADED" if degraded else "UP")
        body = {
            "status": status,
            "service": service_name,
            "version": version,
            "uptime_s": round(time.monotonic() - started, 1),
            "checks": results,
        }
        if info:
            body.update(info())
        return JSONResponse(body, status_code=503 if failing_critical else 200)

    return router
