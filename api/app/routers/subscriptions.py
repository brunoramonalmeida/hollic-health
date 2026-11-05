from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..auth import (
    STAFF_ROLES,
    ensure_customer_access,
    get_current_user,
    is_client,
    is_seller,
    require_customer_area,
    require_seller,
    require_staff,
)
from ..billing import (
    INSTALLMENT_OVERDUE,
    INSTALLMENT_PAID,
    PART_COMPANY,
    PART_EMPLOYEE,
    PARTS,
    current_subscription,
    is_split,
    locked,
    part_cents,
    part_paid,
    register_part,
    undo_part,
    dependents_count,
    installment_status,
    list_amount,
    monthly_amount,
    subscription_status,
    sync_installments,
    waiting_until,
)
from ..database import get_db
from ..models import (
    Agreement,
    EnrollmentShare,
    Customer,
    Installment,
    Modality,
    Region,
    RegionPlan,
    SaleChange,
    SaleOverride,
    Seller,
    Subscription,
    User,
)
from ..partners import (
    PAYER_COMPANY,
    split_cents,
    share_on,
    EnrollmentError,
    Ledger,
    check_enrollment,
    grace_days_for,
    invoice_period,
    ledger,
    load_agreement,
    offer_for,
    reference_period,
)
from ..sales import NO_SELLER, SaleError, SaleTerms, check_sale, commission_cents
from ..notifications import (
    change_summary,
    installment_paid,
    installment_part_paid,
    subscription_upgraded,
    sale_change_decided,
    sale_change_requested,
    sale_change_withdrawn,
    subscription_cancelled,
    subscription_created,
)
from ..taxation import apply_installment_taxes, clear_installment_taxes
from ..usage import plan_usage, usage_by_period
from ..categories import EnterprisePlanError, check_individual_use, enterprise_only, is_enterprise, plan_grace_days
from ..copayment import (
    current_share,
    history_out,
    initial_share,
    monthly_split,
    record_share,
    share_effective_from,
    start_share,
)
from .partners import track_minimum
from ..schemas import (
    InstallmentOut,
    InstallmentPay,
    SaleChangeDecision,
    SaleChangeIn,
    SaleChangeOut,
    SaleOverrideOut,
    SaleQuoteOut,
    ShareChangeIn,
    SubscriptionCancel,
    SubscriptionIn,
    SubscriptionOut,
)

router = APIRouter(
    prefix="/subscriptions", tags=["subscriptions"], dependencies=[Depends(require_customer_area)]
)
# Registering money, reactivating and deleting are staff actions. Clients may
# subscribe, view and cancel their own plan. Sellers record their own sales
# (to anyone inside their region's scope) and follow them; to edit or cancel
# a sale a seller asks for a **change request** that the customer confirms
# (the client login, or the staff on the client's word) before it is applied.
# Corporate enrollments (B2B, ``agreement_id``) are recorded and managed by
# the staff only: the employee sees its plan but changes go through its
# company (see app.partners).
STAFF = [Depends(require_staff)]
SELLER = [Depends(require_seller)]


def _ledger(db: Session, agreement_id: int, cache: dict | None) -> Ledger:
    """The agreement's fees by month (memoised per request when listing)."""
    if cache is None:
        return ledger(db, agreement_id)
    if agreement_id not in cache:
        cache[agreement_id] = ledger(db, agreement_id)
    return cache[agreement_id]


def _corporate(row: Subscription, db: Session, today: date, cache: dict | None) -> dict:
    """The corporate side of a subscription: the agreement's waiting period
    for its plan (its terms are frozen) and the company's headcount this
    month against the agreement's minimum."""
    agreement = row.agreement
    book = _ledger(db, agreement.id, cache)
    period = reference_period(agreement, today)
    employees = book.employees(period)
    return {
        "grace_days": grace_days_for(offer_for(agreement.plans, row.modality_id), plan_grace_days(row.modality)),
        "employees": employees,
        "conditions_met": employees >= max(agreement.min_employees, 1),
    }


def _out(
    row: Subscription, db: Session, user: User | None = None, today: date | None = None, cache: dict | None = None
) -> SubscriptionOut:
    """``user``: a client login does not see the seller's commission nor the
    company's headcount (internal). ``cache``: agreement rosters shared by a
    listing."""
    today = today or clock.today()
    items = sorted(row.installments, key=lambda item: item.sequence)
    statuses = {item.id: installment_status(item, today) for item in items}
    open_items = [item for item in items if statuses[item.id] != INSTALLMENT_PAID]
    usage = plan_usage(db, row, today)  # the allowance of the period we are in
    used_by_period = usage_by_period(db, row)
    dependents = dependents_count(db, row.customer_id)
    sold_by_seller = row.seller_id is not None
    disclose = user is None or not is_client(user)
    corporate = _corporate(row, db, today, cache) if row.agreement is not None else None
    company_monthly, employee_monthly = monthly_split(db, row, today)
    commissions = {
        item.id: commission_cents(item.paid_cents or 0, row.commission_bp or 0)
        if sold_by_seller and item.paid_on is not None
        else 0
        for item in items
    }
    paid = [item.paid_cents or 0 for item in items if item.paid_on is not None]
    overrides = [
        SaleOverrideOut(
            level=snapshot.level,
            seller_id=snapshot.seller_id,
            seller_name=snapshot.seller.name,
            rate_bp=snapshot.rate_bp,
            earned_cents=sum(commission_cents(cents, snapshot.rate_bp) for cents in paid),
        )
        for snapshot in row.overrides
    ]
    pending = (
        db.query(SaleChange)
        .options(joinedload(SaleChange.seller), joinedload(SaleChange.modality))
        .filter(SaleChange.subscription_id == row.id, SaleChange.status == CHANGE_PENDING)
        .first()
    )
    return SubscriptionOut(
        id=row.id,
        pending_change=_change_out(pending) if pending is not None else None,
        customer_id=row.customer_id,
        customer_name=row.customer.name,
        modality_id=row.modality_id,
        modality_name=row.modality.name,
        joined_on=row.joined_on,
        expires_on=row.expires_on,
        cancelled_on=row.cancelled_on,
        status=subscription_status(row, items, today),
        usable_from=waiting_until(row.joined_on, corporate["grace_days"] if corporate else plan_grace_days(row.modality)),
        dependents_count=dependents,
        monthly_amount_cents=monthly_amount(row.modality, dependents, row.discount_percent or 0),
        list_amount_cents=list_amount(row.modality, dependents),
        seller_id=row.seller_id,
        seller_name=row.seller.name if row.seller is not None else None,
        region_id=row.region_id,
        region_name=row.region.name if row.region is not None else None,
        commission_bp=(row.commission_bp or 0) if disclose else None,
        discount_percent=row.discount_percent or 0,
        commission_earned_cents=sum(commissions.values()) if disclose else None,
        overrides=overrides if disclose else [],
        agreement_id=row.agreement_id,
        agreement_name=row.agreement.name if corporate else None,
        partner_id=row.agreement.partner_id if corporate else None,
        partner_name=row.agreement.partner.name if corporate else None,
        paid_by_company=bool(corporate) and row.agreement.payer == PAYER_COMPANY,
        agreement_min_employees=row.agreement.min_employees if corporate and disclose else None,
        agreement_employees=corporate["employees"] if corporate and disclose else None,
        agreement_conditions_met=corporate["conditions_met"] if corporate and disclose else None,
        payer=row.agreement.payer if corporate else None,
        company_share_percent=current_share(row, today) if corporate else None,
        company_monthly_cents=company_monthly,
        employee_monthly_cents=employee_monthly,
        share_history=history_out(row) if corporate else [],
        upgraded_from_id=row.upgraded_from_id,
        upgraded_to_id=row.upgraded_to_id,
        installments_total=len(items),
        installments_paid=sum(1 for item in items if statuses[item.id] == INSTALLMENT_PAID),
        installments_overdue=sum(1 for item in items if statuses[item.id] == INSTALLMENT_OVERDUE),
        next_due_on=min((item.due_on for item in open_items), default=None),
        appointment_limit=usage.limit,
        appointment_limit_period=usage.period,
        appointments_used=usage.used,
        appointments_remaining=usage.remaining,
        usage_window_start=usage.window_start,
        usage_window_end=usage.window_end,
        installments=[
            InstallmentOut(
                id=item.id,
                sequence=item.sequence,
                period_start=item.period_start,
                period_end=item.period_end,
                due_on=item.due_on,
                amount_cents=item.amount_cents,
                discount_cents=item.discount_cents or 0,
                paid_on=item.paid_on,
                paid_cents=item.paid_cents,
                tax_cents=item.tax_cents or 0,
                status=statuses[item.id],
                appointments_used=used_by_period.get(item.sequence, 0),
                commission_cents=commissions[item.id] if disclose else None,
                company_share_cents=part_cents(item, PART_COMPANY) if is_split(row) else 0,
                employee_share_cents=part_cents(item, PART_EMPLOYEE) if is_split(row) else item.amount_cents,
                company_paid_on=item.company_paid_on,
                company_paid_cents=item.company_paid_cents,
                employee_paid_on=item.employee_paid_on,
                employee_paid_cents=item.employee_paid_cents,
            )
            for item in items
        ],
    )


def _query(db: Session):
    return db.query(Subscription).options(
        joinedload(Subscription.customer),
        joinedload(Subscription.modality),
        joinedload(Subscription.seller),
        joinedload(Subscription.region),
        joinedload(Subscription.agreement).joinedload(Agreement.partner),
        joinedload(Subscription.agreement).selectinload(Agreement.plans),
        selectinload(Subscription.installments),
        selectinload(Subscription.overrides).joinedload(SaleOverride.seller),
        selectinload(Subscription.shares).joinedload(EnrollmentShare.created_by),
    )


def _load(db: Session, item_id: int) -> Subscription:
    row = _query(db).filter(Subscription.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return row


def _check(db: Session, body: SubscriptionIn, exclude_id: int | None = None) -> Subscription | None:
    """Existence checks; returns the subscription an upgrade replaces.

    A customer has one plan at a time: the new (or edited) period may not
    overlap another non-cancelled subscription of the customer — except, on
    an **upgrade**, the one plan it overlaps (which then ends the day before
    the new one starts) and, on an edit, the other side of an upgrade. A plan starting after the current one
    ends is simply the next one (e.g. a renewed six-month corporate
    contract): nothing is replaced, and a plan that already ended is never
    stretched to meet the new one."""
    if db.get(Customer, body.customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if db.get(Modality, body.modality_id) is None:
        raise HTTPException(status_code=404, detail="Modality not found")
    if body.agreement_id is not None and db.get(Agreement, body.agreement_id) is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    try:
        check_individual_use(db.get(Modality, body.modality_id), body.agreement_id)
    except EnterprisePlanError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message)
    skip = {exclude_id}
    if exclude_id is not None:
        edited = db.get(Subscription, exclude_id)
        skip |= {edited.upgraded_to_id, edited.upgraded_from_id}  # the other side of an upgrade is no conflict
    overlapping = [
        row
        for row in db.query(Subscription)
        .filter(Subscription.customer_id == body.customer_id, Subscription.cancelled_on.is_(None))
        .all()
        if row.id not in skip and row.joined_on <= body.expires_on and body.joined_on <= row.expires_on
    ]
    if not overlapping:
        return None
    # An upgrade replaces the plan the new one overlaps (the one in force on its start).
    if not body.upgrade or exclude_id is not None or len(overlapping) != 1:
        raise HTTPException(
            status_code=409,
            detail="Customer already has a subscription: upgrade it (upgrade=true) or cancel it first",
        )
    other = overlapping[0]
    if body.joined_on <= other.joined_on:
        raise HTTPException(
            status_code=409,
            detail=f"An upgrade starts after the current plan's join date ({other.joined_on:%Y-%m-%d})",
        )
    return other


def _upgrade(db: Session, previous: Subscription, new: Subscription) -> None:
    """End the replaced plan the day before the new one starts: the open
    installments after that are no longer owed, the paid ones stay (no
    proration). Both records point at each other."""
    previous = _load(db, previous.id)
    end = new.joined_on - timedelta(days=1)
    paid_after = [item for item in previous.installments if locked(item) and item.period_start > end]
    if paid_after:
        raise HTTPException(
            status_code=409,
            detail=f"The current plan has a paid installment starting {paid_after[0].period_start:%Y-%m-%d}, "
            "after the upgrade: start the new plan after it",
        )
    previous.expires_on = end
    previous.upgraded_to_id = new.id
    new.upgraded_from_id = previous.id
    sync_installments(db, previous)


def _load_seller(db: Session, seller_id: int) -> Seller:
    seller = (
        db.query(Seller)
        .options(
            joinedload(Seller.region).selectinload(Region.areas),
            joinedload(Seller.region).selectinload(Region.plans).joinedload(RegionPlan.modality),
            joinedload(Seller.region).selectinload(Region.overrides),
            joinedload(Seller.leader),
        )
        .filter(Seller.id == seller_id)
        .first()
    )
    if seller is None:
        raise HTTPException(status_code=404, detail="Seller not found")
    return seller


def _seller_terms(db: Session, seller_id: int | None, modality_id: int, customer_id: int) -> SaleTerms:
    """Terms of the sale, checked against the seller's region (app.sales)."""
    if seller_id is None:
        return NO_SELLER
    try:
        return check_sale(_load_seller(db, seller_id), db.get(Modality, modality_id), db.get(Customer, customer_id))
    except SaleError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message)


def _requested_seller(body: SubscriptionIn, user: User, row: Subscription | None) -> int | None:
    """The seller of the sale. Omitted on an edit: unchanged (explicit null
    removes it). Only the staff attributes a sale to a seller: a client
    never sets or changes it, and a seller login sells only as itself."""
    current = row.seller_id if row is not None else None
    requested = body.seller_id if "seller_id" in body.model_fields_set else current
    if is_client(user):
        if body.seller_id is not None and body.seller_id != current:
            raise HTTPException(status_code=403, detail="Only the staff records a seller's sale")
        return current
    if is_seller(user):
        if body.seller_id is not None and body.seller_id != user.seller_id:
            raise HTTPException(status_code=403, detail="A seller only records its own sales")
        return user.seller_id
    return requested


def _no_sellers(user: User, what: str) -> None:
    if is_seller(user):
        raise HTTPException(status_code=403, detail=f"Sellers do not {what}; ask the staff")


def _is_staff(user: User) -> bool:
    return user.role in STAFF_ROLES


def _requested_agreement(body: SubscriptionIn, user: User, row: Subscription | None) -> int | None:
    """The corporate agreement of the enrollment. Omitted on an edit:
    unchanged (explicit null removes it). Only the staff enrolls employees
    in an agreement or takes them out of one."""
    current = row.agreement_id if row is not None else None
    requested = body.agreement_id if "agreement_id" in body.model_fields_set else current
    if requested != current and not _is_staff(user):
        raise HTTPException(status_code=403, detail="Only the staff enrolls employees in a corporate agreement")
    return requested


def _company_managed(user: User, row: Subscription | None, what: str) -> None:
    """A corporate enrollment is managed by the staff on the company's
    word: the employee (or a seller) does not change it on its own."""
    if row is None or row.agreement_id is None or _is_staff(user):
        return
    partner = row.agreement.partner.name if row.agreement is not None else "the company"
    raise HTTPException(
        status_code=403,
        detail=f"This plan is part of {partner}'s corporate agreement: {what} goes through the company (contact the staff)",
    )


def _enrollment_terms(db: Session, agreement_id: int, modality_id: int, joined_on: date, expires_on: date, new: bool):
    """The agreement's conditions for the enrollment (app.partners), or 409."""
    agreement = load_agreement(db, agreement_id)
    if agreement is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    try:
        return check_enrollment(agreement, db.get(Modality, modality_id), joined_on, expires_on, clock.today(), new=new)
    except EnrollmentError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message)


def _no_seller_on_enrollment(seller_id: int | None, agreement_id: int | None) -> None:
    if seller_id is not None and agreement_id is not None:
        raise HTTPException(
            status_code=409,
            detail="A corporate enrollment is not a seller's sale: remove the seller (seller_id: null) to enroll in the agreement",
        )


def _apply_terms(row: Subscription, terms: SaleTerms) -> None:
    row.seller_id = terms.seller_id
    row.region_id = terms.region_id
    row.commission_bp = terms.commission_bp
    row.discount_percent = terms.discount_percent
    row.overrides = [
        SaleOverride(level=item.level, seller_id=item.seller_id, rate_bp=item.rate_bp) for item in terms.overrides
    ]


def organisation_ids(db: Session, seller_id: int) -> set[int]:
    """The seller itself and every seller below it, all levels."""
    leader_of = dict(db.query(Seller.id, Seller.leader_id).filter(Seller.leader_id.is_not(None)).all())
    reports: dict[int, list[int]] = {}
    for child, leader in leader_of.items():
        reports.setdefault(leader, []).append(child)
    result, queue = {seller_id}, list(reports.get(seller_id, []))
    while queue:
        current = queue.pop()
        if current in result:
            continue
        result.add(current)
        queue.extend(reports.get(current, []))
    return result


def _team_ids(db: Session, user: User) -> set[int]:
    return organisation_ids(db, user.seller_id)


def _own_sale(user: User, row: Subscription, db: Session | None = None) -> None:
    """A seller follows its own sales — and, with ``db``, those of its team."""
    if not is_seller(user) or row.seller_id == user.seller_id:
        return
    if db is not None and row.seller_id in _team_ids(db, user):
        return
    raise HTTPException(status_code=403, detail="Not allowed for another seller's sale")


def _apply_update(
    db: Session, row: Subscription, body: SubscriptionIn, seller_id: int | None, agreement_id: int | None = None
) -> list[int | None]:
    """Change the terms of a subscription (used by the staff/client edit and
    by a confirmed seller's request). The sale is re-checked against the
    seller's region only when it changes (seller, plan or customer);
    otherwise the terms sold are kept even if the region's rules changed.
    A corporate enrollment is checked against its agreement on every edit
    (the plan and the period; moving into an agreement is checked as a new
    enrollment). Returns the agreements whose headcount may have changed
    (the caller re-prices them once everything is in place)."""
    if row.cancelled_on is not None:
        raise HTTPException(status_code=409, detail="Cancelled subscription cannot be edited")
    _no_seller_on_enrollment(seller_id, agreement_id)
    body = body.model_copy(update={"agreement_id": agreement_id})
    _check(db, body, exclude_id=row.id)
    paid_after_end = [
        item for item in row.installments if locked(item) and item.period_start > body.expires_on
    ]
    if paid_after_end:
        raise HTTPException(
            status_code=409,
            detail="expires_on cannot be before a paid installment "
            f"(installment {paid_after_end[0].sequence} starts {paid_after_end[0].period_start:%Y-%m-%d})",
        )
    if row.upgraded_from_id is not None:
        previous = db.get(Subscription, row.upgraded_from_id)
        if previous is not None and body.joined_on <= previous.expires_on:
            raise HTTPException(
                status_code=409,
                detail=f"The plan starts after the one it replaced ends ({previous.expires_on:%Y-%m-%d})",
            )
    if row.upgraded_to_id is not None:
        following = db.get(Subscription, row.upgraded_to_id)
        if following is not None and body.expires_on >= following.joined_on:
            raise HTTPException(
                status_code=409,
                detail=f"The plan was replaced by an upgrade starting {following.joined_on:%Y-%m-%d}; it cannot run past it",
            )
    offer = None
    if agreement_id is not None:
        offer = _enrollment_terms(
            db, agreement_id, body.modality_id, body.joined_on, body.expires_on, new=agreement_id != row.agreement_id
        )
    previous_agreement = row.agreement_id
    if (seller_id, body.modality_id, body.customer_id, agreement_id) != (
        row.seller_id,
        row.modality_id,
        row.customer_id,
        row.agreement_id,
    ):
        _apply_terms(row, _seller_terms(db, seller_id, body.modality_id, body.customer_id))
    moved = agreement_id != row.agreement_id
    row.agreement_id = agreement_id
    if offer is not None:
        row.discount_percent = offer.discount_percent  # the negotiated one, snapshotted (frozen terms)
    for key in ("customer_id", "modality_id", "joined_on", "expires_on"):
        setattr(row, key, getattr(body, key))
    db.flush()
    db.refresh(row, attribute_names=["modality", "seller", "region", "agreement"])
    if moved:
        # A new split history: the new agreement's default (none for an individual plan).
        row.shares = []
        db.flush()
        if row.agreement is not None:
            start_share(db, row, initial_share(row.agreement, row.modality_id, None), None)
    sync_installments(db, row)
    return [previous_agreement, agreement_id]


def _apply_cancel(db: Session, row: Subscription, cancelled_on: date | None, actor: User) -> None:
    if row.cancelled_on is not None:
        raise HTTPException(status_code=409, detail="Subscription is already cancelled")
    cancelled_on = cancelled_on or clock.today()
    if cancelled_on < row.joined_on:
        raise HTTPException(status_code=400, detail="cancelled_on cannot be before joined_on")
    row.cancelled_on = cancelled_on
    # Open installments after the cancellation are no longer owed.
    for item in list(row.installments):
        if not locked(item) and item.period_start > cancelled_on:
            row.installments.remove(item)
    subscription_cancelled(db, row, actor)


@router.get("", response_model=list[SubscriptionOut])
def list_subscriptions(
    seller_id: int | None = None,
    region_id: int | None = None,
    agreement_id: int | None = None,
    partner_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """``seller_id`` / ``region_id``: the sales of a seller / made in a
    region; ``agreement_id`` / ``partner_id``: the corporate enrollments of
    an agreement / of every agreement of a partner company."""
    query = _query(db)
    if is_client(user):
        query = query.filter(Subscription.customer_id == user.customer_id)
    if is_seller(user):
        query = query.filter(Subscription.seller_id.in_(sorted(_team_ids(db, user))))  # its own sales and its team's
    if seller_id is not None:
        query = query.filter(Subscription.seller_id == seller_id)
    if region_id is not None:
        query = query.filter(Subscription.region_id == region_id)
    if agreement_id is not None:
        query = query.filter(Subscription.agreement_id == agreement_id)
    if partner_id is not None:
        query = query.filter(Subscription.agreement_id.in_(db.query(Agreement.id).filter(Agreement.partner_id == partner_id)))
    rows = query.order_by(Subscription.id.desc()).all()
    cache: dict = {}
    return [_out(row, db, user, cache=cache) for row in rows]


@router.get("/quote", response_model=SaleQuoteOut)
def quote_subscription(
    customer_id: int,
    modality_id: int,
    seller_id: int | None = None,
    agreement_id: int | None = None,
    joined_on: date | None = None,
    expires_on: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Preview of a sale: the monthly amount and, with a seller, whether its
    region allows the sale and with which discount and commission. With an
    ``agreement_id`` (staff only), the corporate enrollment: whether the
    agreement covers the plan and the period (``joined_on``, default today;
    ``expires_on``, default the join day), its conditions and whether its
    minimum of employees is met on the join day counting this enrollment.
    Never refuses: ``allowed``/``reason`` tell what creating it would answer."""
    ensure_customer_access(user, customer_id, customer_area=True)
    if seller_id is not None and is_client(user):
        raise HTTPException(status_code=403, detail="Only the staff records a seller's sale")
    if agreement_id is not None and not _is_staff(user):
        raise HTTPException(status_code=403, detail="Only the staff enrolls employees in a corporate agreement")
    if agreement_id is not None and seller_id is not None:
        raise HTTPException(status_code=400, detail="A corporate enrollment is not a seller's sale: send seller_id or agreement_id")
    if is_seller(user):
        if seller_id is not None and seller_id != user.seller_id:
            raise HTTPException(status_code=403, detail="A seller only records its own sales")
        seller_id = user.seller_id
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    modality = db.get(Modality, modality_id)
    if modality is None:
        raise HTTPException(status_code=404, detail="Modality not found")
    dependents = dependents_count(db, customer_id)
    terms, reason, region = NO_SELLER, None, None
    if seller_id is not None:
        seller = _load_seller(db, seller_id)
        region = seller.region
        try:
            terms = check_sale(seller, modality, customer)
        except SaleError as error:
            reason = error.message
    if agreement_id is not None:
        return _enrollment_quote(db, customer, modality, dependents, agreement_id, joined_on, expires_on)
    if is_enterprise(modality):
        reason = enterprise_only(modality)
    monthly = monthly_amount(modality, dependents, terms.discount_percent)
    return SaleQuoteOut(
        customer_id=customer_id,
        modality_id=modality_id,
        seller_id=seller_id,
        region_id=region.id if region is not None else None,
        region_name=region.name if region is not None else None,
        allowed=reason is None,
        reason=reason,
        dependents_count=dependents,
        list_amount_cents=list_amount(modality, dependents),
        discount_percent=terms.discount_percent,
        monthly_amount_cents=monthly,
        commission_bp=terms.commission_bp,
        commission_cents=commission_cents(monthly, terms.commission_bp),
    )


def _enrollment_quote(
    db: Session, customer: Customer, modality: Modality, dependents: int, agreement_id: int, joined_on, expires_on
) -> SaleQuoteOut:
    agreement = load_agreement(db, agreement_id)
    if agreement is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    joined_on = joined_on or max(clock.today(), agreement.starts_on)
    expires_on = expires_on or agreement.ends_on
    reason, offer = None, None
    try:
        offer = check_enrollment(agreement, modality, joined_on, expires_on, clock.today())
    except EnrollmentError as error:
        reason = error.message
        offer = offer_for(agreement.plans, modality.id)
    # Headcount of the join month with this enrollment (its first fee is due on the join day).
    employees = ledger(db, agreement.id).employees(invoice_period(joined_on), exclude_customer_id=customer.id) + 1
    percent = offer.discount_percent if offer is not None else 0
    monthly = monthly_amount(modality, dependents, percent)
    company_part = split_cents(monthly, initial_share(agreement, modality.id, None) if offer is not None else 0)
    return SaleQuoteOut(
        customer_id=customer.id,
        modality_id=modality.id,
        allowed=reason is None,
        reason=reason,
        dependents_count=dependents,
        list_amount_cents=list_amount(modality, dependents),
        discount_percent=percent,
        monthly_amount_cents=monthly,
        agreement_id=agreement.id,
        agreement_name=agreement.name,
        partner_name=agreement.partner.name,
        payer=agreement.payer,
        agreement_ends_on=agreement.ends_on,
        min_employees=agreement.min_employees,
        employees=employees,
        conditions_met=employees >= max(agreement.min_employees, 1),
        negotiated_discount_percent=offer.discount_percent if offer is not None else None,
        grace_days=grace_days_for(offer, plan_grace_days(modality)),
        company_share_percent=initial_share(agreement, modality.id, None) if offer is not None else None,
        company_monthly_cents=company_part,
        employee_monthly_cents=monthly - company_part,
    )


@router.get("/customer/{customer_id}", response_model=SubscriptionOut | None)
def customer_subscription(
    customer_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The customer's current (non-cancelled) subscription, or null."""
    ensure_customer_access(user, customer_id, customer_area=True)
    if db.get(Customer, customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    row = current_subscription(db, customer_id)
    return _out(_load(db, row.id), db, user) if row is not None else None


@router.post("", response_model=SubscriptionOut, status_code=201)
def create_subscription(
    body: SubscriptionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """A new plan for the customer. With ``upgrade: true`` it replaces the
    customer's current subscription (a **plan upgrade**): the current plan
    ends the day before the new one starts and the new one is sold like
    any other (seller, region rules, commission). Without it, a customer
    with a current subscription is refused."""
    ensure_customer_access(user, body.customer_id, customer_area=True)
    agreement_id = _requested_agreement(body, user, None)
    if body.company_share_percent is not None and not _is_staff(user):
        raise HTTPException(status_code=403, detail="Only the staff (or the company, in its portal) sets a co-payment split")
    row = record_subscription(db, body, user, _requested_seller(body, user, None), agreement_id)
    db.commit()
    return _out(_load(db, row.id), db, user)


def record_subscription(
    db: Session, body: SubscriptionIn, user: User, seller_id: int | None, agreement_id: int | None
) -> Subscription:
    """(See below.) A corporate enrollment starts its split history: the
    split the company and the employee agreed (``body.company_share_percent``,
    co-payment only), the one in force on an upgrade inside the same
    agreement, or the plan's / agreement's default."""
    """Create a subscription under every rule (one plan at a time or an
    upgrade, the seller's region or the corporate agreement), with its
    installments and notifications; the caller commits. Used by the staff,
    clients and sellers here, and by the companies' portal for enrollments."""
    previous = _check(db, body)
    _company_managed(user, previous, "an upgrade")
    _no_seller_on_enrollment(seller_id, agreement_id)
    offer = (
        _enrollment_terms(db, agreement_id, body.modality_id, body.joined_on, body.expires_on, new=True)
        if agreement_id is not None
        else None
    )
    terms = _seller_terms(db, seller_id, body.modality_id, body.customer_id)
    row = Subscription(
        customer_id=body.customer_id,
        modality_id=body.modality_id,
        joined_on=body.joined_on,
        expires_on=body.expires_on,
        created_by_id=user.id,
    )
    _apply_terms(row, terms)
    if offer is not None:
        row.agreement_id = agreement_id
        row.discount_percent = offer.discount_percent  # the negotiated one, snapshotted (frozen terms)
    db.add(row)
    db.flush()
    if offer is not None:
        requested = body.company_share_percent
        if requested is None and previous is not None and previous.agreement_id == agreement_id:
            requested = share_on(previous.shares, row.joined_on)  # an upgrade keeps the agreed split
        start_share(db, row, initial_share(row.agreement, row.modality_id, requested), user)
    elif body.company_share_percent is not None:
        raise HTTPException(status_code=409, detail="Only a corporate enrollment has a split")
    if previous is not None:
        _upgrade(db, previous, row)
    sync_installments(db, row)
    # A new employee (or one leaving through an upgrade) changes the company's headcount.
    track_minimum(db, [row.agreement_id, previous.agreement_id if previous is not None else None], user)
    db.flush()
    subscription_created(db, _load(db, row.id), user)
    if previous is not None:
        subscription_upgraded(db, _load(db, previous.id), _load(db, row.id), user)
    return row


@router.get("/{item_id}", response_model=SubscriptionOut)
def get_subscription(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = _load(db, item_id)
    ensure_customer_access(user, row.customer_id, customer_area=True)
    _own_sale(user, row, db)
    return _out(row, db, user)


@router.put("/{item_id}", response_model=SubscriptionOut)
def update_subscription(
    item_id: int, body: SubscriptionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The staff or the client change a subscription directly; a seller asks
    for a change the customer confirms (``POST /{id}/changes``)."""
    row = _load(db, item_id)
    _no_sellers(user, "edit sales directly: ask for a change the customer confirms")
    ensure_customer_access(user, row.customer_id)
    ensure_customer_access(user, body.customer_id)
    _company_managed(user, row, "changing it")
    agreement_id = _requested_agreement(body, user, row)
    touched = _apply_update(db, row, body, _requested_seller(body, user, row), agreement_id)
    track_minimum(db, touched, user)
    db.commit()
    return _out(_load(db, item_id), db, user)


@router.post("/{item_id}/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    item_id: int,
    body: SubscriptionCancel | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _load(db, item_id)
    _no_sellers(user, "cancel sales directly: ask for a cancellation the customer confirms")
    ensure_customer_access(user, row.customer_id)
    _company_managed(user, row, "cancelling it")
    _apply_cancel(db, row, body.cancelled_on if body else None, user)
    track_minimum(db, [row.agreement_id], user)
    db.commit()
    return _out(_load(db, item_id), db, user)


# --- a seller's change requests ---------------------------------------------------

CHANGE_PENDING, CHANGE_CONFIRMED, CHANGE_REJECTED, CHANGE_WITHDRAWN = "pending", "confirmed", "rejected", "withdrawn"


def _change_out(change: SaleChange) -> SaleChangeOut:
    return SaleChangeOut(
        id=change.id,
        subscription_id=change.subscription_id,
        seller_id=change.seller_id,
        seller_name=change.seller.name,
        kind=change.kind,
        status=change.status,
        modality_id=change.modality_id,
        modality_name=change.modality.name if change.modality is not None else None,
        joined_on=change.joined_on,
        expires_on=change.expires_on,
        cancelled_on=change.cancelled_on,
        note=change.note or "",
        summary=change_summary(change),
        created_at=change.created_at,
        decided_at=change.decided_at,
        decided_by_name=change.decided_by.name if change.decided_by is not None else None,
        decision_note=change.decision_note or "",
    )


def _changes(db: Session, subscription_id: int):
    return (
        db.query(SaleChange)
        .options(joinedload(SaleChange.seller), joinedload(SaleChange.modality), joinedload(SaleChange.decided_by))
        .filter(SaleChange.subscription_id == subscription_id)
    )


def _load_change(db: Session, row: Subscription, change_id: int) -> SaleChange:
    change = _changes(db, row.id).filter(SaleChange.id == change_id).first()
    if change is None:
        raise HTTPException(status_code=404, detail="Change request not found")
    return change


def _edit_body(row: Subscription, change: SaleChange) -> SubscriptionIn:
    """The subscription as the request asks it to be (omitted fields kept)."""
    return SubscriptionIn(
        customer_id=row.customer_id,
        modality_id=change.modality_id if change.modality_id is not None else row.modality_id,
        joined_on=change.joined_on or row.joined_on,
        expires_on=change.expires_on or row.expires_on,
        seller_id=row.seller_id,
        agreement_id=row.agreement_id,
    )


def _decider(user: User, row: Subscription) -> None:
    """Who confirms or rejects: the customer's own login, or the staff on the
    client's word. The seller never decides its own request."""
    if is_seller(user):
        raise HTTPException(status_code=403, detail="The customer confirms a seller's request, not the seller")
    ensure_customer_access(user, row.customer_id)


@router.get("/{item_id}/changes", response_model=list[SaleChangeOut])
def list_changes(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The change requests of a subscription, newest first."""
    row = _load(db, item_id)
    ensure_customer_access(user, row.customer_id, customer_area=True)
    _own_sale(user, row, db)
    return [_change_out(change) for change in _changes(db, item_id).order_by(SaleChange.id.desc()).all()]


@router.post("/{item_id}/changes", response_model=SaleChangeOut, status_code=201, dependencies=SELLER)
def request_change(
    item_id: int, body: SaleChangeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """A seller asks to edit or cancel one of its sales. Nothing changes
    until the customer confirms; the customer is notified at once. The edit
    is checked now (region rules, paid installments) and again when confirmed."""
    row = _load(db, item_id)
    _own_sale(user, row)
    if row.cancelled_on is not None:
        raise HTTPException(status_code=409, detail="Subscription is already cancelled")
    if _changes(db, item_id).filter(SaleChange.status == CHANGE_PENDING).first() is not None:
        raise HTTPException(status_code=409, detail="A change is already awaiting the customer's confirmation")
    change = SaleChange(
        subscription_id=row.id,
        seller_id=user.seller_id,
        requested_by_id=user.id,
        kind=body.kind,
        modality_id=body.modality_id,
        joined_on=body.joined_on,
        expires_on=body.expires_on,
        cancelled_on=(body.cancelled_on or clock.today()) if body.kind == "cancel" else None,
        note=body.note,
    )
    if change.kind == "edit":
        wanted = _edit_body(row, change)
        if (wanted.modality_id, wanted.joined_on, wanted.expires_on) == (row.modality_id, row.joined_on, row.expires_on):
            raise HTTPException(status_code=400, detail="The request changes nothing")
        # Dry run of the edit: the same refusals the confirmation would give.
        _apply_update(db, row, wanted, row.seller_id, row.agreement_id)
        db.rollback()
        row = _load(db, item_id)
    elif change.cancelled_on < row.joined_on:
        raise HTTPException(status_code=400, detail="cancelled_on cannot be before joined_on")
    db.add(change)
    db.flush()
    change = _load_change(db, row, change.id)
    sale_change_requested(db, row, change, user)
    db.commit()
    return _change_out(_load_change(db, row, change.id))


@router.post("/{item_id}/changes/{change_id}/confirm", response_model=SubscriptionOut)
def confirm_change(
    item_id: int,
    change_id: int,
    body: SaleChangeDecision | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The customer (or the staff on its word) accepts: the change is applied now."""
    row = _load(db, item_id)
    _decider(user, row)
    change = _load_change(db, row, change_id)
    if change.status != CHANGE_PENDING:
        raise HTTPException(status_code=409, detail=f"Change request already {change.status}")
    if change.kind == "edit":
        track_minimum(db, _apply_update(db, row, _edit_body(row, change), row.seller_id, row.agreement_id), user)
    else:
        _apply_cancel(db, row, change.cancelled_on, user)
        track_minimum(db, [row.agreement_id], user)
    change.status = CHANGE_CONFIRMED
    change.decided_at = clock.now().replace(microsecond=0)
    change.decided_by_id = user.id
    change.decision_note = body.note if body else ""
    db.flush()
    sale_change_decided(db, _load(db, item_id), _load_change(db, row, change_id), user)
    db.commit()
    return _out(_load(db, item_id), db, user)


@router.post("/{item_id}/changes/{change_id}/reject", response_model=SubscriptionOut)
def reject_change(
    item_id: int,
    change_id: int,
    body: SaleChangeDecision | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = _load(db, item_id)
    _decider(user, row)
    change = _load_change(db, row, change_id)
    if change.status != CHANGE_PENDING:
        raise HTTPException(status_code=409, detail=f"Change request already {change.status}")
    change.status = CHANGE_REJECTED
    change.decided_at = clock.now().replace(microsecond=0)
    change.decided_by_id = user.id
    change.decision_note = body.note if body else ""
    db.flush()
    sale_change_decided(db, row, _load_change(db, row, change_id), user)
    db.commit()
    return _out(_load(db, item_id), db, user)


@router.delete("/{item_id}/changes/{change_id}", response_model=SubscriptionOut, dependencies=SELLER)
def withdraw_change(item_id: int, change_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The seller takes its pending request back."""
    row = _load(db, item_id)
    _own_sale(user, row)
    change = _load_change(db, row, change_id)
    if change.status != CHANGE_PENDING:
        raise HTTPException(status_code=409, detail=f"Change request already {change.status}")
    change.status = CHANGE_WITHDRAWN
    change.decided_at = clock.now().replace(microsecond=0)
    change.decided_by_id = user.id
    db.flush()
    sale_change_withdrawn(db, row, _load_change(db, row, change_id), user)
    db.commit()
    return _out(_load(db, item_id), db, user)


@router.post("/{item_id}/reactivate", response_model=SubscriptionOut, dependencies=STAFF)
def reactivate_subscription(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = _load(db, item_id)
    if row.cancelled_on is None:
        raise HTTPException(status_code=409, detail="Subscription is not cancelled")
    other = current_subscription(db, row.customer_id)
    if other is not None:
        raise HTTPException(status_code=409, detail="Customer already has a subscription; cancel it first")
    row.cancelled_on = None
    sync_installments(db, row)
    track_minimum(db, [row.agreement_id], user)
    db.commit()
    return _out(_load(db, item_id), db)


@router.put("/{item_id}/installments/{installment_id}", response_model=SubscriptionOut, dependencies=STAFF)
def pay_installment(
    item_id: int,
    installment_id: int,
    body: InstallmentPay,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Register (or, with ``paid_on: null``, undo) a fee's payment. A
    corporate fee has two parts — the company's and the employee's —
    registered on their own (``part``) or together (omitted: every part
    still open; to undo, both); it is paid once both are in, and only then
    are its taxes computed and the employee told it is paid."""
    row = _load(db, item_id)
    item = db.get(Installment, installment_id)
    if item is None or item.subscription_id != item_id:
        raise HTTPException(status_code=404, detail="Installment not found")
    was_paid = item.paid_on is not None
    if not is_split(row):
        if body.part is not None:
            raise HTTPException(status_code=400, detail="Only a corporate fee is paid in parts")
        if body.paid_on is None:
            item.paid_on = item.paid_cents = item.registered_by_id = None
        else:
            item.paid_on = body.paid_on
            item.paid_cents = body.paid_cents if body.paid_cents is not None else item.amount_cents
            item.registered_by_id = user.id
    else:
        parts = [body.part] if body.part else [part for part in PARTS if body.paid_on is None or not part_paid(item, part)]
        if body.paid_cents is not None and len(parts) != 1:
            raise HTTPException(status_code=400, detail="paid_cents needs the part it pays (company or employee)")
        for part in parts:
            if body.paid_on is None:
                undo_part(item, part)
            else:
                register_part(item, part, body.paid_on, body.paid_cents, user.id)
        if body.paid_on is not None and item.paid_on is None:
            db.flush()
            for part in parts:
                if part_cents(item, part):
                    installment_part_paid(db, row, item, part)
    if item.paid_on is None:
        clear_installment_taxes(item)
    elif not was_paid or body.paid_on is not None:
        apply_installment_taxes(db, row, item)
        db.flush()
        db.expire(row, ["installments"])
        installment_paid(db, row, item, subscription_status(row, row.installments, clock.today()))
    db.commit()
    return _out(_load(db, item_id), db)


@router.put("/{item_id}/share", response_model=SubscriptionOut, dependencies=STAFF)
def change_share(item_id: int, body: ShareChangeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Co-payment: a new split of the employee's fees, from
    ``effective_from`` on (default today; any date, on the company's word).
    The open fees of the periods starting then are split again; fees with a
    part paid keep their split."""
    row = _load(db, item_id)
    record_share(db, row, body.company_share_percent, share_effective_from(body.effective_from, False), user, body.note)
    db.commit()
    return _out(_load(db, item_id), db, user)


@router.delete("/{item_id}", status_code=204, dependencies=STAFF)
def delete_subscription(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(Subscription, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    agreement_id = row.agreement_id
    try:
        db.delete(row)
        db.flush()
        track_minimum(db, [agreement_id], user)  # one employee fewer
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record is in use")
