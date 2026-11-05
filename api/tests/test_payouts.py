"""Professional payouts: what each professional is owed for a period.

Only completed (``realizada``) appointments pay, and each pays the
professional's fee snapshotted when it was booked. Scheduled and cancelled
appointments are listed for control but add nothing.
"""

from datetime import date, datetime

import pytest

from conftest import activate_plan, auth, auth_as, ensure_plan, sign
from app import clock
from app.reports import PayoutStats, matches_professional, payout_cents, payouts_by_professional

W43_MON = datetime(2026, 10, 19, 8, 0)
W43_TUE = datetime(2026, 10, 20, 8, 0)
W44_MON = datetime(2026, 10, 26, 8, 0)
NOV_MON = datetime(2026, 11, 2, 8, 0)


@pytest.fixture()
def ctx(client):
    admin = auth(client)
    plan = ensure_plan(client, admin, discount_percent=20)
    cardio = client.post("/specialties", headers=admin, json={"name": "Cardiologia", "price_cents": 10000}).json()
    clinic_b = client.post("/clinics", headers=admin, json={"name": "Clinic Bar", "city": "X", "fee_cents": 2000}).json()
    lorem = client.post(
        "/professionals", headers=admin, json={"name": "Lorem Ipsum", "specialty_id": 1, "clinic_id": 1, "price_cents": 5000}
    ).json()["id"]
    dolor = client.post(
        "/professionals",
        headers=admin,
        json={"name": "Dolor Sit", "specialty_id": cardio["id"], "clinic_id": clinic_b["id"], "price_cents": 7000},
    ).json()["id"]
    amet = client.post(
        "/professionals", headers=admin, json={"name": "Amet Consectetur", "specialty_id": 1, "clinic_id": 1, "price_cents": 4000}
    ).json()["id"]
    holder = client.post("/customers", headers=admin, json={"name": "Foo Holder"}).json()["id"]
    other = client.post("/customers", headers=admin, json={"name": "Bar Holder"}).json()["id"]
    activate_plan(client, admin, holder, plan=plan)
    activate_plan(client, admin, other, plan=plan)

    def book(customer, professional, when, status="agendada"):
        response = client.post(
            "/appointments",
            headers=admin,
            json={"customer_id": customer, "professional_id": professional, "marked_at": when.isoformat(), "status": status if status != "realizada" else "agendada"},
        )
        assert response.status_code == 201, response.text
        if status == "realizada":  # completion is the professional's signature
            sign(client, response.json()["id"])
        return response.json()

    # Lorem: two completed in W43 (5000 each), one scheduled in W44, one cancelled in W44.
    book(holder, lorem, W43_MON, "realizada")
    book(other, lorem, W43_TUE, "realizada")
    book(holder, lorem, W44_MON)
    book(other, lorem, W44_MON.replace(hour=9), "cancelada")
    # Dolor: one completed in W44 (7000), one completed in November (outside October).
    book(other, dolor, W44_MON, "realizada")
    book(holder, dolor, NOV_MON, "realizada")
    # Amet: nothing at all.

    class Ctx:
        pass

    c = Ctx()
    c.client, c.admin = client, admin
    c.lorem, c.dolor, c.amet = lorem, dolor, amet
    c.cardio, c.clinic_b = cardio["id"], clinic_b["id"]
    return c


OCTOBER = "start=2026-10-01&end=2026-10-31"


def test_payouts_pay_only_completed_appointments_at_the_snapshot_fee(ctx):
    body = ctx.client.get(f"/reports/payouts?granularity=week&{OCTOBER}", headers=ctx.admin).json()
    rows = {row["name"]: row for row in body["professionals"]}
    lorem = rows["Lorem Ipsum"]
    assert (lorem["completed"], lorem["scheduled"], lorem["cancelled"]) == (2, 1, 1)
    assert lorem["payout_cents"] == 2 * 5000  # scheduled and cancelled pay nothing
    dolor = rows["Dolor Sit"]
    assert (dolor["completed"], dolor["payout_cents"]) == (1, 7000)  # November is outside the range
    amet = rows["Amet Consectetur"]  # listed although nothing is due
    assert (amet["completed"], amet["payout_cents"]) == (0, 0)
    assert body["professionals_count"] == 3
    assert body["totals"]["payout_cents"] == 17000
    assert body["totals"]["completed"] == 3
    assert body["totals"]["cancelled"] == 1
    # Highest payout first.
    assert [row["name"] for row in body["professionals"]] == ["Lorem Ipsum", "Dolor Sit", "Amet Consectetur"]


def test_payouts_per_period(ctx):
    body = ctx.client.get(f"/reports/payouts?granularity=week&{OCTOBER}", headers=ctx.admin).json()
    by_key = {p["period"]: p for p in body["periods"]}
    assert by_key["2026-W43"]["payout_cents"] == 10000
    assert by_key["2026-W44"]["payout_cents"] == 7000
    assert by_key["2026-W44"]["scheduled"] == 1
    assert by_key["2026-W44"]["cancelled"] == 1
    lorem = next(row for row in body["professionals"] if row["name"] == "Lorem Ipsum")
    lorem_periods = {p["period"]: p for p in lorem["periods"]}
    assert lorem_periods["2026-W43"]["payout_cents"] == 10000
    assert lorem_periods["2026-W44"]["payout_cents"] == 0
    assert lorem_periods["2026-W40"]["payout_cents"] == 0  # empty weeks are still listed

    monthly = ctx.client.get("/reports/payouts?granularity=month&start=2026-10-01&end=2026-11-30", headers=ctx.admin).json()
    assert [p["period"] for p in monthly["periods"]] == ["2026-10", "2026-11"]
    assert [p["payout_cents"] for p in monthly["periods"]] == [17000, 7000]


def test_payout_uses_the_fee_snapshot_not_the_current_fee(ctx):
    # Raising Lorem's fee after the appointments does not change what is due.
    current = ctx.client.get(f"/professionals/{ctx.lorem}", headers=ctx.admin).json()
    updated = ctx.client.put(
        f"/professionals/{ctx.lorem}",
        headers=ctx.admin,
        json={**current, "price_cents": 9000},
    )
    assert updated.status_code == 200, updated.text
    body = ctx.client.get(f"/reports/payouts?{OCTOBER}", headers=ctx.admin).json()
    lorem = next(row for row in body["professionals"] if row["name"] == "Lorem Ipsum")
    assert lorem["payout_cents"] == 10000
    assert lorem["fee_cents"] == 9000  # the current fee is shown for reference


def test_signing_an_appointment_moves_it_into_the_payout(ctx):
    before = ctx.client.get(f"/reports/payouts?{OCTOBER}&professional_id={ctx.lorem}", headers=ctx.admin).json()
    assert before["totals"]["payout_cents"] == 10000
    scheduled = next(
        line
        for line in ctx.client.get(f"/reports/payouts/{ctx.lorem}?{OCTOBER}", headers=ctx.admin).json()["lines"]
        if line["status"] == "agendada"
    )
    appointment = ctx.client.get(f"/appointments/{scheduled['appointment_id']}", headers=ctx.admin).json()
    # The staff cannot complete it...
    refused = ctx.client.put(
        f"/appointments/{appointment['id']}",
        headers=ctx.admin,
        json={
            "customer_id": appointment["customer_id"],
            "dependent_id": appointment["dependent_id"],
            "professional_id": appointment["professional_id"],
            "marked_at": appointment["marked_at"],
            "status": "realizada",
            "notes": appointment["notes"],
        },
    )
    assert refused.status_code == 403
    # ...only the professional's signature does.
    sign(ctx.client, appointment["id"])
    after = ctx.client.get(f"/reports/payouts?{OCTOBER}&professional_id={ctx.lorem}", headers=ctx.admin).json()
    assert after["totals"]["payout_cents"] == 15000
    assert after["totals"]["scheduled"] == 0
    statement = ctx.client.get(f"/reports/payouts/{ctx.lorem}?{OCTOBER}", headers=ctx.admin).json()
    signed = next(line for line in statement["lines"] if line["appointment_id"] == appointment["id"])
    assert signed["signed_at"] is not None


def test_payouts_filter_by_name(ctx):
    body = ctx.client.get(f"/reports/payouts?{OCTOBER}&name=lorem", headers=ctx.admin).json()
    assert [row["name"] for row in body["professionals"]] == ["Lorem Ipsum"]
    assert body["totals"]["payout_cents"] == 10000  # totals follow the filter
    partial = ctx.client.get(f"/reports/payouts?{OCTOBER}&name=%20SIT%20", headers=ctx.admin).json()
    assert [row["name"] for row in partial["professionals"]] == ["Dolor Sit"]
    none = ctx.client.get(f"/reports/payouts?{OCTOBER}&name=nobody", headers=ctx.admin).json()
    assert none["professionals"] == []
    assert none["professionals_count"] == 0
    assert none["totals"]["payout_cents"] == 0


def test_payouts_filter_by_specialty(ctx):
    body = ctx.client.get(f"/reports/payouts?{OCTOBER}&specialty_id={ctx.cardio}", headers=ctx.admin).json()
    assert [row["name"] for row in body["professionals"]] == ["Dolor Sit"]
    assert body["professionals"][0]["specialty_name"] == "Cardiologia"
    general = ctx.client.get(f"/reports/payouts?{OCTOBER}&specialty_id=1", headers=ctx.admin).json()
    assert [row["name"] for row in general["professionals"]] == ["Lorem Ipsum", "Amet Consectetur"]
    assert general["totals"]["payout_cents"] == 10000
    # Filters combine.
    both = ctx.client.get(f"/reports/payouts?{OCTOBER}&specialty_id=1&name=amet", headers=ctx.admin).json()
    assert [row["name"] for row in both["professionals"]] == ["Amet Consectetur"]
    empty = ctx.client.get(f"/reports/payouts?{OCTOBER}&specialty_id={ctx.cardio}&name=lorem", headers=ctx.admin).json()
    assert empty["professionals"] == []


def test_payouts_filter_by_clinic_and_professional(ctx):
    body = ctx.client.get(f"/reports/payouts?{OCTOBER}&clinic_id={ctx.clinic_b}", headers=ctx.admin).json()
    assert [row["name"] for row in body["professionals"]] == ["Dolor Sit"]
    one = ctx.client.get(f"/reports/payouts?{OCTOBER}&professional_id={ctx.amet}", headers=ctx.admin).json()
    assert [row["name"] for row in one["professionals"]] == ["Amet Consectetur"]


def test_payout_statement_lists_every_appointment_with_what_it_pays(ctx):
    body = ctx.client.get(f"/reports/payouts/{ctx.lorem}?{OCTOBER}", headers=ctx.admin).json()
    assert body["name"] == "Lorem Ipsum"
    assert body["specialty_name"] == "Clínico geral"
    assert body["payout_cents"] == 10000
    assert (body["completed"], body["scheduled"], body["cancelled"]) == (2, 1, 1)
    lines = body["lines"]
    assert [line["marked_at"][:16] for line in lines] == ["2026-10-19T08:00", "2026-10-20T08:00", "2026-10-26T08:00", "2026-10-26T09:00"]
    assert [line["status"] for line in lines] == ["realizada", "realizada", "agendada", "cancelada"]
    assert [line["payout_cents"] for line in lines] == [5000, 5000, 0, 0]
    assert lines[0]["patient"] == "Foo Holder"
    assert lines[1]["patient"] == "Bar Holder"
    assert lines[0]["plan"] == "Plan Foo"
    assert lines[0]["gross_cents"] == 15000 and lines[0]["amount_cents"] == 12000
    assert sum(line["payout_cents"] for line in lines) == body["payout_cents"]
    assert ctx.client.get(f"/reports/payouts/9999?{OCTOBER}", headers=ctx.admin).status_code == 404


def test_payouts_csv(ctx):
    response = ctx.client.get(f"/reports/payouts.csv?{OCTOBER}", headers=ctx.admin)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="payouts_2026-10-01_2026-10-31.csv"' in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].split(";")[:4] == ["date", "time", "professional_id", "professional"]
    assert len(lines) == 1 + 3  # only completed appointments are rows
    assert sum(int(line.split(";")[-1]) for line in lines[1:]) == 17000
    assert "cancelada" not in response.text
    filtered = ctx.client.get(f"/reports/payouts.csv?{OCTOBER}&name=dolor", headers=ctx.admin).text.strip().splitlines()
    assert len(filtered) == 2
    assert filtered[1].split(";")[3] == "Dolor Sit"


def test_payouts_validation_and_defaults(ctx):
    assert ctx.client.get("/reports/payouts?granularity=day", headers=ctx.admin).status_code == 400
    assert ctx.client.get("/reports/payouts?start=2026-10-10&end=2026-10-01", headers=ctx.admin).status_code == 400
    defaults = ctx.client.get("/reports/payouts", headers=ctx.admin).json()
    assert defaults["granularity"] == "month"
    assert defaults["end"] == clock.today().isoformat()


@pytest.mark.parametrize("role, expected", [("admin", 200), ("supervisor", 403), ("attendant", 403), ("client", 403)])
def test_payouts_are_for_admins_and_professionals_only(ctx, role, expected):
    customer = ctx.client.post("/customers", headers=ctx.admin, json={"name": "Client Holder"}).json()["id"]
    headers = auth_as(ctx.client, role, customer)
    assert ctx.client.get(f"/reports/payouts?{OCTOBER}", headers=headers).status_code == expected
    assert ctx.client.get(f"/reports/payouts/{ctx.lorem}?{OCTOBER}", headers=headers).status_code == expected
    assert ctx.client.get(f"/reports/payouts.csv?{OCTOBER}", headers=headers).status_code == expected


def test_professional_sees_only_its_own_payout(ctx):
    lorem = auth_as(ctx.client, "professional", professional_id=ctx.lorem)
    body = ctx.client.get(f"/reports/payouts?granularity=week&{OCTOBER}", headers=lorem).json()
    assert [row["name"] for row in body["professionals"]] == ["Lorem Ipsum"]
    assert body["totals"]["payout_cents"] == 10000
    # Filters cannot widen the scope.
    widened = ctx.client.get(f"/reports/payouts?{OCTOBER}&name=dolor&specialty_id={ctx.cardio}", headers=lorem).json()
    assert [row["name"] for row in widened["professionals"]] == ["Lorem Ipsum"]
    assert ctx.client.get(f"/reports/payouts?{OCTOBER}&professional_id={ctx.dolor}", headers=lorem).status_code == 403
    # Own statement and CSV, not another professional's.
    statement = ctx.client.get(f"/reports/payouts/{ctx.lorem}?{OCTOBER}", headers=lorem).json()
    assert statement["payout_cents"] == 10000
    assert ctx.client.get(f"/reports/payouts/{ctx.dolor}?{OCTOBER}", headers=lorem).status_code == 403
    csv_lines = ctx.client.get(f"/reports/payouts.csv?{OCTOBER}&name=dolor", headers=lorem).text.strip().splitlines()
    assert len(csv_lines) == 3 and all("Lorem Ipsum" in line for line in csv_lines[1:])


# --- pure rules -----------------------------------------------------------------------


class Row:
    def __init__(self, professional_id, status, fee, marked_at=datetime(2026, 10, 19, 8, 0)):
        self.professional_id = professional_id
        self.status = status
        self.professional_price_cents = fee
        self.marked_at = marked_at


class Pro:
    def __init__(self, id, name, specialty_id=1, clinic_id=1):
        self.id, self.name, self.specialty_id, self.clinic_id = id, name, specialty_id, clinic_id


def test_payout_cents_rule():
    assert payout_cents(Row(1, "realizada", 5000)) == 5000
    assert payout_cents(Row(1, "agendada", 5000)) == 0
    assert payout_cents(Row(1, "cancelada", 5000)) == 0


def test_payout_stats_counts_every_status_but_pays_completed_only():
    stats = PayoutStats()
    for row in [Row(1, "realizada", 5000), Row(1, "realizada", 6000), Row(1, "agendada", 5000), Row(1, "cancelada", 5000)]:
        stats.add(row)
    assert (stats.completed, stats.scheduled, stats.cancelled, stats.payout_cents) == (2, 1, 1, 11000)


def test_payouts_by_professional_orders_by_amount_and_keeps_idle_ones():
    pros = [Pro(1, "Zed"), Pro(2, "Ana"), Pro(3, "Bob")]
    rows = [Row(1, "realizada", 100), Row(3, "realizada", 500), Row(2, "agendada", 900)]
    result = payouts_by_professional(pros, rows, date(2026, 10, 19), date(2026, 10, 25), "week")
    assert [(item.professional.name, item.totals.payout_cents) for item in result] == [("Bob", 500), ("Zed", 100), ("Ana", 0)]
    assert [bucket.key for bucket, _ in result[0].periods] == ["2026-W43"]


def test_matches_professional():
    pro = Pro(1, "Lorem Ipsum", specialty_id=2, clinic_id=3)
    assert matches_professional(pro, None, None, None)
    assert matches_professional(pro, "  ipsum ", 2, 3)
    assert matches_professional(pro, "LOREM", None, None)
    assert not matches_professional(pro, "dolor", None, None)
    assert not matches_professional(pro, None, 1, None)
    assert not matches_professional(pro, None, None, 1)
