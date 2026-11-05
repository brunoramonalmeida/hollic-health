"""API tests for the patient record of holders and dependents."""

import pytest

from conftest import auth

FULL_DEPENDENT = {
    "name": "Ipsum Child",
    "birth_date": "2018-02-20",
    "sex": "m",
    "kinship": "filho(a)",
    "document": "000.000.000-02",
    "identity_card": "MG-00.000.000",
    "health_card": "898000000000000",
    "phone": "(00) 00000-0003",
    "email": "child@example.com",
    "mother_name": "Foo Holder",
    "father_name": "Pater Foo",
    "street": "Rua Exemplo",
    "number": "100",
    "complement": "Apto 1",
    "district": "Centro",
    "city": "Sample",
    "state": "sp",
    "zip_code": "00000-000",
    "blood_type": "o+",
    "allergies": "Dipirona",
    "notes": "Asthma",
}


@pytest.fixture()
def ctx(client):
    headers = auth(client)
    holder = client.post("/customers", headers=headers, json={"name": "Foo Holder"})
    assert holder.status_code == 201

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.holder = holder.json()["id"]
    c.dependents = f"/customers/{c.holder}/dependents"
    return c


def test_dependent_full_record_round_trip(ctx):
    created = ctx.client.post(ctx.dependents, headers=ctx.headers, json=FULL_DEPENDENT)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["customer_id"] == ctx.holder
    # Codes are normalized to upper case.
    assert body["sex"] == "M"
    assert body["state"] == "SP"
    assert body["blood_type"] == "O+"
    for key, value in FULL_DEPENDENT.items():
        if key not in {"sex", "state", "blood_type"}:
            assert body[key] == value, key
    fetched = ctx.client.get(f"{ctx.dependents}/{body['id']}", headers=ctx.headers)
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_dependent_requires_name_and_birth_date(ctx):
    assert ctx.client.post(ctx.dependents, headers=ctx.headers, json={"name": "No Birth"}).status_code == 422
    assert ctx.client.post(ctx.dependents, headers=ctx.headers, json={"birth_date": "2018-02-20"}).status_code == 422
    assert ctx.client.post(ctx.dependents, headers=ctx.headers, json={"name": "", "birth_date": "2018-02-20"}).status_code == 422


def test_dependent_other_fields_are_optional(ctx):
    response = ctx.client.post(
        ctx.dependents, headers=ctx.headers, json={"name": "Minimal", "birth_date": "2018-02-20"}
    )
    assert response.status_code == 201
    assert response.json()["mother_name"] == ""
    assert response.json()["kinship"] == ""


@pytest.mark.parametrize(
    "field, value",
    [
        ("birth_date", "2999-01-01"),
        ("birth_date", "20/02/2018"),
        ("sex", "X"),
        ("blood_type", "C+"),
        ("state", "SPX"),
        ("zip_code", "00000-0000000"),
    ],
)
def test_dependent_field_validation(ctx, field, value):
    payload = {"name": "Ipsum Child", "birth_date": "2018-02-20", field: value}
    assert ctx.client.post(ctx.dependents, headers=ctx.headers, json=payload).status_code == 422


def test_dependent_update_replaces_all_fields(ctx):
    created = ctx.client.post(ctx.dependents, headers=ctx.headers, json=FULL_DEPENDENT).json()
    updated = ctx.client.put(
        f"{ctx.dependents}/{created['id']}",
        headers=ctx.headers,
        json={"name": "Ipsum Child", "birth_date": "2018-02-21", "kinship": "enteado(a)"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["birth_date"] == "2018-02-21"
    assert body["kinship"] == "enteado(a)"
    assert body["allergies"] == ""  # cleared: PUT is a full replacement


def test_dependent_is_scoped_to_its_holder(ctx):
    other = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Bar Holder"}).json()
    created = ctx.client.post(ctx.dependents, headers=ctx.headers, json=FULL_DEPENDENT).json()
    wrong = f"/customers/{other['id']}/dependents/{created['id']}"
    assert ctx.client.get(wrong, headers=ctx.headers).status_code == 404
    assert ctx.client.put(wrong, headers=ctx.headers, json=FULL_DEPENDENT).status_code == 404
    assert ctx.client.delete(wrong, headers=ctx.headers).status_code == 404
    assert ctx.client.get(f"/customers/{other['id']}/dependents", headers=ctx.headers).json() == []


def test_customer_full_record(ctx):
    payload = {k: v for k, v in FULL_DEPENDENT.items() if k != "kinship"}
    payload["name"] = "Full Holder"
    created = ctx.client.post("/customers", headers=ctx.headers, json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["health_card"] == "898000000000000"
    assert body["mother_name"] == "Foo Holder"
    assert body["blood_type"] == "O+"
    assert "kinship" not in body


def test_customer_birth_date_is_optional(ctx):
    response = ctx.client.get(f"/customers/{ctx.holder}", headers=ctx.headers)
    assert response.status_code == 200
    assert response.json()["birth_date"] is None
