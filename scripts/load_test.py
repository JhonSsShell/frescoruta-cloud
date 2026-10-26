"""Prueba de carga y concurrencia ligera contra el API Gateway.

Mide latencias (p50/p95) del catálogo y de la creación de pedidos y verifica
que no exista sobreventa cuando muchos pedidos compiten por el mismo stock.

Uso:
    RATE_LIMIT_PER_MINUTE=100000 RATE_LIMIT_BURST=10000 ./scripts/run_local.sh
    python scripts/load_test.py --url http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import uuid

import httpx

PRODUCT_UNDER_CONTENTION = "FR-009"


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


async def timed(
    client: httpx.AsyncClient, method: str, url: str, **kw
) -> tuple[float, httpx.Response]:
    start = time.perf_counter()
    resp = await client.request(method, url, **kw)
    return (time.perf_counter() - start) * 1000, resp


async def catalog_load(client, users: int, requests_per_user: int) -> None:
    async def user():
        return [await timed(client, "GET", "/products") for _ in range(requests_per_user)]

    start = time.perf_counter()
    results = [r for batch in await asyncio.gather(*(user() for _ in range(users))) for r in batch]
    elapsed = time.perf_counter() - start
    lat = [ms for ms, _ in results]
    ok = sum(1 for _, r in results if r.status_code == 200)
    hits = sum(1 for _, r in results if r.headers.get("X-Cache") == "HIT")
    print(
        f"GET /api/products  usuarios={users}  peticiones={len(results)}  ok={ok}  "
        f"cache_hit={hits / len(results):.0%}  p50={statistics.median(lat):.0f} ms  "
        f"p95={pct(lat, 0.95):.0f} ms  throughput={len(results) / elapsed:.0f} req/s"
    )


async def contention(client, orders: int, qty: int, stock: int = 40) -> None:
    current = (await client.get(f"/products/{PRODUCT_UNDER_CONTENTION}")).json()["stock"]
    if current != stock:  # deja el escenario reproducible entre ejecuciones
        await client.patch(
            f"/products/{PRODUCT_UNDER_CONTENTION}/stock", json={"delta": stock - current}
        )
    stock_before = (await client.get(f"/products/{PRODUCT_UNDER_CONTENTION}")).json()["stock"]
    body = {
        "customer_name": "Prueba de carga",
        "customer_email": "carga@frescoruta.co",
        "delivery_city": "Cali",
        "payment_method": "PSE",
        "items": [{"product_id": PRODUCT_UNDER_CONTENTION, "quantity": qty}],
    }

    async def one():
        return await timed(
            client, "POST", "/orders", json=body, headers={"Idempotency-Key": uuid.uuid4().hex}
        )

    results = await asyncio.gather(*(one() for _ in range(orders)))
    statuses = [r.json()["status"] for _, r in results]
    lat = [ms for ms, _ in results]
    confirmed = statuses.count("CONFIRMED")
    stock_after = (await client.get(f"/products/{PRODUCT_UNDER_CONTENTION}")).json()["stock"]
    expected_max = stock_before // qty
    rejected = statuses.count("REJECTED")
    print(
        f"POST /api/orders concurrentes={orders}  stock_inicial={stock_before}  qty={qty}  "
        f"confirmados={confirmed} (máximo posible {expected_max})  rechazados={rejected}  "
        f"stock_final={stock_after}  p50={statistics.median(lat):.0f} ms  "
        f"p95={pct(lat, 0.95):.0f} ms"
    )
    assert stock_after >= 0, "¡Sobreventa detectada!"
    assert confirmed <= expected_max
    assert stock_before - confirmed * qty == stock_after
    print("Sin sobreventa: stock_final = stock_inicial − confirmados × qty ✔")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--key", default="frescoruta-demo-key")
    parser.add_argument("--users", type=int, default=50)
    args = parser.parse_args()
    limits = httpx.Limits(max_connections=100)
    async with httpx.AsyncClient(
        base_url=args.url.rstrip("/") + "/api",
        headers={"X-API-Key": args.key},
        timeout=120,
        limits=limits,
    ) as client:
        await client.put("/resilience/chaos", json={"failure_rate": 0, "latency_ms": 0})
        await catalog_load(client, users=args.users, requests_per_user=20)
        await contention(client, orders=30, qty=3)


if __name__ == "__main__":
    asyncio.run(main())
