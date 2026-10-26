"""Tabla de enrutamiento del API Gateway (Gateway Routing).

Solo se exponen las operaciones públicas. Los endpoints internos de los
servicios (reservas, reembolsos, ...) quedan ocultos: únicamente la saga
del servicio de pedidos puede invocarlos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern
    service: str
    target: str
    protected: bool = True  # requiere X-API-Key

    def match(self, method: str, path: str) -> re.Match | None:
        return self.pattern.match(path) if method == self.method else None

    def target_path(self, match: re.Match) -> str:
        return self.target.format(*match.groups())


def r(method: str, pattern: str, service: str, target: str, protected: bool = True) -> Route:
    return Route(method, re.compile(f"^{pattern}$"), service, target, protected)


ID = r"([A-Za-z0-9_-]{1,40})"

ROUTES: list[Route] = [
    # Catálogo (lecturas públicas)
    r("GET", "/products", "catalog-service", "/products", protected=False),
    r("GET", f"/products/{ID}", "catalog-service", "/products/{0}", protected=False),
    r("POST", "/products", "catalog-service", "/products"),
    r("PATCH", f"/products/{ID}/stock", "catalog-service", "/products/{0}/stock"),
    # Pedidos
    r("POST", "/orders", "order-service", "/orders"),
    r("GET", "/orders", "order-service", "/orders"),
    r("GET", "/orders/stats", "order-service", "/orders/stats"),
    r("GET", f"/orders/{ID}", "order-service", "/orders/{0}"),
    # Pagos
    r("GET", "/payment-methods", "payment-service", "/payment-methods", protected=False),
    r("GET", "/payments", "payment-service", "/payments"),
    # Notificaciones
    r("GET", "/notifications", "notification-service", "/notifications"),
    r("GET", "/notifications/stats", "notification-service", "/notifications/stats"),
    # Resiliencia (demostración)
    r("GET", "/resilience/circuit-breakers", "order-service", "/circuit-breakers"),
    r("POST", "/resilience/circuit-breakers/reset", "order-service", "/circuit-breakers/reset"),
    r("GET", "/resilience/chaos", "payment-service", "/admin/chaos"),
    r("PUT", "/resilience/chaos", "payment-service", "/admin/chaos"),
]

BACKEND_SERVICES = ["catalog-service", "order-service", "payment-service", "notification-service"]


def find_route(method: str, path: str) -> tuple[Route, re.Match] | tuple[None, None]:
    for route in ROUTES:
        match = route.match(method, path)
        if match:
            return route, match
    return None, None


def path_exists(path: str) -> bool:
    return any(route.pattern.match(path) for route in ROUTES)
