"""The patient app contract (/api/app/v1) over the existing domain."""

from conftest import auth, auth_as


def _holder(client):
    created = client.post("/customers", headers=auth(client), json={"name": "Foo Holder", "city": "Sample", "state": "SP"})
    assert created.status_code == 201, created.text
    customer = created.json()
    email = f"client{customer['id']}@example.com"
    auth_as(client, "client", customer_id=customer["id"])
    return customer, email


def test_holder_logs_in_and_reads_the_app(client):
    customer, email = _holder(client)
    refused = client.post("/api/app/v1/login", json={"email": "foo.bar@example.com", "password": "saude123"})
    assert refused.status_code == 403

    logged = client.post("/api/app/v1/login", json={"email": email, "password": "saude123"})
    assert logged.status_code == 200, logged.text
    body = logged.json()
    assert body["token_type"] == "Bearer" and body["token"]
    assert body["customer"]["id"] == customer["id"]
    assert body["customer"]["name"] == "Foo Holder"

    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/app/v1/user", headers=headers).json()["customer"]["id"] == customer["id"]
    assert client.get("/api/app/v1/appointments", headers=headers).json() == []
    assert client.get("/api/app/v1/checkout", headers=headers).json()["errors"]
    card = client.get("/api/app/v1/sante-card", headers=headers)
    assert card.status_code == 200, card.text
    assert card.json()["holder"] == "Foo Holder"
    assert card.json()["code"]
    assert client.get("/api/app/v1/subscriptions", headers=headers).status_code == 404
    cities = client.get("/api/app/v1/cities", headers=headers)
    assert cities.status_code == 200
    assert any(item["name"] == "Sample" for item in cities.json())


def test_wrong_password_is_rejected(client):
    _customer, email = _holder(client)
    response = client.post("/api/app/v1/login", json={"email": email, "password": "nope"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"
