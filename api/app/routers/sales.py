"""Sales regions and sellers.

The staff reads them (the reception records a seller's sale on the
subscription); only administrators and supervisors write them, because a
region's commission and special discounts move money, like the price list.
A seller login sees only itself and the rules of its own region
(``/sellers/me``) and edits its own contact data. The rules a sale must
follow live in ``app.sales``.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from ..auth import get_current_user, require_pricing, require_seller, require_staff
from ..billing import monthly_amount
from ..database import get_db
from ..models import Customer, Modality, Region, RegionArea, RegionOverride, RegionPlan, Seller, Subscription, User
from ..notifications import region_rules_changed, seller_leader_changed, seller_moved, seller_status_changed
from ..categories import enterprise_only, is_enterprise
from ..sales import MAX_OVERRIDE_LEVELS, ancestors, area_label, format_bp, scope_label
from ..schemas import (
    RegionAreaOut,
    RegionIn,
    RegionOut,
    RegionPlanOut,
    SellerIn,
    SellerMeOut,
    SellerOut,
    SellerProfileIn,
)

WRITE = [Depends(require_pricing)]

regions = APIRouter(prefix="/regions", tags=["sales"], dependencies=[Depends(require_staff)])
sellers = APIRouter(prefix="/sellers", tags=["sales"], dependencies=[Depends(require_staff)])
# The seller's own area. Registered before ``sellers`` so ``/sellers/me`` is
# not taken for ``/sellers/{item_id}`` (which sellers may not open).
me = APIRouter(prefix="/sellers/me", tags=["sales"], dependencies=[Depends(require_seller)])


# --- regions ------------------------------------------------------------------


def _region_query(db: Session):
    return db.query(Region).options(
        selectinload(Region.areas),
        selectinload(Region.plans).joinedload(RegionPlan.modality),
        selectinload(Region.overrides),
    )


def _load_region(db: Session, item_id: int) -> Region:
    row = _region_query(db).filter(Region.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Region not found")
    return row


def _seller_counts(db: Session) -> dict[int, tuple[int, int]]:
    """region id -> (sellers, active sellers)."""
    rows = (
        db.query(Seller.region_id, Seller.active, func.count(Seller.id))
        .group_by(Seller.region_id, Seller.active)
        .all()
    )
    counts: dict[int, tuple[int, int]] = {}
    for region_id, active, count in rows:
        total, actives = counts.get(region_id, (0, 0))
        counts[region_id] = (total + count, actives + (count if active else 0))
    return counts


def region_out(row: Region, counts: dict[int, tuple[int, int]] | None = None) -> RegionOut:
    sellers_count, active_count = (counts or {}).get(row.id, (0, 0))
    plans = sorted(row.plans, key=lambda item: item.modality.name.lower())
    return RegionOut(
        id=row.id,
        name=row.name,
        description=row.description or "",
        commission_bp=row.commission_bp or 0,
        commission_label=format_bp(row.commission_bp or 0),
        areas=[RegionAreaOut(state=item.state, city=item.city or "", label=area_label(item)) for item in row.areas],
        scope=scope_label(row.areas),
        plans=[
            RegionPlanOut(
                modality_id=item.modality_id,
                discount_percent=item.discount_percent,
                modality_name=item.modality.name,
                plan_value_cents=item.modality.plan_value_cents,
                value_cents=monthly_amount(item.modality, 0, item.discount_percent),
            )
            for item in plans
        ],
        override_bps=_override_bps(row),
        override_labels=[format_bp(rate) for rate in _override_bps(row)],
        sellers_count=sellers_count,
        active_sellers_count=active_count,
    )


def _override_bps(row: Region) -> list[int]:
    """The region's override rates as a list by level (1-based levels -> index 0...)."""
    rates = {item.level: item.rate_bp or 0 for item in row.overrides}
    depth = max(rates, default=0)
    return [rates.get(level, 0) for level in range(1, depth + 1)]


def _rules(row: Region) -> tuple:
    """What the sellers care about, to tell them only when it really changed."""
    return (
        row.commission_bp or 0,
        tuple(sorted((item.state, item.city or "") for item in row.areas)),
        tuple(sorted((item.modality_id, item.discount_percent) for item in row.plans)),
        tuple(_override_bps(row)),
    )


def _apply_region(db: Session, row: Region, body: RegionIn) -> None:
    for item in body.plans:
        plan = db.get(Modality, item.modality_id)
        if plan is None:
            raise HTTPException(status_code=404, detail=f"Plan {item.modality_id} not found")
        if is_enterprise(plan):
            raise HTTPException(status_code=409, detail=f"{enterprise_only(plan)}: sellers never sell it")
    row.name = body.name
    row.description = body.description
    row.commission_bp = body.commission_bp
    # Replace the scope, the plans and the overrides: delete first so the unique keys are free.
    row.areas.clear()
    row.plans.clear()
    row.overrides.clear()
    db.flush()
    row.areas.extend(RegionArea(state=item.state, city=item.city) for item in body.areas)
    row.plans.extend(
        RegionPlan(modality_id=item.modality_id, discount_percent=item.discount_percent) for item in body.plans
    )
    row.overrides.extend(
        RegionOverride(level=level, rate_bp=rate) for level, rate in enumerate(body.override_bps[:MAX_OVERRIDE_LEVELS], start=1)
    )


def _commit_region(db: Session, item_id: int) -> RegionOut:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Region name already exists")
    db.expire_all()
    return region_out(_load_region(db, item_id), _seller_counts(db))


@regions.get("", response_model=list[RegionOut])
def list_regions(db: Session = Depends(get_db)):
    counts = _seller_counts(db)
    return [region_out(row, counts) for row in _region_query(db).order_by(Region.name).all()]


@regions.post("", response_model=RegionOut, status_code=201, dependencies=WRITE)
def create_region(body: RegionIn, db: Session = Depends(get_db)):
    if db.query(Region).filter(func.lower(Region.name) == body.name.lower()).first() is not None:
        raise HTTPException(status_code=409, detail="Region name already exists")
    row = Region(name=body.name)
    db.add(row)
    db.flush()
    _apply_region(db, row, body)
    return _commit_region(db, row.id)


@regions.get("/{item_id}", response_model=RegionOut)
def get_region(item_id: int, db: Session = Depends(get_db)):
    return region_out(_load_region(db, item_id), _seller_counts(db))


@regions.put("/{item_id}", response_model=RegionOut, dependencies=WRITE)
def update_region(item_id: int, body: RegionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Rule changes apply to the next sales; existing subscriptions keep the
    commission and discount snapshotted when they were sold. The sellers of
    the region are notified when its rules change."""
    row = _load_region(db, item_id)
    other = db.query(Region).filter(func.lower(Region.name) == body.name.lower(), Region.id != item_id).first()
    if other is not None:
        raise HTTPException(status_code=409, detail="Region name already exists")
    before = _rules(row)
    _apply_region(db, row, body)
    db.flush()
    if _rules(_load_region(db, item_id)) != before:
        region_rules_changed(db, _load_region(db, item_id), user)
    return _commit_region(db, item_id)


@regions.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_region(item_id: int, db: Session = Depends(get_db)):
    row = _load_region(db, item_id)
    if db.query(Seller).filter(Seller.region_id == item_id).count():
        raise HTTPException(status_code=409, detail="Region has sellers: move them to another region first")
    if db.query(Subscription).filter(Subscription.region_id == item_id).count():
        raise HTTPException(status_code=409, detail="Region has sales recorded and cannot be deleted")
    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record is in use")


# --- sellers ------------------------------------------------------------------


def _sales_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(Subscription.seller_id, func.count(Subscription.id))
        .filter(Subscription.seller_id.is_not(None))
        .group_by(Subscription.seller_id)
        .all()
    )
    return dict(rows)


class Teams:
    """The whole hierarchy, to answer team sizes and depths in one query."""

    def __init__(self, db: Session):
        self.leader_of = {seller_id: leader_id for seller_id, leader_id in db.query(Seller.id, Seller.leader_id).all()}
        self.reports: dict[int, list[int]] = {}
        for seller_id, leader_id in self.leader_of.items():
            if leader_id is not None:
                self.reports.setdefault(leader_id, []).append(seller_id)

    def team(self, seller_id: int) -> list[int]:
        return self.reports.get(seller_id, [])

    def organisation(self, seller_id: int) -> list[tuple[int, int]]:
        """Every seller below, as (id, depth below ``seller_id``), breadth first."""
        result, queue, seen = [], [(child, 1) for child in self.team(seller_id)], {seller_id}
        while queue:
            child, depth = queue.pop(0)
            if child in seen:
                continue
            seen.add(child)
            result.append((child, depth))
            queue.extend((grandchild, depth + 1) for grandchild in self.team(child))
        return result

    def depth(self, seller_id: int) -> int:
        depth, current, seen = 0, self.leader_of.get(seller_id), {seller_id}
        while current is not None and current not in seen:
            seen.add(current)
            depth += 1
            current = self.leader_of.get(current)
        return depth


def seller_out(row: Seller, sales: dict[int, int] | None = None, teams: Teams | None = None) -> SellerOut:
    return SellerOut(
        id=row.id,
        name=row.name,
        document=row.document or "",
        email=row.email or "",
        phone=row.phone or "",
        region_id=row.region_id,
        region_name=row.region.name,
        commission_bp=row.region.commission_bp or 0,
        active=bool(row.active),
        notes=row.notes or "",
        subscriptions_count=(sales or {}).get(row.id, 0),
        leader_id=row.leader_id,
        leader_name=row.leader.name if row.leader is not None else None,
        team_size=len(teams.team(row.id)) if teams else 0,
        organisation_size=len(teams.organisation(row.id)) if teams else 0,
        depth=teams.depth(row.id) if teams else 0,
    )


def _load_seller(db: Session, item_id: int) -> Seller:
    row = (
        db.query(Seller)
        .options(joinedload(Seller.region), joinedload(Seller.leader))
        .filter(Seller.id == item_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Seller not found")
    return row


def _collaborators(db: Session, leader_id: int) -> list[Seller]:
    return (
        db.query(Seller)
        .options(joinedload(Seller.region), joinedload(Seller.leader))
        .filter(Seller.leader_id == leader_id)
        .order_by(Seller.name)
        .all()
    )


def _check_seller(db: Session, body: SellerIn, row: Seller | None = None) -> None:
    """Region and team rules: the leader is a seller of the same region and
    not the seller itself nor anyone below it (no cycles; any depth); a
    seller leading others cannot leave their region."""
    if db.get(Region, body.region_id) is None:
        raise HTTPException(status_code=404, detail="Region not found")
    team = _collaborators(db, row.id) if row is not None else []
    if body.leader_id is not None:
        if row is not None and body.leader_id == row.id:
            raise HTTPException(status_code=400, detail="A seller cannot be its own leader")
        leader = _load_seller(db, body.leader_id) if db.get(Seller, body.leader_id) is not None else None
        if leader is None:
            raise HTTPException(status_code=404, detail="Leader not found")
        if leader.region_id != body.region_id:
            raise HTTPException(status_code=409, detail=f"Leader {leader.name} is in another region ({leader.region.name})")
        if row is not None and any(item.id == row.id for item in ancestors(leader)):
            raise HTTPException(status_code=409, detail=f"{leader.name} is in {row.name}'s team: that would make a cycle")
    if row is not None and team and body.region_id != row.region_id:
        raise HTTPException(status_code=409, detail=f"{row.name} leads {len(team)} seller(s) in {row.region.name}; reassign them before moving it")


@sellers.get("", response_model=list[SellerOut])
def list_sellers(region_id: int | None = None, active: bool | None = None, db: Session = Depends(get_db)):
    query = db.query(Seller).options(joinedload(Seller.region))
    if region_id is not None:
        query = query.filter(Seller.region_id == region_id)
    if active is not None:
        query = query.filter(Seller.active.is_(active))
    sales, teams = _sales_counts(db), Teams(db)
    query = query.options(joinedload(Seller.leader))
    return [seller_out(row, sales, teams) for row in query.order_by(Seller.name).all()]


@sellers.post("", response_model=SellerOut, status_code=201, dependencies=WRITE)
def create_seller(body: SellerIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _check_seller(db, body)
    row = Seller(**body.model_dump())
    db.add(row)
    db.flush()
    if row.leader_id is not None:
        seller_leader_changed(db, _load_seller(db, row.id), None, user)
    db.commit()
    return seller_out(_load_seller(db, row.id), _sales_counts(db), Teams(db))


@sellers.get("/{item_id}", response_model=SellerOut)
def get_seller(item_id: int, db: Session = Depends(get_db)):
    return seller_out(_load_seller(db, item_id), _sales_counts(db), Teams(db))


@sellers.get("/{item_id}/team", response_model=list[SellerOut])
def seller_team(item_id: int, db: Session = Depends(get_db)):
    """The collaborators a seller leads."""
    _load_seller(db, item_id)
    sales, teams = _sales_counts(db), Teams(db)
    return [seller_out(row, sales, teams) for row in _collaborators(db, item_id)]


@sellers.put("/{item_id}", response_model=SellerOut, dependencies=WRITE)
def update_seller(item_id: int, body: SellerIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Moving a seller to another region bounds its next sales by the new
    region; the sales already made keep the region they were made in. The
    seller is told when it is moved, deactivated or reactivated."""
    row = _load_seller(db, item_id)
    _check_seller(db, body, row)
    previous_region_id, previous_region, previous_active = row.region_id, row.region.name, bool(row.active)
    previous_leader_id = row.leader_id
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    db.flush()
    db.expire(row, ["region", "leader"])
    if row.region_id != previous_region_id:
        seller_moved(db, row, previous_region, user)
    if bool(row.active) != previous_active:
        seller_status_changed(db, row, user)
    if row.leader_id != previous_leader_id:
        seller_leader_changed(db, row, previous_leader_id, user)
    db.commit()
    db.expire_all()
    return seller_out(_load_seller(db, item_id), _sales_counts(db), Teams(db))


@sellers.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_seller(item_id: int, db: Session = Depends(get_db)):
    row = _load_seller(db, item_id)
    if db.query(Subscription).filter(Subscription.seller_id == item_id).count():
        raise HTTPException(status_code=409, detail="Seller has sales recorded: deactivate it instead")
    if _collaborators(db, item_id):
        raise HTTPException(status_code=409, detail="Seller leads a team: reassign its collaborators first")
    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record is in use")


# --- the seller's own area ------------------------------------------------------


def _me(db: Session, user: User) -> SellerMeOut:
    row = _load_seller(db, user.seller_id)
    region = _load_region(db, row.region_id)
    sold_to = db.query(Subscription.customer_id).filter(Subscription.seller_id == row.id)
    customers = (
        db.query(Customer).filter((Customer.seller_id == row.id) | Customer.id.in_(sold_to)).count()
    )
    sales, teams = _sales_counts(db), Teams(db)
    organisation = []
    for seller_id, depth in teams.organisation(row.id):
        member = seller_out(_load_seller(db, seller_id), sales, teams)
        member.depth = depth  # relative to the viewer
        organisation.append(member)
    return SellerMeOut(
        **seller_out(row, sales, teams).model_dump(),
        region=region_out(region, _seller_counts(db)),
        customers_count=customers,
        leader=seller_out(_load_seller(db, row.leader_id), sales, teams) if row.leader_id is not None else None,
        team=[seller_out(item, sales, teams) for item in _collaborators(db, row.id)],
        organisation=organisation,
        override_bps=_override_bps(region),
    )


@me.get("", response_model=SellerMeOut)
def my_record(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The seller's own record and the rules of its region."""
    return _me(db, user)


@me.put("", response_model=SellerMeOut)
def update_my_record(body: SellerProfileIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Contact data only: the name, the region and the status are the staff's."""
    row = _load_seller(db, user.seller_id)
    row.document = body.document
    row.email = body.email
    row.phone = body.phone
    db.commit()
    return _me(db, user)
