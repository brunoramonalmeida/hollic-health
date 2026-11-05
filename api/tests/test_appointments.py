"""API tests for appointments: slot rules on create (POST) and edit (PUT), and
amounts computed from the price list."""

from datetime import datetime, timedelta

import pytest

from conftest import activate_plan, auth, sign

# 2026-10-26 is a Monday (weekday 0); 2026-10-27 is a Tuesday (weekday 1).
MONDAY_8 = datetime(2026, 10, 26, 8, 0)
MONDAY_830 = datetime(2026, 10, 26, 8, 30)
MONDAY_9 = datetime(2026, 10, 26, 9, 0)
MONDAY_815 = datetime(2026, 10, 26, 8, 15)
TUESDAY_8 = datetime(2026, 10, 27, 8, 0)

# Monday 08:00-09:00 in 30-minute slots: 08:00 and 08:30.
MONDAY_HOURS = [{"weekday": 0, "start": "08:00", "end": "09:00"}]


@pytest.fixture()
def ctx(client):
    """Two professionals (Monday 08:00/08:30), two customers and one dependent."""
    headers = auth(client)

    def professional(name, working_hours=MONDAY_HOURS, **extra):
        payload = {"name": name, "specialty_id": 1, "clinic_id": 1, "price_cents": 5000, **extra}
        if working_hours is not None:
            payload["working_hours"] = working_hours
        created = client.post("/professionals", headers=headers, json=payload)
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def customer(name):
        created = client.post("/customers", headers=headers, json={"name": name})
        assert created.status_code == 201
        activate_plan(client, headers, created.json()["id"])
        return created.json()["id"]

    holder = customer("Foo Holder")
    dependent = client.post(
        f"/customers/{holder}/dependents",
        headers=headers,
        json={"name": "Ipsum Child", "birth_date": "2018-02-20"},
    )
    assert dependent.status_code == 201, dependent.text

    class Ctx:
        pass

    c = Ctx()
    c.client = client
    c.headers = headers
    c.pro_a = professional("Lorem Ipsum")
    c.pro_b = professional("Dolor Sit")
    c.holder = holder
    c.dependent = dependent.json()["id"]
    c.other = customer("Bar Holder")
    c.new_professional = professional

    def body(**overrides):
        data = {
            "customer_id": c.holder,
            "dependent_id": None,
            "professional_id": c.pro_a,
            "marked_at": MONDAY_8.isoformat(),
            "status": "agendada",
            "notes": "",
        }
        data.update(overrides)
        if isinstance(data["marked_at"], datetime):
            data["marked_at"] = data["marked_at"].isoformat()
        return data

    def create(**overrides):
        return client.post("/appointments", headers=headers, json=body(**overrides))

    def update(item_id, **overrides):
        return client.put(f"/appointments/{item_id}", headers=headers, json=body(**overrides))

    c.body = body
    c.create = create
    c.update = update
    return c


# --- create: working hours and time off ---------------------------------------


def test_create_in_working_slot(ctx):
    response = ctx.create()
    assert response.status_code == 201
    assert response.json()["marked_at"] == "2026-10-26T08:00:00"


def test_create_rejected_outside_working_hours(ctx):
    response = ctx.create(marked_at=MONDAY_9)
    assert response.status_code == 400
    assert response.json()["detail"] == "Professional does not work on Monday at 09:00"


def test_create_rejected_off_the_slot_grid(ctx):
    # 08:15 is inside 08:00-09:00 but not a 30-minute slot start.
    assert ctx.create(marked_at=MONDAY_815).status_code == 400


def test_slot_grid_follows_the_professional_slot_minutes(ctx):
    pro = ctx.new_professional("Hourly", slot_minutes=60)
    assert ctx.create(professional_id=pro, marked_at=MONDAY_830).status_code == 400
    assert ctx.create(professional_id=pro, marked_at=MONDAY_8).status_code == 201


def test_create_rejected_on_weekday_without_hours(ctx):
    response = ctx.create(marked_at=TUESDAY_8)
    assert response.status_code == 400
    assert "Tuesday" in response.json()["detail"]


def test_create_rejected_when_professional_has_empty_week(ctx):
    pro = ctx.new_professional("Amet Consectetur", working_hours=[])
    assert ctx.create(professional_id=pro).status_code == 400


def test_professional_without_hours_gets_the_default_week(ctx):
    pro = ctx.new_professional("Default Week", working_hours=None)
    # Tuesday 08:00 is in the default week (Mon-Fri 08-12 / 13-17).
    assert ctx.create(professional_id=pro, marked_at=TUESDAY_8).status_code == 201
    assert ctx.create(professional_id=pro, customer_id=ctx.other, marked_at=datetime(2026, 10, 27, 12, 0)).status_code == 400
    assert ctx.create(professional_id=pro, customer_id=ctx.other, marked_at=datetime(2026, 10, 31, 8, 0)).status_code == 400


def test_create_allowed_on_following_weeks_of_the_same_weekday(ctx):
    assert ctx.create(marked_at=MONDAY_8 + timedelta(weeks=2)).status_code == 201


def test_create_with_seconds_is_stored_as_hh_mm(ctx):
    response = ctx.create(marked_at=MONDAY_8.replace(second=45, microsecond=123000))
    assert response.status_code == 201
    assert response.json()["marked_at"] == "2026-10-26T08:00:00"


def test_create_with_timezone_offset_keeps_wall_clock_time(ctx):
    response = ctx.create(marked_at="2026-10-26T08:00:00-03:00")
    assert response.status_code == 201
    assert response.json()["marked_at"] == "2026-10-26T08:00:00"


def test_ordinary_validations_still_apply(ctx):
    assert ctx.create(customer_id=9999).status_code == 404
    assert ctx.create(professional_id=9999).status_code == 404
    assert ctx.create(status="whatever").status_code == 400


def test_working_hours_change_applies_to_new_appointments(ctx):
    assert ctx.create(marked_at=MONDAY_830).status_code == 201
    put = ctx.client.put(
        f"/professionals/{ctx.pro_a}/working-hours",
        headers=ctx.headers,
        json={"ranges": [{"weekday": 0, "start": "08:00", "end": "08:30"}]},
    )
    assert put.status_code == 200
    assert ctx.create(customer_id=ctx.other, marked_at=MONDAY_830).status_code == 400


def test_create_rejected_on_whole_day_off(ctx):
    off = ctx.client.post(
        f"/professionals/{ctx.pro_a}/time-off",
        headers=ctx.headers,
        json={"date": "2026-10-26", "reason": "Holiday"},
    )
    assert off.status_code == 201
    response = ctx.create()
    assert response.status_code == 400
    assert response.json()["detail"] == "Professional is off on 2026-10-26 at 08:00"
    # The following week is unaffected.
    assert ctx.create(marked_at=MONDAY_8 + timedelta(weeks=1)).status_code == 201


def test_create_rejected_in_partial_time_off_only(ctx):
    off = ctx.client.post(
        f"/professionals/{ctx.pro_a}/time-off",
        headers=ctx.headers,
        json={"date": "2026-10-26", "start": "08:30", "end": "09:00"},
    )
    assert off.status_code == 201
    assert ctx.create(marked_at=MONDAY_830).status_code == 400
    assert ctx.create(marked_at=MONDAY_8).status_code == 201


def test_time_off_of_one_professional_does_not_affect_another(ctx):
    ctx.client.post(
        f"/professionals/{ctx.pro_a}/time-off", headers=ctx.headers, json={"date": "2026-10-26"}
    )
    assert ctx.create(professional_id=ctx.pro_b).status_code == 201


def test_removing_time_off_frees_the_slot(ctx):
    off = ctx.client.post(
        f"/professionals/{ctx.pro_a}/time-off", headers=ctx.headers, json={"date": "2026-10-26"}
    ).json()
    assert ctx.create().status_code == 400
    deleted = ctx.client.delete(
        f"/professionals/{ctx.pro_a}/time-off/{off['id']}", headers=ctx.headers
    )
    assert deleted.status_code == 204
    assert ctx.create().status_code == 201


# --- create: professional double booking ----------------------------------------


def test_professional_cannot_be_booked_twice_in_same_slot(ctx):
    assert ctx.create().status_code == 201
    second = ctx.create(customer_id=ctx.other)
    assert second.status_code == 409
    assert second.json()["detail"] == "Professional already has an appointment at 2026-10-26 08:00"


def test_completed_appointment_blocks_the_slot(ctx):
    created = ctx.create()
    assert created.status_code == 201
    sign(ctx.client, created.json()["id"])
    assert ctx.create(customer_id=ctx.other).status_code == 409


def test_completion_is_only_by_the_professional_signature(ctx):
    # Nobody types "completed": not on create...
    refused = ctx.create(status="realizada")
    assert refused.status_code == 403
    assert "sign" in refused.json()["detail"]
    # ...nor on edit.
    item_id = ctx.create().json()["id"]
    refused = ctx.update(item_id, status="realizada")
    assert refused.status_code == 403
    assert ctx.client.get(f"/appointments/{item_id}", headers=ctx.headers).json()["status"] == "agendada"


def test_cancelled_appointment_does_not_block_the_slot(ctx):
    assert ctx.create(status="cancelada").status_code == 201
    assert ctx.create(customer_id=ctx.other).status_code == 201


def test_cancelled_appointment_can_be_created_anywhere(ctx):
    assert ctx.create().status_code == 201
    assert ctx.create(customer_id=ctx.other, status="cancelada").status_code == 201
    assert ctx.create(status="cancelada", marked_at=TUESDAY_8).status_code == 201


def test_seconds_do_not_create_a_different_slot(ctx):
    assert ctx.create().status_code == 201
    assert ctx.create(customer_id=ctx.other, marked_at=MONDAY_8.replace(second=30)).status_code == 409


def test_same_professional_different_slots_same_day(ctx):
    assert ctx.create(marked_at=MONDAY_8).status_code == 201
    assert ctx.create(customer_id=ctx.other, marked_at=MONDAY_830).status_code == 201


def test_same_professional_same_time_on_another_week(ctx):
    assert ctx.create().status_code == 201
    assert ctx.create(customer_id=ctx.other, marked_at=MONDAY_8 + timedelta(weeks=1)).status_code == 201


def test_different_professionals_different_customers_share_the_slot(ctx):
    assert ctx.create(professional_id=ctx.pro_a, customer_id=ctx.holder).status_code == 201
    assert ctx.create(professional_id=ctx.pro_b, customer_id=ctx.other).status_code == 201


# --- create: customer double booking --------------------------------------------


def test_customer_cannot_be_with_two_professionals_in_same_slot(ctx):
    assert ctx.create(professional_id=ctx.pro_a).status_code == 201
    second = ctx.create(professional_id=ctx.pro_b)
    assert second.status_code == 409
    assert second.json()["detail"] == (
        "Customer already has an appointment with another professional at 2026-10-26 08:00"
    )


def test_customer_can_see_two_professionals_at_different_times(ctx):
    assert ctx.create(professional_id=ctx.pro_a, marked_at=MONDAY_8).status_code == 201
    assert ctx.create(professional_id=ctx.pro_b, marked_at=MONDAY_830).status_code == 201


def test_customer_conflict_ignores_cancelled_appointment(ctx):
    assert ctx.create(professional_id=ctx.pro_a, status="cancelada").status_code == 201
    assert ctx.create(professional_id=ctx.pro_b).status_code == 201


def test_customer_conflict_applies_to_completed_appointment(ctx):
    created = ctx.create(professional_id=ctx.pro_a)
    assert created.status_code == 201
    sign(ctx.client, created.json()["id"])
    assert ctx.create(professional_id=ctx.pro_b).status_code == 409


def test_dependent_cannot_be_with_two_professionals_in_same_slot(ctx):
    assert ctx.create(professional_id=ctx.pro_a, dependent_id=ctx.dependent).status_code == 201
    assert ctx.create(professional_id=ctx.pro_b, dependent_id=ctx.dependent).status_code == 409


def test_holder_and_dependent_may_have_parallel_appointments(ctx):
    assert ctx.create(professional_id=ctx.pro_a, dependent_id=None).status_code == 201
    assert ctx.create(professional_id=ctx.pro_b, dependent_id=ctx.dependent).status_code == 201


# --- amounts from the price list ------------------------------------------------


def test_amounts_are_computed_not_taken_from_the_body(ctx):
    response = ctx.create(fee_cents=1, specialty_price_cents=1, professional_price_cents=1, amount_cents=1)
    assert response.status_code == 201
    body = response.json()
    assert body["fee_cents"] == 2000  # Clinic Foo
    assert body["specialty_price_cents"] == 8000  # Clínico geral
    assert body["professional_price_cents"] == 5000  # fixture professional
    assert body["amount_cents"] == 15000


def test_specialty_and_clinic_come_from_the_professional(ctx):
    clinic = ctx.client.post(
        "/clinics", headers=ctx.headers, json={"name": "Clinic Bar", "city": "X", "fee_cents": 300}
    ).json()
    specialty = ctx.client.post(
        "/specialties", headers=ctx.headers, json={"name": "Specialty Bar", "price_cents": 700}
    ).json()
    pro = ctx.new_professional(
        "Elsewhere", specialty_id=specialty["id"], clinic_id=clinic["id"], price_cents=1000
    )
    response = ctx.create(professional_id=pro, specialty_id=1, clinic_id=1)
    assert response.status_code == 201
    body = response.json()
    assert body["clinic_id"] == clinic["id"]
    assert body["specialty_id"] == specialty["id"]
    assert body["amount_cents"] == 300 + 700 + 1000


def test_price_list_changes_do_not_alter_existing_appointment(ctx):
    item = ctx.create().json()
    assert item["amount_cents"] == 15000
    put = ctx.client.put(
        f"/professionals/{ctx.pro_a}",
        headers=ctx.headers,
        json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 9000},
    )
    assert put.status_code == 200
    # Editing notes keeps the snapshot taken at scheduling time.
    edited = ctx.update(item["id"], notes="follow-up")
    assert edited.status_code == 200
    assert edited.json()["professional_price_cents"] == 5000
    assert edited.json()["amount_cents"] == 15000
    # A new appointment uses the new price.
    fresh = ctx.create(customer_id=ctx.other, marked_at=MONDAY_830)
    assert fresh.json()["amount_cents"] == 2000 + 8000 + 9000


def test_changing_professional_recomputes_the_amounts(ctx):
    pro = ctx.new_professional("Pricier", price_cents=12000)
    item = ctx.create().json()
    edited = ctx.update(item["id"], professional_id=pro)
    assert edited.status_code == 200
    assert edited.json()["professional_price_cents"] == 12000
    assert edited.json()["amount_cents"] == 2000 + 8000 + 12000


# --- edit -------------------------------------------------------------------------


def test_edit_without_changing_slot_does_not_conflict_with_itself(ctx):
    item_id = ctx.create().json()["id"]
    response = ctx.update(item_id, notes="updated")
    assert response.status_code == 200
    assert response.json()["notes"] == "updated"


def test_edit_to_free_working_slot(ctx):
    item_id = ctx.create(marked_at=MONDAY_8).json()["id"]
    response = ctx.update(item_id, marked_at=MONDAY_830)
    assert response.status_code == 200
    assert response.json()["marked_at"] == "2026-10-26T08:30:00"


def test_edit_rejected_when_moving_outside_working_hours(ctx):
    item_id = ctx.create().json()["id"]
    assert ctx.update(item_id, marked_at=MONDAY_9).status_code == 400
    assert ctx.update(item_id, marked_at=TUESDAY_8).status_code == 400
    current = ctx.client.get(f"/appointments/{item_id}", headers=ctx.headers).json()
    assert current["marked_at"] == "2026-10-26T08:00:00"


def test_edit_rejected_when_moving_into_time_off(ctx):
    item_id = ctx.create(marked_at=MONDAY_8).json()["id"]
    ctx.client.post(
        f"/professionals/{ctx.pro_a}/time-off",
        headers=ctx.headers,
        json={"date": "2026-10-26", "start": "08:30", "end": "09:00"},
    )
    assert ctx.update(item_id, marked_at=MONDAY_830).status_code == 400


def test_edit_rejected_when_moving_onto_another_appointment_of_the_professional(ctx):
    assert ctx.create(marked_at=MONDAY_8).status_code == 201
    item_id = ctx.create(customer_id=ctx.other, marked_at=MONDAY_830).json()["id"]
    assert ctx.update(item_id, customer_id=ctx.other, marked_at=MONDAY_8).status_code == 409


def test_edit_rejected_when_changing_professional_to_a_busy_one(ctx):
    assert ctx.create(professional_id=ctx.pro_a, customer_id=ctx.other).status_code == 201
    item_id = ctx.create(professional_id=ctx.pro_b).json()["id"]
    assert ctx.update(item_id, professional_id=ctx.pro_a).status_code == 409


def test_edit_rejected_when_changing_professional_to_one_not_working(ctx):
    pro = ctx.new_professional("Tuesday Only", working_hours=[{"weekday": 1, "start": "08:00", "end": "09:00"}])
    item_id = ctx.create().json()["id"]
    assert ctx.update(item_id, professional_id=pro).status_code == 400


def test_edit_rejected_when_customer_becomes_double_booked(ctx):
    assert ctx.create(professional_id=ctx.pro_a, customer_id=ctx.holder).status_code == 201
    item_id = ctx.create(professional_id=ctx.pro_b, customer_id=ctx.other).json()["id"]
    assert ctx.update(item_id, professional_id=ctx.pro_b, customer_id=ctx.holder).status_code == 409


def test_signed_appointment_keeps_the_slot_and_is_frozen(ctx):
    item_id = ctx.create().json()["id"]
    sign(ctx.client, item_id)
    assert ctx.create(customer_id=ctx.other).status_code == 409
    # Notes may still be edited (status stays completed)...
    assert ctx.update(item_id, status="realizada", notes="record").status_code == 200
    # ...but patient, time and "back to scheduled" are frozen until the signature is revoked.
    assert ctx.update(item_id, status="agendada").status_code == 409
    moved = ctx.update(item_id, status="realizada", marked_at=(MONDAY_8 + timedelta(minutes=30)).isoformat())
    assert moved.status_code == 409
    assert "Signed" in moved.json()["detail"]
    # The staff may cancel it, which drops the signature and frees the slot.
    cancelled = ctx.update(item_id, status="cancelada")
    assert cancelled.status_code == 200
    assert cancelled.json()["signed_at"] is None
    assert ctx.create(customer_id=ctx.other).status_code == 201


def test_cancelling_frees_the_slot_for_others(ctx):
    item_id = ctx.create().json()["id"]
    assert ctx.update(item_id, status="cancelada").status_code == 200
    assert ctx.create(customer_id=ctx.other).status_code == 201


def test_cancelling_is_allowed_even_if_time_is_no_longer_worked(ctx):
    item_id = ctx.create(marked_at=MONDAY_830).json()["id"]
    put = ctx.client.put(
        f"/professionals/{ctx.pro_a}/working-hours",
        headers=ctx.headers,
        json={"ranges": [{"weekday": 0, "start": "08:00", "end": "08:30"}]},
    )
    assert put.status_code == 200
    assert ctx.update(item_id, marked_at=MONDAY_830, status="cancelada").status_code == 200


def test_reactivating_cancelled_appointment_requires_free_slot(ctx):
    item_id = ctx.create(status="cancelada").json()["id"]
    assert ctx.create(customer_id=ctx.other).status_code == 201
    assert ctx.update(item_id, status="agendada").status_code == 409


def test_reactivating_cancelled_appointment_requires_working_time(ctx):
    item_id = ctx.create(status="cancelada", marked_at=TUESDAY_8).json()["id"]
    assert ctx.update(item_id, status="agendada", marked_at=TUESDAY_8).status_code == 400
    assert ctx.update(item_id, status="agendada", marked_at=MONDAY_8).status_code == 200


def test_edit_missing_appointment_is_404(ctx):
    assert ctx.update(9999).status_code == 404


def test_deleted_appointment_frees_the_slot(ctx):
    item_id = ctx.create().json()["id"]
    assert ctx.client.delete(f"/appointments/{item_id}", headers=ctx.headers).status_code == 204
    assert ctx.create(customer_id=ctx.other).status_code == 201
