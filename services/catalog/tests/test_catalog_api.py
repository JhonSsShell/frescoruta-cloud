def stock_of(client, product_id):
    return client.get(f"/products/{product_id}").json()["stock"]


def test_health_reports_database_up(client):
    body = client.get("/health").json()
    assert body["status"] == "UP"
    assert body["checks"]["database"] == "UP"


def test_list_products_applies_cache_aside(client):
    first = client.get("/products")
    second = client.get("/products")
    assert first.status_code == 200 and len(first.json()) == 13
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"


def test_filter_by_category(client):
    data = client.get("/products", params={"category": "Lácteos"}).json()
    assert {p["category"] for p in data} == {"Lácteos"}


def test_reservation_decrements_stock_and_invalidates_cache(client):
    client.get("/products")  # calienta la caché
    before = stock_of(client, "FR-001")
    resp = client.post(
        "/reservations",
        json={"order_id": "PED-1", "items": [{"product_id": "FR-001", "quantity": 10}]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "RESERVED"
    assert body["total"] == 10 * 4200
    assert stock_of(client, "FR-001") == before - 10
    assert client.get("/products").headers["X-Cache"] == "MISS"


def test_reservation_is_idempotent_per_order(client):
    payload = {"order_id": "PED-2", "items": [{"product_id": "FR-002", "quantity": 5}]}
    first = client.post("/reservations", json=payload)
    second = client.post("/reservations", json=payload)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert stock_of(client, "FR-002") == 1500 - 5


def test_insufficient_stock_is_all_or_nothing(client):
    resp = client.post(
        "/reservations",
        json={
            "order_id": "PED-3",
            "items": [
                {"product_id": "FR-001", "quantity": 1},
                {"product_id": "FR-009", "quantity": 999},
            ],
        },
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "INSUFFICIENT_STOCK"
    assert body["shortages"][0]["product_id"] == "FR-009"
    assert stock_of(client, "FR-001") == 800  # sin reservas parciales


def test_unknown_product_returns_404(client):
    resp = client.post(
        "/reservations",
        json={"order_id": "PED-4", "items": [{"product_id": "FR-999", "quantity": 1}]},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "PRODUCT_NOT_FOUND"


def test_release_compensates_and_is_idempotent(client):
    res = client.post(
        "/reservations",
        json={"order_id": "PED-5", "items": [{"product_id": "FR-005", "quantity": 4}]},
    ).json()
    assert stock_of(client, "FR-005") == 396
    assert client.post(f"/reservations/{res['id']}/release").json()["status"] == "RELEASED"
    assert client.post(f"/reservations/{res['id']}/release").status_code == 200
    assert stock_of(client, "FR-005") == 400


def test_commit_then_release_is_rejected(client):
    res = client.post(
        "/reservations",
        json={"order_id": "PED-6", "items": [{"product_id": "FR-006", "quantity": 1}]},
    ).json()
    assert client.post(f"/reservations/{res['id']}/commit").json()["status"] == "COMMITTED"
    assert client.post(f"/reservations/{res['id']}/release").status_code == 409


def test_low_stock_event_is_published(client, bus):
    client.post(
        "/reservations",
        json={"order_id": "PED-7", "items": [{"product_id": "FR-009", "quantity": 10}]},
    )
    events = [e for e in bus.published if e.type == "stock.low"]
    assert events and events[0].payload["product_id"] == "FR-009"
    assert events[0].payload["stock"] == 30


def test_create_product_and_adjust_stock(client):
    new = {
        "id": "FR-100",
        "name": "Fresa",
        "category": "Frutas",
        "unit": "kg",
        "price": 9000,
        "stock": 50,
    }
    assert client.post("/products", json=new).status_code == 201
    assert client.post("/products", json=new).status_code == 409
    assert client.patch("/products/FR-100/stock", json={"delta": 25}).json()["stock"] == 75
    assert client.patch("/products/FR-100/stock", json={"delta": -100}).status_code == 409
