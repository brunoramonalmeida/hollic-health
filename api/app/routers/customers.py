"""Customers (plan holders) and their dependents.

Staff manage every record. A client user may only read its own customer
record and dependents; changes to personal data go through the staff. A
seller sells to anyone inside its region's scope, so it reaches every
customer record: it registers customers (``seller_id`` remembers who did)
and edits them and their dependents; deleting a customer and the virtual
cards stay with the staff.

The **virtual card** of each patient (holder and dependents) lives here:
its rotating code is shown only to the patient's own login (see
``app.virtual_card``); the staff or the patient can reset it.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..auth import (
    ensure_customer_access,
    get_current_user,
    is_client,
    is_seller,
    require_customer_area,
    require_selling,
    require_staff,
)
from ..billing import current_subscription, refresh_customer_installments, subscription_status
from ..database import get_db
from ..models import Customer, Dependent, User
from ..notifications import card_reset
from ..schemas import CustomerIn, CustomerOut, DependentIn, DependentOut, VirtualCardOut
from ..usage import plan_usage
from ..virtual_card import PERIOD_SECONDS, card_number, current_code, generate_secret, seconds_remaining

# Staff and clients only: professional logins never browse customer records.
router = APIRouter(prefix="/customers", tags=["customers"], dependencies=[Depends(require_customer_area)])
STAFF = [Depends(require_staff)]
SELLING = [Depends(require_selling)]  # staff and sellers


def _customer(db: Session, customer_id: int) -> Customer:
    row = db.get(Customer, customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


@router.get("", response_model=list[CustomerOut])
def list_customers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Customer)
    if user.role == "client":
        query = query.filter(Customer.id == user.customer_id)
    return query.order_by(Customer.name).all()


@router.post("", response_model=CustomerOut, status_code=201, dependencies=SELLING)
def create_customer(body: CustomerIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """``seller_id`` remembers the seller who registered the customer, if one did."""
    row = Customer(**body.model_dump(), seller_id=user.seller_id if is_seller(user) else None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{item_id}", response_model=CustomerOut)
def get_customer(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ensure_customer_access(user, item_id, customer_area=True)
    return _customer(db, item_id)


@router.put("/{item_id}", response_model=CustomerOut, dependencies=SELLING)
def update_customer(item_id: int, body: CustomerIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ensure_customer_access(user, item_id, customer_area=True)
    row = _customer(db, item_id)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{item_id}", status_code=204, dependencies=STAFF)
def delete_customer(item_id: int, db: Session = Depends(get_db)):
    row = _customer(db, item_id)
    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record is in use")


@router.get("/{item_id}/dependents", response_model=list[DependentOut])
def list_dependents(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ensure_customer_access(user, item_id, customer_area=True)
    _customer(db, item_id)
    return (
        db.query(Dependent)
        .filter(Dependent.customer_id == item_id)
        .order_by(Dependent.name)
        .all()
    )


@router.post("/{item_id}/dependents", response_model=DependentOut, status_code=201, dependencies=SELLING)
def create_dependent(item_id: int, body: DependentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ensure_customer_access(user, item_id, customer_area=True)
    _customer(db, item_id)
    row = Dependent(customer_id=item_id, **body.model_dump())
    db.add(row)
    db.flush()
    refresh_customer_installments(db, item_id)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{item_id}/dependents/{dependent_id}", response_model=DependentOut)
def get_dependent(
    item_id: int, dependent_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ensure_customer_access(user, item_id, customer_area=True)
    row = db.get(Dependent, dependent_id)
    if row is None or row.customer_id != item_id:
        raise HTTPException(status_code=404, detail="Dependent not found")
    return row


@router.put("/{item_id}/dependents/{dependent_id}", response_model=DependentOut, dependencies=SELLING)
def update_dependent(
    item_id: int, dependent_id: int, body: DependentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ensure_customer_access(user, item_id, customer_area=True)
    row = db.get(Dependent, dependent_id)
    if row is None or row.customer_id != item_id:
        raise HTTPException(status_code=404, detail="Dependent not found")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{item_id}/dependents/{dependent_id}", status_code=204, dependencies=SELLING)
def delete_dependent(item_id: int, dependent_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ensure_customer_access(user, item_id, customer_area=True)
    row = db.get(Dependent, dependent_id)
    if row is None or row.customer_id != item_id:
        raise HTTPException(status_code=404, detail="Dependent not found")
    try:
        db.delete(row)
        db.flush()
        refresh_customer_installments(db, item_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record is in use")


# --- virtual card -------------------------------------------------------------------


def ensure_card_secret(db: Session, person) -> str:
    """Issue the card on first use (records created before the cards existed)."""
    if not person.card_secret:
        person.card_secret = generate_secret()
        db.commit()
    return person.card_secret


def _card_out(db: Session, customer: Customer, person, dependent_id: int | None) -> VirtualCardOut:
    now = clock.now()
    subscription = current_subscription(db, customer.id)
    usage = plan_usage(db, subscription, now.date()) if subscription else None
    return VirtualCardOut(
        customer_id=customer.id,
        dependent_id=dependent_id,
        card_number=card_number(customer.id, dependent_id),
        patient_name=person.name,
        holder_name=customer.name,
        plan_name=subscription.modality.name if subscription else None,
        plan_status=subscription_status(subscription, subscription.installments, now.date()) if subscription else None,
        valid_until=subscription.expires_on if subscription else None,
        appointment_limit=usage.limit if usage else 0,
        appointments_remaining=usage.remaining if usage else None,
        code=current_code(ensure_card_secret(db, person), now),
        period_seconds=PERIOD_SECONDS,
        seconds_remaining=seconds_remaining(now),
        server_time=now,
    )


def _card_owner(user: User, customer_id: int) -> None:
    """The card (its code) is only for the patient's own login: the staff
    never sees it, so nobody but the patient can present it."""
    if not is_client(user) or user.customer_id != customer_id:
        raise HTTPException(status_code=403, detail="The virtual card is only shown to the patient's own login")


@router.get("/{item_id}/card", response_model=VirtualCardOut)
def holder_card(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The holder's virtual card with the code in force (changes every 30 s)."""
    _card_owner(user, item_id)
    customer = _customer(db, item_id)
    return _card_out(db, customer, customer, None)


@router.get("/{item_id}/dependents/{dependent_id}/card", response_model=VirtualCardOut)
def dependent_card(
    item_id: int, dependent_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """A dependent's virtual card (each dependent has its own secret)."""
    _card_owner(user, item_id)
    customer = _customer(db, item_id)
    row = db.get(Dependent, dependent_id)
    if row is None or row.customer_id != item_id:
        raise HTTPException(status_code=404, detail="Dependent not found")
    return _card_out(db, customer, row, dependent_id)


def _reset(db: Session, customer: Customer, person, user: User) -> None:
    person.card_secret = generate_secret()
    db.flush()
    card_reset(db, customer, person.name, user)
    db.commit()


@router.post("/{item_id}/card/reset", status_code=204)
def reset_holder_card(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Issue a new card (old codes stop working): the patient's own login or the staff."""
    ensure_customer_access(user, item_id)  # not the customer area: sellers have no part in the cards
    customer = _customer(db, item_id)
    _reset(db, customer, customer, user)


@router.post("/{item_id}/dependents/{dependent_id}/card/reset", status_code=204)
def reset_dependent_card(
    item_id: int, dependent_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ensure_customer_access(user, item_id)
    customer = _customer(db, item_id)
    row = db.get(Dependent, dependent_id)
    if row is None or row.customer_id != item_id:
        raise HTTPException(status_code=404, detail="Dependent not found")
    _reset(db, customer, row, user)
