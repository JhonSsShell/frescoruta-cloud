"""Guion de demostración de punta a punta contra el API Gateway.

Uso:
    python scripts/demo.py                               # local
    python scripts/demo.py --url https://<gateway>.onrender.com --key <API_KEY>

Recorre los escenarios de la presentación: caché, saga exitosa, stock
insuficiente, pago rechazado con compensación y caída de la pasarela con
apertura del Circuit Breaker.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

import httpx

ORDER = {
    "customer_name": "Restaurante La Huerta",
    "customer_email": "compras@lahuerta.co",
    "delivery_city": "Bogotá",
    "payment_method": "CARD",
    "items": [
        {"product_id": "FR-001", "quantity": 10},
        {"product_id": "FR-005", "quantity": 3},
        {"product_id": "FR-009", "quantity": 2},
    ],
}


def title(text: str) -> None:
    print(f"\n\033[1;32m== {text}\033[0m")


def show_order(order: dict) -> None:
    steps = " → ".join(f"{s['step']}:{s['status']}" for s in order["steps"])
    print(f"   {order['id']}  {order['status']}  total=${order['total']:,}".replace(",", "."))
    if order.get("rejection_reason"):
        print(f"   motivo: {order['rejection_reason']}")
    print(f"   saga: {steps}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--key", default="frescoruta-demo-key")
    args = parser.parse_args()
    h = {"X-API-Key": args.key}
    api = httpx.Client(base_url=args.url.rstrip("/") + "/api", timeout=90, headers=h)

    def create(order: dict) -> dict:
        resp = api.post("/orders", json=order, headers={"Idempotency-Key": uuid.uuid4().hex})
        resp.raise_for_status()
        return resp.json()

    title("1. Salud agregada (Gateway Aggregation + Health Endpoint Monitoring)")
    status = api.get("/status").json()
    for s in status["services"]:
        print(f"   {s['service']:<22} {s['status']:<9} {s['url']}")
    if status["status"] != "UP":
        print("   (algún servicio no está UP; en Render puede estar despertando, reintente)")

    title("2. Cache-Aside en el catálogo")
    for _ in range(2):
        resp = api.get("/products")
        cache = resp.headers.get("X-Cache")
        print(f"   GET /products -> {len(resp.json())} productos, X-Cache={cache}")

    api.put("/resilience/chaos", json={"failure_rate": 0, "latency_ms": 0})
    api.post("/resilience/circuit-breakers/reset")

    title("3. Saga exitosa")
    show_order(create(ORDER))

    title("4. Stock insuficiente (falla en T1, no se cobra)")
    show_order(create({**ORDER, "items": [{"product_id": "FR-009", "quantity": 500}]}))

    title("5. Pago rechazado (falla en T2 → compensación: liberar stock)")
    declined = {
        **ORDER,
        "payment_method": "CASH_ON_DELIVERY",
        "items": [{"product_id": "FR-012", "quantity": 100}],
    }
    show_order(create(declined))

    title("6. Pasarela caída: Retry + Circuit Breaker")
    api.put("/resilience/chaos", json={"failure_rate": 1, "latency_ms": 0})
    for i in (1, 2):
        start = time.perf_counter()
        order = create(ORDER)
        print(f"   intento {i}: {(time.perf_counter() - start) * 1000:.0f} ms")
        show_order(order)
    for b in api.get("/resilience/circuit-breakers").json():
        print(f"   breaker {b['name']}: {b['state']} (rechazadas={b['total_rejected']})")
    api.put("/resilience/chaos", json={"failure_rate": 0, "latency_ms": 0})
    api.post("/resilience/circuit-breakers/reset")

    title("7. Notificaciones generadas por eventos (Publish/Subscribe)")
    time.sleep(2)
    for n in api.get("/notifications", params={"limit": 8}).json():
        print(f"   [{n['channel']}] {n['event_type']:<16} -> {n['recipient']}: {n['subject']}")

    title("8. Métricas del gateway")
    print(f"   {api.get('/metrics').json()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
