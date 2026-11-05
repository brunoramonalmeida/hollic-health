"""User management.

Admins manage every user. Supervisors manage only the external logins of
clients (tied to a customer) and of partner companies (tied to a partner,
for the self-service portal); they cannot create, change or delete staff,
professional or seller accounts. A professional user is tied to the
professional whose appointments it signs and whose payouts it sees; a seller
user to the seller whose sales, customers and commissions it manages.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..auth import ROLE_ADMIN, ROLE_CLIENT, ROLE_PARTNER, ROLE_SUPERVISOR, get_current_user, hash_password, require_roles
from ..database import get_db
from ..models import Customer, Partner, Professional, Seller, User
from ..notifications import user_created
from ..signing import reset_enrolment
from ..schemas import UserIn, UserOut
from .auth import user_out

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_roles(ROLE_ADMIN, ROLE_SUPERVISOR))],
)


# The external logins a supervisor may manage.
SUPERVISED_ROLES = (ROLE_CLIENT, ROLE_PARTNER)
NOT_SUPERVISED = "Supervisors can only manage client and partner company users"


def _can_manage(actor: User, role: str) -> bool:
    return actor.role == ROLE_ADMIN or role in SUPERVISED_ROLES


def _load(db: Session, item_id: int) -> User:
    row = (
        db.query(User)
        .options(joinedload(User.customer), joinedload(User.professional), joinedload(User.seller), joinedload(User.partner))
        .filter(User.id == item_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return row


def _check_links(db: Session, body: UserIn):
    if body.customer_id is not None and db.get(Customer, body.customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if body.professional_id is not None and db.get(Professional, body.professional_id) is None:
        raise HTTPException(status_code=404, detail="Professional not found")
    if body.seller_id is not None and db.get(Seller, body.seller_id) is None:
        raise HTTPException(status_code=404, detail="Seller not found")
    if body.partner_id is not None and db.get(Partner, body.partner_id) is None:
        raise HTTPException(status_code=404, detail="Partner not found")


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    query = db.query(User).options(
        joinedload(User.customer), joinedload(User.professional), joinedload(User.seller), joinedload(User.partner)
    )
    if actor.role != ROLE_ADMIN:
        query = query.filter(User.role.in_(SUPERVISED_ROLES))
    return [user_out(row) for row in query.order_by(User.name).all()]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    if not _can_manage(actor, body.role):
        raise HTTPException(status_code=403, detail=NOT_SUPERVISED)
    if not body.password:
        raise HTTPException(status_code=400, detail="Password is required for a new user")
    _check_links(db, body)
    row = User(
        name=body.name,
        email=body.email,
        role=body.role,
        customer_id=body.customer_id,
        professional_id=body.professional_id,
        seller_id=body.seller_id,
        partner_id=body.partner_id,
        password_hash=hash_password(body.password),
    )
    db.add(row)
    try:
        db.flush()
        user_created(db, _load(db, row.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already in use")
    return user_out(_load(db, row.id))


@router.get("/{item_id}", response_model=UserOut)
def get_user(item_id: int, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    row = _load(db, item_id)
    if not _can_manage(actor, row.role):
        raise HTTPException(status_code=403, detail=NOT_SUPERVISED)
    return user_out(row)


@router.put("/{item_id}", response_model=UserOut)
def update_user(
    item_id: int, body: UserIn, db: Session = Depends(get_db), actor: User = Depends(get_current_user)
):
    row = _load(db, item_id)
    if not _can_manage(actor, row.role) or not _can_manage(actor, body.role):
        raise HTTPException(status_code=403, detail=NOT_SUPERVISED)
    if row.id == actor.id and body.role != actor.role:
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    _check_links(db, body)
    row.name = body.name
    row.email = body.email
    row.role = body.role
    row.customer_id = body.customer_id
    row.professional_id = body.professional_id
    row.seller_id = body.seller_id
    row.partner_id = body.partner_id
    if body.password:
        row.password_hash = hash_password(body.password)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already in use")
    return user_out(_load(db, item_id))


@router.delete("/{item_id}", status_code=204)
def delete_user(item_id: int, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    row = _load(db, item_id)
    if not _can_manage(actor, row.role):
        raise HTTPException(status_code=403, detail=NOT_SUPERVISED)
    if row.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own user")
    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record is in use")


@router.post("/{item_id}/totp/reset", response_model=UserOut)
def reset_totp(item_id: int, db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    """Lost phone: an administrator clears the professional's authenticator so it can be set up again."""
    if actor.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Only administrators reset an authenticator")
    row = _load(db, item_id)
    reset_enrolment(row)
    row.sign_failures = 0
    row.sign_locked_until = None
    db.commit()
    return user_out(_load(db, item_id))
