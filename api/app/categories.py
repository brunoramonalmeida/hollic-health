"""Plan categories and enterprise plans.

Plans may be grouped in **categories** (``PlanCategory``: Individual,
Family, Enterprise…). A category may be **enterprise**: its plans — the
*enterprise plans* — are only available through **company agreements**
(app.partners). An enterprise plan is never:

* subscribed without an agreement — by the staff, a client on its own or an
  upgrade — nor kept after an enrollment leaves its agreement;
* offered in a sales region nor sold by a seller (nor asked in a seller's
  change request).

Enterprise plans carry their own special discounts and conditions like any
plan (the appointment discount and its per-specialty overrides, the
allowance, the dependents), and each agreement negotiates its fee discount,
split and waiting period on top.

**The waiting period.** A category may set the waiting period of all its
plans (``grace_days``): then it replaces each plan's own ``grace_days``.
The waiting period of a subscription is, from the most specific: the one
its corporate agreement negotiated for the plan, the category's, the plan's
(``plan_grace_days``; app.partners ``grace_days_for``).

A plan (or a whole category) cannot become enterprise while individual
customers still have it in force or a sales region offers it (``409``): the
sales already made are never stranded.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from .models import Modality, RegionPlan, Subscription


class EnterprisePlanError(Exception):
    """An enterprise plan used outside a company agreement."""

    status_code = 409

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def is_enterprise(modality) -> bool:
    category = getattr(modality, "category", None)
    return bool(category is not None and category.enterprise)


def plan_grace_days(modality) -> int:
    """The plan's waiting period: its category's when the category sets one,
    else its own."""
    category = getattr(modality, "category", None)
    if category is not None and category.grace_days is not None:
        return max(int(category.grace_days), 0)
    return max(int(modality.grace_days or 0), 0)


def enterprise_only(modality) -> str:
    return f'"{modality.name}" is an enterprise plan: it is only available through a company agreement'


def check_individual_use(modality, agreement_id: int | None) -> None:
    """An enterprise plan needs a company agreement."""
    if agreement_id is None and is_enterprise(modality):
        raise EnterprisePlanError(enterprise_only(modality))


# --- database -----------------------------------------------------------------------


@dataclass(frozen=True)
class IndividualUse:
    """What keeps plans from becoming enterprise plans."""

    subscriptions: int  # individual subscriptions (no agreement) not cancelled and not ended
    regions: int  # sales regions offering them

    @property
    def any(self) -> bool:
        return bool(self.subscriptions or self.regions)

    def message(self, what: str) -> str:
        parts = []
        if self.subscriptions:
            parts.append(f"{self.subscriptions} individual subscription(s) in force")
        if self.regions:
            parts.append(f"{self.regions} sales region(s) offering it")
        return (
            f"{what} cannot become enterprise (only through company agreements): "
            f"{' and '.join(parts)}. Move them to another plan first, or let them end."
        )


def individual_use(db: Session, modality_ids: list[int], today: date) -> IndividualUse:
    if not modality_ids:
        return IndividualUse(0, 0)
    subscriptions = (
        db.query(Subscription)
        .filter(
            Subscription.modality_id.in_(modality_ids),
            Subscription.agreement_id.is_(None),
            Subscription.cancelled_on.is_(None),
            Subscription.expires_on >= today,
        )
        .count()
    )
    regions = (
        db.query(RegionPlan.region_id).filter(RegionPlan.modality_id.in_(modality_ids)).distinct().count()
    )
    return IndividualUse(subscriptions, regions)


def category_plan_ids(db: Session, category_id: int) -> list[int]:
    return [row.id for row in db.query(Modality.id).filter(Modality.category_id == category_id).all()]
