from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import clock
from ..auth import (
    ROLE_PROFESSIONAL,
    create_token,
    ensure_access_active,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)
from ..database import get_db
from ..models import User
from ..schemas import LoginIn, PasswordChangeIn, TokenOut, TotpConfirmIn, TotpSetupOut, TotpStatusOut, UserOut
from ..signing import ISSUER, SigningError, begin_enrolment, confirm_enrolment, otpauth_uri, totp_enabled

router = APIRouter(prefix="/auth", tags=["auth"])


def user_out(user: User, team_size: int = 0) -> UserOut:
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        customer_id=user.customer_id,
        customer_name=user.customer.name if user.customer else None,
        professional_id=user.professional_id,
        professional_name=user.professional.name if user.professional else None,
        seller_id=user.seller_id,
        seller_name=user.seller.name if user.seller else None,
        seller_team_size=team_size,
        partner_id=user.partner_id,
        partner_name=user.partner.name if user.partner else None,
        totp_enabled=totp_enabled(user),
    )


@router.post("/token", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    ensure_access_active(user)
    return TokenOut(access_token=create_token(user))


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from .subscriptions import organisation_ids  # every seller below it, all levels

    team = len(organisation_ids(db, user.seller_id)) - 1 if user.seller_id is not None else 0
    return user_out(user, team)


@router.put("/password", status_code=204)
def change_password(body: PasswordChangeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Any login changes its own password after typing the current one."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()


# --- authenticator (TOTP) of the professional -------------------------------------------

PROFESSIONAL = [Depends(require_roles(ROLE_PROFESSIONAL))]


@router.get("/totp", response_model=TotpStatusOut, dependencies=PROFESSIONAL)
def totp_status(user: User = Depends(get_current_user)):
    return TotpStatusOut(
        enabled=totp_enabled(user),
        enabled_at=user.totp_enabled_at,
        pending=bool(user.totp_secret) and user.totp_enabled_at is None,
        sign_locked_until=user.sign_locked_until,
    )


@router.post("/totp/setup", response_model=TotpSetupOut, dependencies=PROFESSIONAL)
def totp_setup(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Start (or restart) the authenticator setup: a new secret to type into
    the app. It only counts after ``/auth/totp/confirm`` with a code from it."""
    if totp_enabled(user):
        raise HTTPException(status_code=409, detail="Authenticator already set up; ask an administrator to reset it")
    secret = begin_enrolment(user)
    db.commit()
    return TotpSetupOut(secret=secret, otpauth_uri=otpauth_uri(secret, user.email), account=user.email, issuer=ISSUER)


@router.post("/totp/confirm", response_model=TotpStatusOut, dependencies=PROFESSIONAL)
def totp_confirm(body: TotpConfirmIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        confirm_enrolment(user, body.code, clock.now())
    except SigningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db.commit()
    return totp_status(user)
