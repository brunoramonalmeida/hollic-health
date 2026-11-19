import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))
bearer = HTTPBearer(auto_error=False)

ROLE_ADMIN = "admin"
ROLE_SUPERVISOR = "supervisor"
ROLE_ATTENDANT = "attendant"
ROLE_CLIENT = "client"
ROLE_PROFESSIONAL = "professional"
ROLE_SELLER = "seller"
# A partner company's login: its self-service portal (employees and agreements), nothing else.
ROLE_PARTNER = "partner"
ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_ATTENDANT, ROLE_CLIENT, ROLE_PROFESSIONAL, ROLE_SELLER, ROLE_PARTNER)
STAFF_ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_ATTENDANT)
# Roles that work with customer records and plans (a professional login only
# sees the patients of its own appointments; a seller its own portfolio).
CUSTOMER_AREA_ROLES = STAFF_ROLES + (ROLE_CLIENT, ROLE_SELLER)
# Roles that take part in appointments (sellers sell plans, nothing else).
APPOINTMENT_ROLES = STAFF_ROLES + (ROLE_CLIENT, ROLE_PROFESSIONAL)
# Roles that register customers and sell plans.
SELLING_ROLES = STAFF_ROLES + (ROLE_SELLER,)
# The network's own users (everyone but the partner companies, whose logins
# only reach their portal): the catalog and the professionals' records.
NETWORK_ROLES = STAFF_ROLES + (ROLE_CLIENT, ROLE_PROFESSIONAL, ROLE_SELLER)
# Roles allowed to change anything that moves prices: plans, discounts,
# specialties, clinic fees, professionals' fees and hours.
PRICING_ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, digest = stored.split("$", 1)
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000)
    return secrets.compare_digest(check.hex(), digest)


def create_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_MINUTES)
    payload = {"sub": str(user.id), "email": user.email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    ensure_access_active(user)
    return user


INACTIVE_SELLER = "Your seller access was deactivated; contact the company"
INACTIVE_PARTNER = "Your company's access was deactivated; contact Hollic Health"


def ensure_access_active(user: User) -> None:
    """A deactivated seller — or a deactivated partner company — loses access
    at once: the login is refused and tokens already issued stop working
    (checked on every request)."""
    if user.role == ROLE_SELLER and (user.seller is None or not user.seller.active):
        raise HTTPException(status_code=401, detail=INACTIVE_SELLER)
    if user.role == ROLE_PARTNER and (user.partner is None or not user.partner.active):
        raise HTTPException(status_code=401, detail=INACTIVE_PARTNER)


def require_roles(*roles: str):
    """Dependency that allows only the given roles."""

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Not allowed for your role")
        return user

    return dependency


require_staff = require_roles(*STAFF_ROLES)
require_pricing = require_roles(*PRICING_ROLES)
require_admin = require_roles(ROLE_ADMIN)
require_customer_area = require_roles(*CUSTOMER_AREA_ROLES)
require_appointment_area = require_roles(*APPOINTMENT_ROLES)
require_selling = require_roles(*SELLING_ROLES)
require_seller = require_roles(ROLE_SELLER)
require_partner = require_roles(ROLE_PARTNER)
require_network = require_roles(*NETWORK_ROLES)


def is_client(user: User) -> bool:
    return user.role == ROLE_CLIENT


def is_professional(user: User) -> bool:
    return user.role == ROLE_PROFESSIONAL


def is_seller(user: User) -> bool:
    return user.role == ROLE_SELLER


def is_partner(user: User) -> bool:
    return user.role == ROLE_PARTNER


def ensure_customer_access(user: User, customer_id: int, customer_area: bool = False) -> None:
    """Clients may only act on their own customer record; professionals and
    partner companies have no access to customer records at all
    (professionals see their own appointments, companies their employees'
    enrollments in the portal);
    sellers reach every customer record and plan (they sell to anyone inside
    their region's scope) but only in the customer area — appointments and
    virtual cards are not theirs."""
    if is_professional(user) or is_partner(user):
        raise HTTPException(status_code=403, detail="Not allowed for your role")
    if is_client(user) and user.customer_id != customer_id:
        raise HTTPException(status_code=403, detail="Not allowed for another customer")
    if is_seller(user) and not customer_area:
        raise HTTPException(status_code=403, detail="Not allowed for your role")


def ensure_appointment_access(user: User, row) -> None:
    """Who may read an appointment: staff any; a client those of its customer;
    a professional those of its own agenda."""
    if is_professional(user):
        if user.professional_id != row.professional_id:
            raise HTTPException(status_code=403, detail="Not allowed for another professional's appointment")
        return
    ensure_customer_access(user, row.customer_id)
