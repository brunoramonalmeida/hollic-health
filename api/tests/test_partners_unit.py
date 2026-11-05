"""Unit tests of the corporate agreement rules (app.partners): six-month
contracts, frozen terms, the monthly headcount against the minimum, the
shortfall the company pays, enrollment checks and the monthly invoice."""

from datetime import date
from types import SimpleNamespace as NS

import pytest

from app.partners import (
    AGREEMENT_ENDED,
    AGREEMENT_IN_FORCE,
    AGREEMENT_SCHEDULED,
    AGREEMENT_SUSPENDED,
    INVOICE_NOTHING_DUE,
    INVOICE_OPEN,
    INVOICE_OVERDUE,
    INVOICE_PAID,
    PAYER_SHARED,
    EnrollmentError,
    Ledger,
    agreement_status,
    check_enrollment,
    contract_end,
    contract_share,
    default_share,
    grace_days_for,
    invoice_period,
    invoice_totals,
    offer_for,
    parse_period,
    payer_label,
    reference_period,
    share_on,
    shortfall_due_on,
    split_cents,
    terms_of,
)


def agreement(**fields):
    base = dict(
        name="Acme 2026/2",
        partner_id=1,
        starts_on=date(2026, 10, 1),
        ends_on=date(2027, 3, 31),
        min_employees=2,
        payer="company",
        company_share_percent=100,
        active=True,
        partner=NS(name="Acme", active=True),
        plans=[NS(modality_id=1, discount_percent=20, grace_days=0, company_share_percent=None)],
    )
    base.update(fields)
    return NS(**base)


PLAN = NS(id=1, name="Plan Foo")
OTHER_PLAN = NS(id=2, name="Plan Bar")


def fee(due_on, amount=7920, discount=1980, paid_on=None, company=None, company_paid_on=None, employee_paid_on=None):
    """A fee; ``company``: the company's part (default all of it, as when the
    company pays), paid on ``paid_on`` unless the parts' dates are given."""
    company = amount if company is None else company
    company_paid_on = company_paid_on or (paid_on if company else None)
    employee_paid_on = employee_paid_on or (paid_on if amount - company else None)
    return NS(
        due_on=due_on,
        amount_cents=amount,
        discount_cents=discount,
        paid_on=paid_on,
        paid_cents=amount if paid_on else None,
        sequence=1,
        company_share_cents=company,
        company_paid_on=company_paid_on,
        company_paid_cents=company if company_paid_on else None,
        employee_paid_on=employee_paid_on,
        employee_paid_cents=amount - company if employee_paid_on else None,
    )


def enrollment(customer_id, *fees):
    return NS(id=customer_id, customer_id=customer_id, customer=NS(name=f"E{customer_id}"), installments=list(fees))


def test_contracts_last_six_months():
    assert contract_end(date(2026, 10, 1)) == date(2027, 3, 31)
    assert contract_end(date(2026, 8, 31)) == date(2027, 2, 27)  # clamped like the monthly periods
    assert contract_end(date(2026, 11, 15)) == date(2027, 5, 14)


def test_frozen_terms_and_conditions():
    terms = agreement()
    assert terms_of(terms) == (1, date(2026, 10, 1), 2, "company", 100, ((1, 20, 0, None),))
    assert offer_for(terms.plans, 1) is terms.plans[0]
    assert offer_for(terms.plans, 2) is None
    assert grace_days_for(NS(grace_days=0), 30) == 0
    assert grace_days_for(NS(grace_days=None), 30) == 30  # null: the plan's
    assert grace_days_for(None, 15) == 15


def test_headcount_is_per_month_of_the_fees_due():
    book = Ledger(
        [
            enrollment(1, fee(date(2026, 10, 1)), fee(date(2026, 11, 1))),
            enrollment(2, fee(date(2026, 10, 20)), fee(date(2026, 11, 20))),
            enrollment(3, fee(date(2026, 11, 5))),
        ]
    )
    assert book.periods() == ["2026-10", "2026-11"]
    assert book.employees("2026-10") == 2
    assert book.employees("2026-11") == 3
    assert book.employees("2026-11", exclude_customer_id=3) == 2
    assert book.meets(3, "2026-11") and not book.meets(3, "2026-10")
    assert not Ledger([]).meets(0, "2026-10")  # a minimum of 0 still needs somebody
    # An employee with two enrollments in the month (e.g. a plan change) counts once.
    twice = Ledger([enrollment(1, fee(date(2026, 10, 1))), NS(id=9, customer_id=1, customer=NS(name="E1"), installments=[fee(date(2026, 10, 15))])])
    assert twice.employees("2026-10") == 1


def test_agreement_status_and_reference_month():
    terms = agreement()
    assert agreement_status(terms, date(2026, 9, 30)) == AGREEMENT_SCHEDULED
    assert agreement_status(terms, date(2026, 10, 22)) == AGREEMENT_IN_FORCE
    assert agreement_status(terms, date(2027, 4, 1)) == AGREEMENT_ENDED
    assert agreement_status(agreement(active=False), date(2026, 10, 22)) == AGREEMENT_SUSPENDED
    assert agreement_status(agreement(partner=NS(name="Acme", active=False)), date(2026, 10, 22)) == AGREEMENT_SUSPENDED
    assert reference_period(terms, date(2026, 9, 1)) == "2026-10"
    assert reference_period(terms, date(2028, 1, 1)) == "2027-03"
    assert reference_period(terms, date(2026, 12, 24)) == "2026-12"


def test_check_enrollment():
    today = date(2026, 10, 22)
    terms = agreement()
    assert check_enrollment(terms, PLAN, date(2026, 10, 1), date(2027, 3, 31), today) is terms.plans[0]
    refusals = [
        (terms, OTHER_PLAN, date(2026, 10, 1), date(2027, 3, 31), "not covered"),
        (terms, PLAN, date(2026, 9, 30), date(2027, 3, 31), "must fit"),
        (terms, PLAN, date(2026, 10, 1), date(2027, 4, 1), "must fit"),
        (agreement(active=False), PLAN, date(2026, 10, 1), date(2027, 3, 31), "suspended"),
        (agreement(partner=NS(name="Acme", active=False)), PLAN, date(2026, 10, 1), date(2027, 3, 31), "inactive"),
        (agreement(ends_on=date(2026, 10, 21)), PLAN, date(2026, 10, 1), date(2026, 10, 21), "ended"),
    ]
    for terms_, plan, joined, expires, message in refusals:
        with pytest.raises(EnrollmentError, match=message):
            check_enrollment(terms_, plan, joined, expires, today)
    # An enrollment already in a suspended agreement may still be edited (plan and period rules only).
    assert check_enrollment(agreement(active=False), PLAN, date(2026, 10, 1), date(2027, 2, 28), today, new=False)


def test_periods_and_due_dates():
    assert invoice_period(date(2026, 10, 31)) == "2026-10"
    assert parse_period("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    assert parse_period("2027-02") == (date(2027, 2, 1), date(2027, 2, 28))
    assert parse_period("2026-13") is None
    assert parse_period("october") is None
    assert shortfall_due_on("2026-10") == date(2026, 11, 10)
    assert shortfall_due_on("2026-12") == date(2027, 1, 10)


def lines(*fees):
    return [(NS(customer_id=index), item) for index, item in enumerate(fees, start=1)]


def test_invoice_with_the_minimum_met_has_no_shortfall():
    month = lines(fee(date(2026, 10, 1), paid_on=date(2026, 10, 5)), fee(date(2026, 10, 15)))
    totals = invoice_totals("2026-10", month, date(2026, 10, 16), 2, "company")
    assert (totals.installments, totals.employees, totals.below_minimum) == (2, 2, False)
    assert (totals.amount_cents, totals.discount_cents, totals.list_cents) == (15840, 3960, 19800)
    assert (totals.company_share_cents, totals.employee_share_cents) == (15840, 0)
    assert (totals.company_fees_paid_cents, totals.company_fees_open_cents, totals.shortfall_cents) == (7920, 7920, 0)
    assert totals.company_cents == 15840 and totals.company_open_cents == 7920
    assert totals.status(date(2026, 10, 16)) == INVOICE_OVERDUE  # the fee due on the 15th
    assert invoice_totals("2026-10", month, date(2026, 10, 14), 2, "company").status(date(2026, 10, 14)) == INVOICE_OPEN


def test_below_the_minimum_the_company_pays_the_discounts_once_the_month_ended():
    month = lines(fee(date(2026, 10, 1), paid_on=date(2026, 10, 2)))
    running = invoice_totals("2026-10", month, date(2026, 10, 20), 3, "company")
    assert running.below_minimum and running.shortfall_cents == 1980  # projected
    assert running.shortfall_open and not running.shortfall_payable  # the headcount may still change
    assert running.company_cents == 7920 + 1980 and running.company_open_cents == 1980
    assert running.status(date(2026, 10, 20)) == INVOICE_OPEN
    ended = invoice_totals("2026-10", month, date(2026, 11, 3), 3, "company")
    assert ended.shortfall_payable and ended.shortfall_due_on == date(2026, 11, 10)
    assert ended.status(date(2026, 11, 3)) == INVOICE_OPEN
    assert ended.status(date(2026, 11, 11)) == INVOICE_OVERDUE
    paid = invoice_totals("2026-10", month, date(2026, 11, 11), 3, "company", charge=NS(amount_cents=1900, paid_on=date(2026, 11, 4)))
    assert (paid.shortfall_cents, paid.shortfall_paid_on, paid.shortfall_open) == (1900, date(2026, 11, 4), False)  # frozen at payment
    assert paid.status(date(2026, 11, 11)) == INVOICE_PAID


def test_when_employees_pay_the_company_owes_only_the_shortfall():
    month = lines(fee(date(2026, 10, 1), company=0), fee(date(2026, 10, 5), company=0))
    met = invoice_totals("2026-10", month, date(2026, 11, 3), 2, "employee")
    assert met.company_cents == 0 and met.status(date(2026, 11, 3)) == INVOICE_NOTHING_DUE
    below = invoice_totals("2026-10", month, date(2026, 11, 3), 5, "employee")
    assert (below.company_cents, below.company_open_cents) == (3960, 3960)  # the open fees are the employees'
    assert below.status(date(2026, 11, 3)) == INVOICE_OPEN


# --- co-payment ---------------------------------------------------------------------------------


def test_split_of_a_fee_rounds_the_company_part_half_up():
    assert split_cents(7920, 60) == 4752
    assert split_cents(16915, 50) == 8458  # 8457.5 -> the company's part rounds up, the employee pays 8457
    assert split_cents(9900, 0) == 0 and split_cents(9900, 100) == 9900
    assert split_cents(9900, 150) == 9900  # clamped


def test_default_split_follows_the_payer_and_the_plan():
    company = agreement(payer="company", company_share_percent=100)
    employee = agreement(payer="employee", company_share_percent=0)
    shared = agreement(payer=PAYER_SHARED, company_share_percent=60)
    own = NS(modality_id=2, discount_percent=15, grace_days=None, company_share_percent=50)
    assert (contract_share(company), contract_share(employee), contract_share(shared)) == (100, 0, 60)
    assert default_share(shared, shared.plans[0]) == 60  # the plan sets none: the agreement's
    assert default_share(shared, own) == 50  # the plan's own split
    assert default_share(company, own) == 100  # a plan's split only counts in a co-payment
    assert payer_label(PAYER_SHARED, 60) == "co-paid: 60% by the company, 40% by the employee"


def test_the_split_in_force_on_a_day_follows_the_history():
    history = [
        NS(company_share_percent=60, effective_from=date(2026, 10, 1)),
        NS(company_share_percent=40, effective_from=date(2026, 12, 1)),
        NS(company_share_percent=80, effective_from=date(2027, 2, 15)),
    ]
    assert share_on([], date(2026, 10, 1)) is None
    assert share_on(history, date(2026, 9, 1)) == 60  # before any: the first
    assert share_on(history, date(2026, 11, 30)) == 60
    assert share_on(history, date(2026, 12, 1)) == 40
    assert share_on(reversed(history), date(2027, 3, 1)) == 80  # whatever the order


def test_a_co_payment_invoice_carries_the_company_part_only():
    month = lines(
        fee(date(2026, 10, 1), company=4752, company_paid_on=date(2026, 10, 3)),  # the employee's part is open
        fee(date(2026, 10, 5), company=6336),
    )
    totals = invoice_totals("2026-10", month, date(2026, 10, 4), 2, PAYER_SHARED)
    assert (totals.amount_cents, totals.company_share_cents, totals.employee_share_cents) == (15840, 11088, 4752)
    assert (totals.company_fees_paid_cents, totals.company_fees_open_cents, totals.company_paid_on) == (4752, 6336, date(2026, 10, 3))
    assert (totals.employee_paid_cents, totals.employee_open_cents) == (0, 3168 + 1584)
    assert (totals.company_cents, totals.company_open_cents) == (11088, 6336)
    assert totals.status(date(2026, 10, 4)) == INVOICE_OPEN
