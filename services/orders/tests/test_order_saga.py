ORDER = {
    "customer_name": "Restaurante La Huerta",
    "customer_email": "compras@lahuerta.co",
    "delivery_city": "Medellín",
    "payment_method": "CARD",
    "items": [{"product_id": "FR-001", "quantity": 10}, {"product_id": "FR-005", "quantity": 2}],
}


def steps(order):
    return [(s["step"], s["status"]) for s in order["steps"]]


def event_types(bus):
    return [e.type for e in bus.published]


def test_happy_path_confirms_order(env):
    resp = env["client"].post("/orders", json=ORDER)
    assert resp.status_code == 201
    order = resp.json()
    assert order["status"] == "CONFIRMED"
    assert order["total"] == 10 * 4200 + 2 * 9500
    assert order["items"][0]["product_name"] == "Tomate chonto"
    assert steps(order) == [
        ("reserve_stock", "SUCCEEDED"),
        ("process_payment", "SUCCEEDED"),
        ("commit_stock", "SUCCEEDED"),
        ("confirm_order", "SUCCEEDED"),
    ]
    assert event_types(env["bus"]) == ["order.created", "order.confirmed"]


def test_insufficient_stock_rejects_without_payment(env):
    order = env["client"].post(
        "/orders", json={**ORDER, "items": [{"product_id": "FR-005", "quantity": 50}]}
    ).json()
    assert order["status"] == "REJECTED"
    assert "Aguacate Hass" in order["rejection_reason"]
    assert env["payments"].calls == 0
    assert event_types(env["bus"])[-1] == "order.rejected"


def test_declined_payment_compensates_reservation(env):
    env["payments"].mode = "decline"
    order = env["client"].post("/orders", json=ORDER).json()
    assert order["status"] == "REJECTED"
    assert ("release_stock", "COMPENSATED") in steps(order)
    assert env["catalog"].stock == {"FR-001": 100, "FR-005": 10}


def test_payment_outage_opens_circuit_and_fails_fast(env):
    env["payments"].mode = "down"
    first = env["client"].post("/orders", json=ORDER).json()
    second = env["client"].post("/orders", json=ORDER).json()
    third = env["client"].post("/orders", json=ORDER).json()
    assert {first["status"], second["status"], third["status"]} == {"REJECTED"}
    assert env["breakers"]["payment-service"].state == "OPEN"
    assert env["payments"].calls == 2  # el tercer pedido no llegó a invocar pagos
    assert "circuito abierto" in third["steps"][1]["detail"]
    assert env["catalog"].stock == {"FR-001": 100, "FR-005": 10}  # todo compensado
    breakers = env["client"].get("/circuit-breakers").json()
    assert {b["name"]: b["state"] for b in breakers}["payment-service"] == "OPEN"
    env["client"].post("/circuit-breakers/reset")
    assert env["breakers"]["payment-service"].state == "CLOSED"


def test_commit_failure_refunds_and_releases(env):
    env["catalog"].fail_commit = True
    order = env["client"].post("/orders", json=ORDER).json()
    assert order["status"] == "REJECTED"
    assert ("refund_payment", "COMPENSATED") in steps(order)
    assert ("release_stock", "COMPENSATED") in steps(order)
    assert env["payments"].refunded == ["PAY-1"]


def test_idempotency_key_returns_same_order(env):
    headers = {"Idempotency-Key": "pedido-123"}
    first = env["client"].post("/orders", json=ORDER, headers=headers)
    second = env["client"].post("/orders", json=ORDER, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert env["catalog"].stock["FR-001"] == 90  # solo se reservó una vez


def test_list_get_and_stats(env):
    created = env["client"].post("/orders", json=ORDER).json()
    assert env["client"].get(f"/orders/{created['id']}").json()["id"] == created["id"]
    assert env["client"].get("/orders/PED-NOEXISTE").status_code == 404
    assert len(env["client"].get("/orders").json()) == 1
    stats = env["client"].get("/orders/stats").json()
    assert stats["by_status"]["CONFIRMED"] == 1
    assert stats["confirmed_revenue"] == created["total"]


def test_validation_rejects_bad_email(env):
    assert env["client"].post("/orders", json={**ORDER, "customer_email": "x"}).status_code == 422
