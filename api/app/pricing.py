"""Appointment pricing with plan discounts.

The gross price of an appointment is the price list at scheduling time:
``clinic fee + specialty price + professional fee``. The customer's plan
grants a discount percentage on that gross amount; the plan has a default
percentage and may override it per specialty. Everything is snapshotted on
the appointment, so later changes to prices or discounts never alter a
booked appointment.

**On-call services.** An appointment in an on-call shift of the professional
uses the on-call price list instead (``price_components``): the
professional's on-call rate (always above its regular fee), the clinic's
on-call fee and the specialty's on-call price (each optional: unset means the
regular value). The plan discount applies to that gross as usual, and the
professional's payout is the on-call rate snapshotted on the appointment.

**Urgent visits.** An ad-hoc urgent visit (``urgent``) pays the
professional's urgent rate (``urgent_price_cents``, at least
``URGENT_RATE_MULTIPLIER`` times the regular fee) in place of the fee; the
clinic fee and the specialty price are the regular ones. Urgent visits are
never on-call services (they are refused inside an on-call shift), so the
two price lists never combine.

**Remote appointments.** A remote appointment (video call, ``remote``) uses
the remote price list: the professional's remote rate, the clinic's remote
fee and the specialty's remote price — each optional, unset means the
regular value (a clinic may charge nothing, since no room is used). Remote
appointments are booked in the regular working hours only (refused inside
an on-call shift, and never urgent), so again the price lists never combine.
"""

from dataclasses import dataclass
from typing import Iterable, Protocol


class DiscountLike(Protocol):
    specialty_id: int
    percent: int


class ModalityLike(Protocol):
    discount_percent: int


URGENT_RATE_MULTIPLIER = 3


class ProfessionalPricesLike(Protocol):
    price_cents: int
    on_call_price_cents: int
    urgent_price_cents: int
    remote_price_cents: int | None


class ClinicPricesLike(Protocol):
    fee_cents: int
    on_call_fee_cents: int | None
    remote_fee_cents: int | None


class SpecialtyPricesLike(Protocol):
    price_cents: int
    on_call_price_cents: int | None
    remote_price_cents: int | None


@dataclass(frozen=True)
class Price:
    fee_cents: int
    specialty_price_cents: int
    professional_price_cents: int
    gross_cents: int
    discount_percent: int
    discount_cents: int
    amount_cents: int


def value_or(optional_cents: int | None, regular_cents: int) -> int:
    """An optional value of an alternative price list (on-call, remote)
    falls back to the regular one."""
    return regular_cents if optional_cents is None else int(optional_cents)


def on_call_value(on_call_cents: int | None, regular_cents: int) -> int:
    """An optional on-call value falls back to the regular one."""
    return value_or(on_call_cents, regular_cents)


def remote_value(remote_cents: int | None, regular_cents: int) -> int:
    """An optional remote value falls back to the regular one."""
    return value_or(remote_cents, regular_cents)


def urgent_minimum(price_cents: int) -> int:
    """The lowest urgent rate allowed for a regular fee."""
    return URGENT_RATE_MULTIPLIER * max(int(price_cents), 0)


def price_components(
    clinic: ClinicPricesLike,
    specialty: SpecialtyPricesLike,
    professional: ProfessionalPricesLike,
    on_call: bool,
    urgent: bool = False,
    remote: bool = False,
) -> tuple[int, int, int]:
    """(clinic fee, specialty price, professional fee) of the price list in
    force: the regular one, the on-call one for an on-call service, the
    regular fees with the professional's urgent rate for an urgent visit,
    or the remote one for a remote appointment."""
    if urgent:
        return clinic.fee_cents, specialty.price_cents, professional.urgent_price_cents
    if remote:
        return (
            remote_value(getattr(clinic, "remote_fee_cents", None), clinic.fee_cents),
            remote_value(getattr(specialty, "remote_price_cents", None), specialty.price_cents),
            remote_value(getattr(professional, "remote_price_cents", None), professional.price_cents),
        )
    if not on_call:
        return clinic.fee_cents, specialty.price_cents, professional.price_cents
    return (
        on_call_value(clinic.on_call_fee_cents, clinic.fee_cents),
        on_call_value(specialty.on_call_price_cents, specialty.price_cents),
        professional.on_call_price_cents,
    )


def resolve_discount(
    modality: ModalityLike | None, discounts: Iterable[DiscountLike], specialty_id: int
) -> int:
    """Percentage granted by the plan for the specialty (0 without a plan)."""
    if modality is None:
        return 0
    for item in discounts:
        if item.specialty_id == specialty_id:
            return clamp_percent(item.percent)
    return clamp_percent(modality.discount_percent)


def clamp_percent(value: int) -> int:
    return max(0, min(100, int(value)))


def discount_amount(gross_cents: int, percent: int) -> int:
    """Rounded half up to the cent, as on a receipt."""
    return (gross_cents * clamp_percent(percent) + 50) // 100


def price_appointment(
    fee_cents: int, specialty_price_cents: int, professional_price_cents: int, percent: int
) -> Price:
    gross = fee_cents + specialty_price_cents + professional_price_cents
    discount = discount_amount(gross, percent)
    return Price(
        fee_cents=fee_cents,
        specialty_price_cents=specialty_price_cents,
        professional_price_cents=professional_price_cents,
        gross_cents=gross,
        discount_percent=clamp_percent(percent),
        discount_cents=discount,
        amount_cents=gross - discount,
    )
