"""Sellers, sales regions and the rules of a plan sale.

Every **seller** belongs to exactly one **region**. The region carries the
rules its sellers sell under:

* ``commission_bp`` — the sellers' commission, in basis points
  (1 bp = 0.01 %), on the plan fees actually received from their sales
  (each paid installment: ``paid_cents``), rounded half up per installment;
* **scope** (``areas``) — where they may sell: whole states or single
  municipalities. The customer's address (state and city of the holder's
  record) must fall inside it; cities are compared ignoring case, accents
  and extra spaces;
* **plans available** (``plans``) — the only plans they may sell, each with
  the region's **special discount** on the monthly fee (``discount_percent``;
  0 means the list price).

A sale outside the region — a plan the region does not offer, a customer
whose address is outside the scope or has no state/city to check, or an
inactive seller — is refused (``SaleError``, HTTP 409). The terms of a valid
sale (region, commission, discount) are **snapshotted** on the subscription,
so later changes to the region or moving the seller to another region never
rewrite an existing sale. They are re-checked and re-snapshotted only when the
sale itself changes (other seller, plan or customer).

**Teams.** A seller may report to a **leader** (a coordinator of the same
region), who may report to a regional manager, and so on — any depth. Each
ancestor earns the **override** the region pays for its level above the
seller (``Region.overrides``: level 1 the direct leader, level 2 the
leader's leader…) on every fee received from the sale, on top of the
seller's commission — paid by the company, never taken from the seller.
Like the commission, the ancestors and their rates are snapshotted on the
sale (``SaleOverride`` rows): moving a seller to another team or changing
the region's rates later never rewrites what a sale pays. An inactive
ancestor, or a level the region pays nothing for, gets no row.

Subscriptions without a seller (sold at the counter or by the client) are
not bound by any region.
"""

import unicodedata
from dataclasses import dataclass
from typing import Iterable, Protocol

from .pricing import clamp_percent


class SaleError(Exception):
    """The seller may not make this sale."""

    status_code = 409

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AreaLike(Protocol):
    state: str
    city: str


class RegionPlanLike(Protocol):
    modality_id: int
    discount_percent: int


@dataclass(frozen=True)
class Override:
    """An ancestor of the selling seller and its rate on the sale."""

    level: int  # 1: direct leader, 2: the leader's leader...
    seller_id: int
    rate_bp: int


@dataclass(frozen=True)
class SaleTerms:
    """What is snapshotted on the subscription."""

    seller_id: int | None
    region_id: int | None
    commission_bp: int
    discount_percent: int
    overrides: tuple[Override, ...] = ()


NO_SELLER = SaleTerms(seller_id=None, region_id=None, commission_bp=0, discount_percent=0)
MAX_COMMISSION_BP = 10_000  # 100 %
MAX_OVERRIDE_LEVELS = 5


def ancestors(seller) -> list:
    """The seller's leader, the leader's leader... (stops at a cycle)."""
    chain, seen = [], {seller.id}
    current = getattr(seller, "leader", None)
    while current is not None and current.id not in seen:
        chain.append(current)
        seen.add(current.id)
        current = getattr(current, "leader", None)
    return chain


def overrides_for(seller, region) -> tuple[Override, ...]:
    """The override rows a sale of ``seller`` gets: one per active ancestor
    whose level the region pays for."""
    rates = {item.level: int(item.rate_bp or 0) for item in getattr(region, "overrides", ())}
    rows = []
    for level, ancestor in enumerate(ancestors(seller), start=1):
        rate = rates.get(level, 0)
        if rate > 0 and bool(ancestor.active):
            rows.append(Override(level=level, seller_id=ancestor.id, rate_bp=rate))
    return tuple(rows)


# --- places -------------------------------------------------------------------


def normalize_place(value: str | None) -> str:
    """Comparable form of a city name: no accents, case or repeated spaces."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().split())


def normalize_state(value: str | None) -> str:
    return (value or "").strip().upper()


def area_label(area: AreaLike) -> str:
    """``SP`` (whole state) or ``Campinas/SP``."""
    return f"{area.city}/{area.state}" if area.city else area.state


def scope_label(areas: Iterable[AreaLike]) -> str:
    labels = [area_label(area) for area in areas]
    return ", ".join(labels) if labels else "no area"


def area_covers(area: AreaLike, state: str | None, city: str | None) -> bool:
    if normalize_state(area.state) != normalize_state(state):
        return False
    if not area.city:
        return True  # the whole state
    return normalize_place(area.city) == normalize_place(city)


def scope_covers(areas: Iterable[AreaLike], state: str | None, city: str | None) -> bool:
    """Is the place inside any area of the scope? A place without a state is
    never inside (it cannot be checked)."""
    if not normalize_state(state):
        return False
    return any(area_covers(area, state, city) for area in areas)


# --- money --------------------------------------------------------------------


def commission_cents(base_cents: int, commission_bp: int) -> int:
    """Commission on an amount received, rounded half up to the cent."""
    bp = max(0, min(MAX_COMMISSION_BP, int(commission_bp)))
    return (max(int(base_cents), 0) * bp + 5_000) // 10_000


def format_bp(value: int) -> str:
    """1250 -> '12.5%'."""
    return f"{value / 100:.2f}".rstrip("0").rstrip(".") + "%"


# --- the rule -----------------------------------------------------------------


def region_plan(plans: Iterable[RegionPlanLike], modality_id: int) -> RegionPlanLike | None:
    for item in plans:
        if item.modality_id == modality_id:
            return item
    return None


def check_sale(seller, modality, customer) -> SaleTerms:
    """Terms of the sale of ``modality`` to ``customer`` by ``seller``, or
    ``SaleError`` when it falls outside the seller's region.

    ``seller.region`` must carry its ``areas`` and ``plans``; ``customer`` is
    the holder's record (its ``state``/``city`` is the place of the sale)."""
    region = seller.region
    if not seller.active:
        raise SaleError(f"Seller {seller.name} is inactive and cannot sell")
    category = getattr(modality, "category", None)
    if category is not None and category.enterprise:
        raise SaleError(f'"{modality.name}" is an enterprise plan: it is only available through a company agreement, never sold by a seller')
    offer = region_plan(region.plans, modality.id)
    if offer is None:
        raise SaleError(f'Plan "{modality.name}" is not sold in region {region.name} (seller {seller.name})')
    state, city = normalize_state(customer.state), (customer.city or "").strip()
    if not state or not city:
        raise SaleError(
            f"Customer {customer.name} has no city/state in the address: "
            f"it is needed to check region {region.name}"
        )
    if not scope_covers(region.areas, state, city):
        raise SaleError(
            f"Customer {customer.name} ({city}/{state}) is outside region {region.name} "
            f"of seller {seller.name} (scope: {scope_label(region.areas)})"
        )
    return SaleTerms(
        seller_id=seller.id,
        region_id=region.id,
        commission_bp=int(region.commission_bp or 0),
        discount_percent=clamp_percent(offer.discount_percent),
        overrides=overrides_for(seller, region),
    )
