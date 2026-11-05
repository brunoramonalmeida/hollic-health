"""Tax rules over services.

A service (an appointment, or a plan installment being paid) happens in a
*context*: kind, date, state, city, clinic, specialty, plan. Each ``TaxRule``
carries a tax code (ISS, PIS, COFINS...), a level (federal, state,
municipal), a rate in basis points and optional scope filters. For every
code, the most specific active rule whose filters all match the context is
the one applied; ties go to the most recent ``effective_from``. Rates are
applied to the billed amount (default) or to the gross price, rounded half
up to the cent. Lines are snapshotted on the service so later changes to the
rules never alter history.
"""

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol

LEVELS = ("federal", "state", "municipal")
APPLIES_TO = ("appointment", "subscription", "all")
BASES = ("amount", "gross")
KIND_APPOINTMENT = "appointment"
KIND_SUBSCRIPTION = "subscription"


class RuleLike(Protocol):
    id: int | None
    code: str
    name: str
    level: str
    rate_bp: int
    applies_to: str
    base: str
    state: str | None
    city: str | None
    clinic_id: int | None
    specialty_id: int | None
    modality_id: int | None
    effective_from: date
    effective_to: date | None
    active: bool


@dataclass(frozen=True)
class ServiceContext:
    kind: str  # appointment | subscription
    on: date
    state: str = ""
    city: str = ""
    ibge_code: str = ""
    clinic_id: int | None = None
    specialty_id: int | None = None
    modality_id: int | None = None


@dataclass(frozen=True)
class TaxLine:
    rule_id: int | None
    code: str
    name: str
    level: str
    rate_bp: int
    base_cents: int
    amount_cents: int
    state: str
    city: str
    ibge_code: str


def norm(text: str | None) -> str:
    return (text or "").strip().casefold()


def rule_matches(rule: RuleLike, ctx: ServiceContext) -> bool:
    if not rule.active:
        return False
    if rule.applies_to not in ("all", ctx.kind):
        return False
    if ctx.on < rule.effective_from or (rule.effective_to is not None and ctx.on > rule.effective_to):
        return False
    if rule.state and norm(rule.state) != norm(ctx.state):
        return False
    if rule.city and norm(rule.city) != norm(ctx.city):
        return False
    if rule.clinic_id is not None and rule.clinic_id != ctx.clinic_id:
        return False
    if rule.specialty_id is not None and rule.specialty_id != ctx.specialty_id:
        return False
    if rule.modality_id is not None and rule.modality_id != ctx.modality_id:
        return False
    return True


def specificity(rule: RuleLike) -> int:
    """How many scope filters the rule sets; higher wins."""
    return sum(
        1
        for value in (rule.state, rule.city, rule.clinic_id, rule.specialty_id, rule.modality_id)
        if value not in (None, "")
    ) + (0 if rule.applies_to == "all" else 1)


def resolve_rules(rules: Iterable[RuleLike], ctx: ServiceContext) -> list[RuleLike]:
    """One rule per tax code: the most specific match, newest on ties."""
    chosen: dict[str, RuleLike] = {}
    for rule in rules:
        if not rule_matches(rule, ctx):
            continue
        current = chosen.get(rule.code)
        if current is None or (specificity(rule), rule.effective_from) > (specificity(current), current.effective_from):
            chosen[rule.code] = rule
    return sorted(chosen.values(), key=lambda rule: (LEVELS.index(rule.level) if rule.level in LEVELS else 9, rule.code))


def tax_amount(base_cents: int, rate_bp: int) -> int:
    """Basis points over cents, rounded half up."""
    return (base_cents * rate_bp + 5000) // 10000


def compute_taxes(
    rules: Iterable[RuleLike], ctx: ServiceContext, *, amount_cents: int, gross_cents: int | None = None
) -> list[TaxLine]:
    gross_cents = amount_cents if gross_cents is None else gross_cents
    lines = []
    for rule in resolve_rules(rules, ctx):
        base = gross_cents if rule.base == "gross" else amount_cents
        lines.append(
            TaxLine(
                rule_id=rule.id,
                code=rule.code,
                name=rule.name,
                level=rule.level,
                rate_bp=rule.rate_bp,
                base_cents=base,
                amount_cents=tax_amount(base, rule.rate_bp),
                state=ctx.state or "",
                city=ctx.city or "",
                ibge_code=ctx.ibge_code or "",
            )
        )
    return lines


def total(lines: Iterable[TaxLine]) -> int:
    return sum(line.amount_cents for line in lines)


def format_rate(rate_bp: int) -> str:
    value = rate_bp / 100
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"
