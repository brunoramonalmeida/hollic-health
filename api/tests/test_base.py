from datetime import datetime

from conftest import activate_plan, auth


def test_login_and_protected_route(client):
    denied = client.get("/clinics")
    assert denied.status_code == 401
    headers = auth(client)
    listed = client.get("/clinics", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "Clinic Foo"


def test_appointment_amount_comes_from_the_price_list(client):
    headers = auth(client)
    professional = client.post(
        "/professionals",
        headers=headers,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000},
    )
    assert professional.status_code == 201
    customer = client.post("/customers", headers=headers, json={"name": "Foo Holder"})
    assert customer.status_code == 201
    activate_plan(client, headers, customer.json()["id"])
    created = client.post(
        "/appointments",
        headers=headers,
        json={
            "customer_id": customer.json()["id"],
            "dependent_id": None,
            "professional_id": professional.json()["id"],
            # 2026-10-27 is a Tuesday, 15:00 is within the default week.
            "marked_at": datetime(2026, 10, 27, 15, 0).isoformat(),
            "status": "agendada",
            "notes": "",
        },
    )
    assert created.status_code == 201
    body = created.json()
    # Clinic fee 2000 + specialty 8000 + professional 5000 (see conftest).
    assert body["fee_cents"] == 2000
    assert body["specialty_price_cents"] == 8000
    assert body["professional_price_cents"] == 5000
    assert body["amount_cents"] == 15000
    assert body["specialty_name"] == "Clínico geral"
    assert body["clinic_name"] == "Clinic Foo"


def test_dependent_must_belong_to_customer(client):
    headers = auth(client)
    professional = client.post(
        "/professionals",
        headers=headers,
        json={"name": "Dolor Sit", "specialty_id": 1, "clinic_id": 1, "price_cents": 0},
    )
    holder = client.post("/customers", headers=headers, json={"name": "Foo Holder"})
    other = client.post("/customers", headers=headers, json={"name": "Bar Holder"})
    activate_plan(client, headers, other.json()["id"])
    dependent = client.post(
        f"/customers/{holder.json()['id']}/dependents",
        headers=headers,
        json={"name": "Ipsum Child", "birth_date": "2018-02-20"},
    )
    assert dependent.status_code == 201
    response = client.post(
        "/appointments",
        headers=headers,
        json={
            "customer_id": other.json()["id"],
            "dependent_id": dependent.json()["id"],
            "professional_id": professional.json()["id"],
            "marked_at": "2026-10-27T15:00:00",
            "status": "agendada",
        },
    )
    assert response.status_code == 404
