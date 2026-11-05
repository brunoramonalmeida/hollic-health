"""Unit tests of the sales rules (app.sales): scope matching, commission
rounding and the checks of a seller's sale. No database."""

from types import SimpleNamespace as NS

import pytest

from app.billing import list_amount, monthly_amount
from app.sales import (
    SaleError,
    area_label,
    check_sale,
    commission_cents,
    format_bp,
    normalize_place,
    scope_covers,
    scope_label,
)

SP = NS(state="SP", city="")
CAMPINAS = NS(state="SP", city="Campinas")
NITEROI = NS(state="RJ", city="Niterói")


def make_seller(*, active=True, areas=(SP,), plans=((1, 10),), commission_bp=1250):
    region = NS(
        id=7,
        name="Interior",
        commission_bp=commission_bp,
        areas=list(areas),
        plans=[NS(modality_id=modality_id, discount_percent=percent) for modality_id, percent in plans],
    )
    return NS(id=3, name="Ana Seller", active=active, region=region)


PLAN = NS(id=1, name="Plan Foo")
OTHER_PLAN = NS(id=2, name="Plan Bar")


def customer(city="Campinas", state="SP"):
    return NS(name="Foo Holder", city=city, state=state)


def test_normalize_place_ignores_accents_case_and_spaces():
    assert normalize_place("  São   Paulo ") == normalize_place("sao paulo")
    assert normalize_place("NITERÓI") == "niteroi"
    assert normalize_place(None) == ""


def test_whole_state_area_covers_every_city_of_the_state():
    assert scope_covers([SP], "SP", "Campinas")
    assert scope_covers([SP], "sp", "Anywhere")
    assert not scope_covers([SP], "RJ", "Rio de Janeiro")


def test_city_area_covers_only_that_city():
    assert scope_covers([NITEROI], "RJ", "niteroi")
    assert not scope_covers([NITEROI], "RJ", "Rio de Janeiro")
    # Same city name in another state is another municipality.
    assert not scope_covers([CAMPINAS], "MG", "Campinas")


def test_a_place_without_state_is_never_covered():
    assert not scope_covers([SP], "", "Campinas")
    assert not scope_covers([], "SP", "Campinas")


def test_labels():
    assert area_label(SP) == "SP"
    assert area_label(NITEROI) == "Niterói/RJ"
    assert scope_label([SP, NITEROI]) == "SP, Niterói/RJ"
    assert scope_label([]) == "no area"
    assert format_bp(1250) == "12.5%"
    assert format_bp(1000) == "10%"
    assert format_bp(0) == "0%"


def test_commission_is_rounded_half_up_and_bounded():
    assert commission_cents(8910, 1000) == 891
    assert commission_cents(9999, 1250) == 1250  # 1249.875 -> 1250
    assert commission_cents(1, 5000) == 1  # 0.5 -> 1
    assert commission_cents(1000, 0) == 0
    assert commission_cents(1000, 20000) == 1000  # capped at 100 %
    assert commission_cents(-500, 1000) == 0


def test_valid_sale_returns_the_terms_of_the_region():
    terms = check_sale(make_seller(), PLAN, customer())
    assert terms.seller_id == 3
    assert terms.region_id == 7
    assert terms.commission_bp == 1250
    assert terms.discount_percent == 10


def test_plan_not_offered_in_the_region_is_refused():
    with pytest.raises(SaleError, match='Plan "Plan Bar" is not sold in region Interior'):
        check_sale(make_seller(), OTHER_PLAN, customer())


def test_customer_outside_the_scope_is_refused():
    with pytest.raises(SaleError, match=r"Rio de Janeiro/RJ\) is outside region Interior.*scope: SP"):
        check_sale(make_seller(), PLAN, customer("Rio de Janeiro", "RJ"))


def test_customer_without_address_is_refused():
    with pytest.raises(SaleError, match="no city/state"):
        check_sale(make_seller(), PLAN, customer("", ""))
    with pytest.raises(SaleError, match="no city/state"):
        check_sale(make_seller(), PLAN, customer("", "SP"))


def test_inactive_seller_is_refused():
    with pytest.raises(SaleError, match="inactive"):
        check_sale(make_seller(active=False), PLAN, customer())


def test_special_discount_applies_to_the_whole_monthly_fee():
    plan = NS(plan_value_cents=9900, dependent_limit=2, extra_dependent_cents=1500)
    assert list_amount(plan, 3) == 11400
    assert monthly_amount(plan, 3) == 11400
    assert monthly_amount(plan, 3, 10) == 10260
    assert monthly_amount(plan, 0, 15) == 9900 - 1485
    assert monthly_amount(plan, 0, 100) == 0
