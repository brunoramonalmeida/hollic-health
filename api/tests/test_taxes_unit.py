"""Unit tests for the pure tax engine (app.taxes)."""

from dataclasses import dataclass
from datetime import date

from app.taxes import (
    ServiceContext,
    compute_taxes,
    format_rate,
    resolve_rules,
    rule_matches,
    specificity,
    tax_amount,
    total,
)


@dataclass
class Rule:
    id: int
    code: str
    rate_bp: int
    level: str = "municipal"
    name: str = ""
    applies_to: str = "all"
    base: str = "amount"
    state: str | None = None
    city: str | None = None
    clinic_id: int | None = None
    specialty_id: int | None = None
    modality_id: int | None = None
    effective_from: date = date(2026, 1, 1)
    effective_to: date | None = None
    active: bool = True


CTX = ServiceContext(kind="appointment", on=date(2026, 10, 26), state="SP", city="Sample", ibge_code="3500000", clinic_id=1, specialty_id=1, modality_id=1)


def test_tax_amount_rounds_half_up_on_cents():
    assert tax_amount(10000, 500) == 500  # 5 %
    assert tax_amount(10000, 65) == 65  # 0.65 %
    assert tax_amount(7500, 65) == 49  # 48.75 -> 49
    assert tax_amount(1, 5000) == 1  # 0.5 -> 1
    assert tax_amount(0, 500) == 0


def test_format_rate():
    assert format_rate(500) == "5%"
    assert format_rate(65) == "0.65%"
    assert format_rate(250) == "2.5%"


def test_rule_matches_scope_filters():
    assert rule_matches(Rule(1, "ISS", 500), CTX)
    assert rule_matches(Rule(1, "ISS", 500, state="sp"), CTX)  # case-insensitive
    assert not rule_matches(Rule(1, "ISS", 500, state="RJ"), CTX)
    assert rule_matches(Rule(1, "ISS", 500, city="sample"), CTX)
    assert not rule_matches(Rule(1, "ISS", 500, city="Other"), CTX)
    assert not rule_matches(Rule(1, "ISS", 500, clinic_id=2), CTX)
    assert not rule_matches(Rule(1, "ISS", 500, specialty_id=9), CTX)
    assert not rule_matches(Rule(1, "ISS", 500, modality_id=9), CTX)
    assert rule_matches(Rule(1, "ISS", 500, clinic_id=1, specialty_id=1, modality_id=1), CTX)


def test_rule_matches_kind_dates_and_active():
    assert not rule_matches(Rule(1, "ISS", 500, applies_to="subscription"), CTX)
    assert rule_matches(Rule(1, "ISS", 500, applies_to="appointment"), CTX)
    assert not rule_matches(Rule(1, "ISS", 500, effective_from=date(2026, 11, 1)), CTX)
    assert not rule_matches(Rule(1, "ISS", 500, effective_to=date(2026, 10, 25)), CTX)
    assert rule_matches(Rule(1, "ISS", 500, effective_to=date(2026, 10, 26)), CTX)  # inclusive
    assert not rule_matches(Rule(1, "ISS", 500, active=False), CTX)


def test_specificity_counts_filters():
    assert specificity(Rule(1, "ISS", 500)) == 0
    assert specificity(Rule(1, "ISS", 500, applies_to="appointment")) == 1
    assert specificity(Rule(1, "ISS", 500, state="SP", city="Sample")) == 2
    assert specificity(Rule(1, "ISS", 500, state="SP", city="Sample", clinic_id=1, specialty_id=1, modality_id=1)) == 5


def test_most_specific_rule_wins_per_code():
    rules = [
        Rule(1, "ISS", 500),  # default 5 %
        Rule(2, "ISS", 300, city="Sample"),  # this city 3 %
        Rule(3, "ISS", 200, city="Sample", specialty_id=1),  # this city and specialty 2 %
        Rule(4, "ISS", 100, city="Other"),
        Rule(5, "PIS", 65, level="federal"),
        Rule(6, "COFINS", 300, level="federal"),
    ]
    chosen = resolve_rules(rules, CTX)
    assert [(r.code, r.rate_bp) for r in chosen] == [("COFINS", 300), ("PIS", 65), ("ISS", 200)]  # federal first
    other = ServiceContext(kind="appointment", on=CTX.on, state="SP", city="Other")
    assert {r.code: r.rate_bp for r in resolve_rules(rules, other)}["ISS"] == 100
    elsewhere = ServiceContext(kind="appointment", on=CTX.on, state="RJ", city="Rio")
    assert {r.code: r.rate_bp for r in resolve_rules(rules, elsewhere)}["ISS"] == 500


def test_newest_rule_wins_on_equal_specificity():
    rules = [
        Rule(1, "ISS", 500, effective_from=date(2025, 1, 1)),
        Rule(2, "ISS", 400, effective_from=date(2026, 6, 1)),  # rate change mid-year
    ]
    assert resolve_rules(rules, CTX)[0].rate_bp == 400
    earlier = ServiceContext(kind="appointment", on=date(2026, 3, 1), state="SP", city="Sample")
    assert resolve_rules(rules, earlier)[0].rate_bp == 500


def test_compute_taxes_lines_bases_and_total():
    rules = [
        Rule(1, "ISS", 500, name="ISS"),
        Rule(2, "PIS", 65, level="federal", name="PIS", base="gross"),
    ]
    lines = compute_taxes(rules, CTX, amount_cents=7500, gross_cents=15000)
    by_code = {line.code: line for line in lines}
    assert by_code["ISS"].base_cents == 7500 and by_code["ISS"].amount_cents == 375
    assert by_code["PIS"].base_cents == 15000 and by_code["PIS"].amount_cents == 98  # 97.5 -> 98
    assert by_code["ISS"].state == "SP" and by_code["ISS"].city == "Sample" and by_code["ISS"].ibge_code == "3500000"
    assert by_code["ISS"].rule_id == 1
    assert total(lines) == 473


def test_compute_taxes_without_rules_is_empty():
    assert compute_taxes([], CTX, amount_cents=1000) == []
    assert total([]) == 0
