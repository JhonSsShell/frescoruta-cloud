def pay(client, order_id="PED-1", amount=100_000, method="CARD", key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(
        "/payments", json={"order_id": order_id, "amount": amount, "method": method}, headers=headers
    )


def test_card_payment_approved_and_event_published(client, bus):
    resp = pay(client)
    assert resp.status_code == 201
    assert resp.json()["status"] == "APPROVED"
    assert resp.json()["reference"].startswith("CARD-")
    assert bus.published[-1].type == "payment.approved"


def test_card_payment_declined_over_limit(client, bus):
    resp = pay(client, amount=6_000_000)
    assert resp.json()["status"] == "DECLINED"
    assert bus.published[-1].type == "payment.declined"


def test_cash_on_delivery_limit(client):
    assert pay(client, "PED-2", 1_500_000, "CASH_ON_DELIVERY").json()["status"] == "APPROVED"
    assert pay(client, "PED-3", 2_500_000, "CASH_ON_DELIVERY").json()["status"] == "DECLINED"


def test_idempotency_key_prevents_double_charge(client, bus):
    first = pay(client, key="order:PED-9")
    second = pay(client, key="order:PED-9")
    assert first.status_code == 201 and second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/payments").json()) == 1
    assert len(bus.published) == 1


def test_idempotency_key_reused_with_other_amount_conflicts(client):
    pay(client, key="k-1", amount=1000)
    assert pay(client, key="k-1", amount=2000).status_code == 409


def test_unsupported_method(client):
    assert pay(client, method="BITCOIN").status_code == 422


def test_chaos_injects_503(client, chaos):
    client.put("/admin/chaos", json={"failure_rate": 1.0, "latency_ms": 0})
    resp = pay(client)
    assert resp.status_code == 503
    assert resp.json()["code"] == "GATEWAY_UNAVAILABLE"
    assert chaos.injected_failures == 1
    client.put("/admin/chaos", json={"failure_rate": 0.0})
    assert pay(client).status_code == 201


def test_refund_is_idempotent_and_publishes_once(client, bus):
    payment = pay(client).json()
    assert client.post(f"/payments/{payment['id']}/refund").json()["status"] == "REFUNDED"
    assert client.post(f"/payments/{payment['id']}/refund").status_code == 200
    assert [e.type for e in bus.published].count("payment.refunded") == 1


def test_payment_methods_listed(client):
    methods = {m["method"] for m in client.get("/payment-methods").json()}
    assert methods == {"CARD", "PSE", "CASH_ON_DELIVERY"}
