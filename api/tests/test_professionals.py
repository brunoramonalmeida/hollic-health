"""API tests for professionals: default week, working hours, time off and the
slots calendar."""

import pytest

from conftest import activate_plan, auth, sign

HOURS = [
    {"weekday": 0, "start": "08:00", "end": "10:00"},
    {"weekday": 2, "start": "14:00", "end": "15:00"},
]


@pytest.fixture()
def ctx(client):
    headers = auth(client)

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers

    def create(name="Lorem Ipsum", **extra):
        payload = {"name": name, "specialty_id": 1, "clinic_id": 1, "price_cents": 5000, **extra}
        return client.post("/professionals", headers=headers, json=payload)

    def get(path):
        return client.get(path, headers=headers)

    c.create = create
    c.get = get
    return c


# --- default week and working hours ------------------------------------------


def test_default_week_endpoint(ctx):
    response = ctx.get("/professionals/default-week")
    assert response.status_code == 200
    body = response.json()
    assert body["slot_minutes"] == 30
    assert len(body["working_hours"]) == 10
    assert body["working_hours"][0] == {"weekday": 0, "start": "08:00", "end": "12:00"}
    assert all(item["weekday"] < 5 for item in body["working_hours"])


def test_new_professional_without_hours_gets_default_week(ctx):
    response = ctx.create()
    assert response.status_code == 201
    body = response.json()
    assert body["slot_minutes"] == 30
    assert body["working_hours"] == ctx.get("/professionals/default-week").json()["working_hours"]
    # Catalog prices are exposed so the client can quote the appointment.
    assert body["clinic_fee_cents"] == 2000
    assert body["specialty_price_cents"] == 8000
    assert body["price_cents"] == 5000


def test_new_professional_with_explicit_hours_and_slot_minutes(ctx):
    response = ctx.create(working_hours=HOURS, slot_minutes=20)
    assert response.status_code == 201
    assert response.json()["slot_minutes"] == 20
    assert response.json()["working_hours"] == HOURS


def test_new_professional_with_empty_hours_has_no_slots(ctx):
    response = ctx.create(working_hours=[])
    assert response.status_code == 201
    assert response.json()["working_hours"] == []


def test_working_hours_are_sorted_by_weekday_and_start(ctx):
    unsorted = [
        {"weekday": 2, "start": "14:00", "end": "15:00"},
        {"weekday": 0, "start": "13:00", "end": "14:00"},
        {"weekday": 0, "start": "08:00", "end": "10:00"},
    ]
    response = ctx.create(working_hours=unsorted)
    assert [(h["weekday"], h["start"]) for h in response.json()["working_hours"]] == [
        (0, "08:00"),
        (0, "13:00"),
        (2, "14:00"),
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"weekday": 7, "start": "08:00", "end": "09:00"},
        {"weekday": 0, "start": "8:00", "end": "09:00"},
        {"weekday": 0, "start": "08:00", "end": "25:00"},
        {"weekday": 0, "start": "09:00", "end": "08:00"},
        {"weekday": 0, "start": "09:00", "end": "09:00"},
    ],
)
def test_invalid_working_hours_are_rejected(ctx, bad):
    assert ctx.create(working_hours=[bad]).status_code == 422


def test_overlapping_working_hours_are_rejected(ctx):
    overlapping = [
        {"weekday": 0, "start": "08:00", "end": "10:00"},
        {"weekday": 0, "start": "09:30", "end": "11:00"},
    ]
    response = ctx.create(working_hours=overlapping)
    assert response.status_code == 400
    assert "overlap" in response.json()["detail"]


def test_adjacent_working_hours_are_allowed(ctx):
    adjacent = [
        {"weekday": 0, "start": "08:00", "end": "10:00"},
        {"weekday": 0, "start": "10:00", "end": "11:00"},
    ]
    assert ctx.create(working_hours=adjacent).status_code == 201


def test_slot_minutes_bounds(ctx):
    assert ctx.create(slot_minutes=4).status_code == 422
    assert ctx.create(slot_minutes=241).status_code == 422
    assert ctx.create(slot_minutes=5).status_code == 201


def test_update_without_hours_keeps_current_hours(ctx):
    pid = ctx.create(working_hours=HOURS).json()["id"]
    response = ctx.client.put(
        f"/professionals/{pid}",
        headers=ctx.headers,
        json={"name": "Renamed", "specialty_id": 1, "clinic_id": 1, "price_cents": 1},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["working_hours"] == HOURS


def test_update_with_hours_replaces_them(ctx):
    pid = ctx.create(working_hours=HOURS).json()["id"]
    new_hours = [{"weekday": 4, "start": "09:00", "end": "12:00"}]
    response = ctx.client.put(
        f"/professionals/{pid}",
        headers=ctx.headers,
        json={"name": "Lorem", "specialty_id": 1, "clinic_id": 1, "working_hours": new_hours},
    )
    assert response.status_code == 200
    assert response.json()["working_hours"] == new_hours
    assert ctx.get(f"/professionals/{pid}/working-hours").json() == new_hours


def test_put_working_hours_endpoint(ctx):
    pid = ctx.create().json()["id"]
    response = ctx.client.put(
        f"/professionals/{pid}/working-hours", headers=ctx.headers, json={"ranges": HOURS}
    )
    assert response.status_code == 200
    assert response.json() == HOURS
    assert ctx.get(f"/professionals/{pid}").json()["working_hours"] == HOURS


def test_working_hours_of_missing_professional(ctx):
    assert ctx.get("/professionals/9999/working-hours").status_code == 404
    assert ctx.client.put(
        "/professionals/9999/working-hours", headers=ctx.headers, json={"ranges": []}
    ).status_code == 404


# --- time off -------------------------------------------------------------------


def test_time_off_crud(ctx):
    pid = ctx.create().json()["id"]
    whole = ctx.client.post(
        f"/professionals/{pid}/time-off",
        headers=ctx.headers,
        json={"date": "2026-10-26", "reason": "Holiday"},
    )
    assert whole.status_code == 201
    assert whole.json()["start"] is None and whole.json()["end"] is None
    partial = ctx.client.post(
        f"/professionals/{pid}/time-off",
        headers=ctx.headers,
        json={"date": "2026-10-28", "start": "13:00", "end": "17:00"},
    )
    assert partial.status_code == 201
    listed = ctx.get(f"/professionals/{pid}/time-off").json()
    assert [item["date"] for item in listed] == ["2026-10-26", "2026-10-28"]
    filtered = ctx.get(f"/professionals/{pid}/time-off?start=2026-10-27&end=2026-10-31").json()
    assert [item["id"] for item in filtered] == [partial.json()["id"]]
    deleted = ctx.client.delete(
        f"/professionals/{pid}/time-off/{whole.json()['id']}", headers=ctx.headers
    )
    assert deleted.status_code == 204
    assert len(ctx.get(f"/professionals/{pid}/time-off").json()) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"date": "2026-10-26", "start": "08:00"},  # only one bound
        {"date": "2026-10-26", "end": "08:00"},
        {"date": "2026-10-26", "start": "09:00", "end": "08:00"},
        {"date": "2026-10-26", "start": "9:00", "end": "10:00"},
        {"date": "not-a-date"},
    ],
)
def test_invalid_time_off_is_rejected(ctx, bad):
    pid = ctx.create().json()["id"]
    assert ctx.client.post(f"/professionals/{pid}/time-off", headers=ctx.headers, json=bad).status_code == 422


def test_time_off_belongs_to_its_professional(ctx):
    a = ctx.create("A").json()["id"]
    b = ctx.create("B").json()["id"]
    off = ctx.client.post(
        f"/professionals/{a}/time-off", headers=ctx.headers, json={"date": "2026-10-26"}
    ).json()
    assert ctx.client.delete(f"/professionals/{b}/time-off/{off['id']}", headers=ctx.headers).status_code == 404
    assert ctx.get(f"/professionals/{b}/time-off").json() == []


# --- slots calendar -------------------------------------------------------------


def test_slots_for_a_week(ctx):
    pid = ctx.create(working_hours=HOURS, slot_minutes=30).json()["id"]
    response = ctx.get(f"/professionals/{pid}/slots?start=2026-10-26")
    assert response.status_code == 200
    days = response.json()
    assert [d["date"] for d in days] == [f"2026-10-{26 + i}" for i in range(6)] + ["2026-11-01"]
    assert days[0]["weekday"] == 0
    assert [s["time"] for s in days[0]["slots"]] == ["08:00", "08:30", "09:00", "09:30"]
    assert all(s["status"] == "available" for s in days[0]["slots"])
    assert days[1]["slots"] == []
    assert [s["time"] for s in days[2]["slots"]] == ["14:00", "14:30"]


def test_slots_reflect_appointments_and_time_off(ctx):
    pid = ctx.create(working_hours=HOURS).json()["id"]
    holder = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Foo Holder"}).json()
    activate_plan(ctx.client, ctx.headers, holder["id"])
    child = ctx.client.post(
        f"/customers/{holder['id']}/dependents",
        headers=ctx.headers,
        json={"name": "Ipsum Child", "birth_date": "2018-02-20"},
    ).json()
    booked = ctx.client.post(
        "/appointments",
        headers=ctx.headers,
        json={"customer_id": holder["id"], "professional_id": pid, "marked_at": "2026-10-26T08:00"},
    )
    assert booked.status_code == 201
    for_child = ctx.client.post(
        "/appointments",
        headers=ctx.headers,
        json={
            "customer_id": holder["id"],
            "dependent_id": child["id"],
            "professional_id": pid,
            "marked_at": "2026-10-26T08:30",
        },
    )
    assert for_child.status_code == 201
    sign(ctx.client, for_child.json()["id"])
    cancelled = ctx.client.post(
        "/appointments",
        headers=ctx.headers,
        json={
            "customer_id": holder["id"],
            "professional_id": pid,
            "marked_at": "2026-10-26T09:00",
            "status": "cancelada",
        },
    )
    assert cancelled.status_code == 201
    ctx.client.post(
        f"/professionals/{pid}/time-off",
        headers=ctx.headers,
        json={"date": "2026-10-26", "start": "09:30", "end": "10:00"},
    )
    day = ctx.get(f"/professionals/{pid}/slots?start=2026-10-26&end=2026-10-26").json()[0]
    assert [(s["time"], s["status"]) for s in day["slots"]] == [
        ("08:00", "booked"),
        ("08:30", "booked"),
        ("09:00", "available"),  # cancelled does not occupy
        ("09:30", "blocked"),
    ]
    assert day["slots"][0]["appointment_id"] == booked.json()["id"]
    assert day["slots"][0]["patient_name"] == "Foo Holder"
    assert day["slots"][1]["patient_name"] == "Ipsum Child"
    assert day["slots"][2]["appointment_id"] is None


def test_slots_do_not_leak_other_professionals(ctx):
    a = ctx.create("A", working_hours=HOURS).json()["id"]
    b = ctx.create("B", working_hours=HOURS).json()["id"]
    holder = ctx.client.post("/customers", headers=ctx.headers, json={"name": "Foo Holder"}).json()
    activate_plan(ctx.client, ctx.headers, holder["id"])
    ctx.client.post(
        "/appointments",
        headers=ctx.headers,
        json={"customer_id": holder["id"], "professional_id": a, "marked_at": "2026-10-26T08:00"},
    )
    day_b = ctx.get(f"/professionals/{b}/slots?start=2026-10-26&end=2026-10-26").json()[0]
    assert all(s["status"] == "available" for s in day_b["slots"])


def test_slots_range_validation(ctx):
    pid = ctx.create().json()["id"]
    assert ctx.get(f"/professionals/{pid}/slots").status_code == 422  # start required
    assert ctx.get(f"/professionals/{pid}/slots?start=2026-10-26&end=2026-10-25").status_code == 400
    assert ctx.get(f"/professionals/{pid}/slots?start=2026-01-01&end=2026-12-31").status_code == 400
    assert ctx.get("/professionals/9999/slots?start=2026-10-26").status_code == 404


def test_slots_follow_slot_minutes(ctx):
    pid = ctx.create(working_hours=HOURS, slot_minutes=40).json()["id"]
    day = ctx.get(f"/professionals/{pid}/slots?start=2026-10-26&end=2026-10-26").json()[0]
    assert [s["time"] for s in day["slots"]] == ["08:00", "08:40", "09:20"]
