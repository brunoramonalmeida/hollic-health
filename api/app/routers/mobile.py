"""Patient app contract.

The PWA calls ``/api/app/v1`` (the shape of the old Laravel app). These
routes translate that contract onto the existing customers, plans,
appointments and virtual card. The operations API under ``/appointments``
and the rest is unchanged.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from .. import clock
from ..auth import ROLE_CLIENT, create_token, ensure_access_active, get_current_user, verify_password
from ..billing import (
    INSTALLMENT_OVERDUE,
    INSTALLMENT_PAID,
    current_subscription,
    installment_status,
    monthly_amount,
    subscription_status,
    waiting_until,
)
from ..database import get_db
from ..models import Appointment, Clinic, Customer, Notification, Professional, Specialty, User
from ..routers.appointments import _out, _query, create_appointment, update_appointment
from ..routers.auth import change_password
from ..routers.customers import _card_out
from ..schemas import AppointmentIn, PasswordChangeIn

router = APIRouter(prefix="/api/app/v1", tags=["mobile"])
public = APIRouter(prefix="/api", tags=["mobile"])

UFS = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AM": "Amazonas",
    "AP": "Amapá",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MG": "Minas Gerais",
    "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso",
    "PA": "Pará",
    "PB": "Paraíba",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "PR": "Paraná",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RO": "Rondônia",
    "RR": "Roraima",
    "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina",
    "SE": "Sergipe",
    "SP": "São Paulo",
    "TO": "Tocantins",
}
UF_ID = {uf: index + 1 for index, uf in enumerate(UFS)}

STATUS_META = {
    "agendada": ("marked", "Agendado", True),
    "realizada": ("completed", "Concluído", False),
    "cancelada": ("canceled", "Cancelado", False),
}


def _client(user: User = Depends(get_current_user)) -> User:
    if user.role != ROLE_CLIENT or user.customer_id is None:
        raise HTTPException(status_code=403, detail="This app is for plan holders")
    return user


def _holder(db: Session, user: User) -> Customer:
    row = (
        db.query(Customer)
        .options(joinedload(Customer.dependents))
        .filter(Customer.id == user.customer_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


def _reais(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    raise HTTPException(status_code=422, detail="Birth date must be YYYY-MM-DD or DD/MM/YYYY")


def _state_payload(uf: str) -> dict:
    code = (uf or "").upper()[:2]
    return {"id": UF_ID.get(code, 0), "name": UFS.get(code, code), "uf": code}


def _city_id(name: str, uf: str) -> int:
    return abs(hash((name.strip().lower(), (uf or "").upper()))) % 1_000_000 or 1


def _address_payload(customer: Customer) -> dict | None:
    if not any((customer.street, customer.city, customer.zip_code)):
        return None
    state = _state_payload(customer.state)
    city_name = customer.city or ""
    return {
        "id": customer.id,
        "street": customer.street,
        "number": customer.number,
        "complement": customer.complement,
        "neighborhood": customer.district,
        "zip_code": customer.zip_code,
        "zipcode": customer.zip_code,
        "city_id": _city_id(city_name, customer.state),
        "city": {"id": _city_id(city_name, customer.state), "name": city_name, "state_id": state["id"], "state": state},
        "state": state,
        "state_id": state["id"],
    }


def _customer_payload(customer: Customer) -> dict:
    address = _address_payload(customer)
    return {
        "id": customer.id,
        "name": customer.name,
        "email": customer.email,
        "document": customer.document,
        "cpf": customer.document,
        "rg": customer.identity_card,
        "birth_date": customer.birth_date.isoformat() if customer.birth_date else None,
        "phone": customer.phone,
        "cellphone": customer.phone,
        "contacts": [{"email": customer.email, "phone": customer.phone, "cellphone": customer.phone}],
        "addresses": [address] if address else [],
        "dependents": [
            {
                "id": item.id,
                "name": item.name,
                "kinship": item.kinship,
                "birth_date": item.birth_date.isoformat() if item.birth_date else None,
            }
            for item in customer.dependents
        ],
    }


def _session(user: User, customer: Customer, token: str) -> dict:
    payload = _customer_payload(customer)
    return {
        "token_type": "Bearer",
        "token": token,
        "user": {"id": user.id, "name": user.name, "email": user.email, "customer": payload},
        "customer": payload,
    }


def _appointment_payload(row) -> dict:
    code, label, cancelable = STATUS_META.get(row.status, ("marked", row.status, False))
    when = row.marked_at.strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "id": row.id,
        "amount": _reais(row.amount_cents),
        "scheduled_at": when,
        "marked_to": when,
        "status_label": label,
        "category": row.specialty_name,
        "holder": row.customer_name,
        "patient": row.dependent_name or row.customer_name,
        "clinic": row.clinic_name,
        "specialty": row.specialty_name,
        "professional": row.professional_name,
        "services": [row.specialty_name],
        "payment_method": "Plano",
        "observations": row.notes,
        "meta": {
            "status": code,
            "is_cancelable": cancelable,
            "is_canceled": code == "canceled",
            "is_completed": code == "completed",
            "is_editable": cancelable,
        },
    }


def _places(db: Session) -> list[tuple[str, str]]:
    rows = db.query(Clinic.city, Clinic.state).all()
    seen: dict[tuple[str, str], None] = {}
    for city, state in rows:
        if city:
            seen[(city, (state or "").upper()[:2])] = None
    return list(seen)


@public.post("/forgot-password")
def forgot_password(body: dict):
    if not str(body.get("email") or "").strip():
        raise HTTPException(status_code=422, detail="Email is required")
    return {"message": "If this email belongs to a holder, the recovery instructions will be sent."}


@router.post("/login")
def login(body: dict, db: Session = Depends(get_db)):
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    ensure_access_active(user)
    if user.role != ROLE_CLIENT or user.customer_id is None:
        raise HTTPException(status_code=403, detail="This app is for plan holders")
    return _session(user, _holder(db, user), create_token(user))


@router.delete("/logout", status_code=204)
def logout(_user: User = Depends(_client)):
    return None


@router.get("/user")
def me(db: Session = Depends(get_db), user: User = Depends(_client)):
    customer = _holder(db, user)
    payload = _customer_payload(customer)
    return {"id": user.id, "name": user.name, "email": user.email, "customer": payload}


@router.post("/user/change-password")
def password(body: dict, db: Session = Depends(get_db), user: User = Depends(_client)):
    new_password = str(body.get("password") or "")
    if new_password != str(body.get("password_confirmation") or ""):
        raise HTTPException(status_code=422, detail="Password confirmation does not match")
    change_password(
        PasswordChangeIn(current_password=str(body.get("current_password") or ""), new_password=new_password),
        db,
        user,
    )
    return {"message": "Password updated"}


@router.post("/update")
def update_customer(body: dict, db: Session = Depends(get_db), user: User = Depends(_client)):
    customer = _holder(db, user)
    if "name" in body and str(body["name"]).strip():
        customer.name = str(body["name"]).strip()
        user.name = customer.name
    if "email" in body and str(body["email"]).strip():
        email = str(body["email"]).strip()
        taken = db.query(User).filter(User.email == email, User.id != user.id).first()
        if taken is not None:
            raise HTTPException(status_code=409, detail="Email already in use")
        user.email = email
        customer.email = email
    if "cpf" in body:
        customer.document = str(body["cpf"] or "")
    if "rg" in body:
        customer.identity_card = str(body["rg"] or "")
    if "cellphone" in body:
        customer.phone = str(body["cellphone"] or "")
    if "birth_date" in body:
        customer.birth_date = _parse_date(body.get("birth_date"))
    db.commit()
    return {"message": "Updated", "data": _customer_payload(_holder(db, user))}


@router.get("/highlights")
def highlights():
    return []


@router.get("/partners")
def partners():
    return []


@router.get("/notifications")
def notifications(db: Session = Depends(get_db), user: User = Depends(_client)):
    rows = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.created_at.desc()).all()
    data = [
        {
            "id": row.id,
            "title": row.title,
            "message": row.body,
            "is_read": row.read_at is not None,
            "read_at": row.read_at.isoformat() if row.read_at else None,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
    return {"total_unread": sum(1 for row in rows if row.read_at is None), "data": data}


@router.put("/notifications/{item_id}")
def read_notification(item_id: int, db: Session = Depends(get_db), user: User = Depends(_client)):
    row = db.get(Notification, item_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = clock.now().replace(microsecond=0)
        db.commit()
    return {"id": row.id, "is_read": True}


@router.get("/appointments")
def appointments(db: Session = Depends(get_db), user: User = Depends(_client)):
    rows = (
        _query(db)
        .filter(Appointment.customer_id == user.customer_id)
        .order_by(Appointment.marked_at.desc())
        .all()
    )
    return [_appointment_payload(_out(row, user)) for row in rows]


@router.get("/appointments/needed-data")
def needed_data(att: int, db: Session = Depends(get_db), _user: User = Depends(_client)):
    specialty = db.get(Specialty, att)
    if specialty is None:
        raise HTTPException(status_code=404, detail="Specialty not found")
    people = (
        db.query(Professional)
        .options(joinedload(Professional.clinic))
        .filter(Professional.specialty_id == specialty.id)
        .order_by(Professional.name)
        .all()
    )
    clinics = {person.clinic_id: person.clinic for person in people}
    return {
        "clinics": [{"id": clinic.id, "name": clinic.name} for clinic in clinics.values()],
        "professionals": [{"id": person.id, "name": person.name} for person in people],
        "specialties": [{"id": specialty.id, "name": specialty.name}],
        "services": [{"id": specialty.id, "name": specialty.name, "price": specialty.price_cents / 100}],
        "payment_methods": [{"id": 1, "name": "Plano"}],
    }


@router.get("/appointments/{item_id}")
def appointment(item_id: int, db: Session = Depends(get_db), user: User = Depends(_client)):
    row = _query(db).filter(Appointment.id == item_id, Appointment.customer_id == user.customer_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return _appointment_payload(_out(row, user))


@router.post("/appointments")
def book(body: dict, db: Session = Depends(get_db), user: User = Depends(_client)):
    professional_id = body.get("professional_id")
    if not professional_id:
        raise HTTPException(status_code=422, detail="Choose a professional")
    marked = str(body.get("marked_to") or "").strip().replace(" ", "T")
    try:
        marked_at = datetime.fromisoformat(marked)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Appointment time must be YYYY-MM-DD HH:MM") from exc
    saved = create_appointment(
        AppointmentIn(
            customer_id=user.customer_id,
            professional_id=int(professional_id),
            marked_at=marked_at,
            status="agendada",
        ),
        db,
        user,
    )
    return {"data": _appointment_payload(saved), "message": "Appointment booked"}


@router.delete("/appointments/{item_id}")
def cancel(item_id: int, db: Session = Depends(get_db), user: User = Depends(_client)):
    row = db.get(Appointment, item_id)
    if row is None or row.customer_id != user.customer_id:
        raise HTTPException(status_code=404, detail="Appointment not found")
    update_appointment(
        item_id,
        AppointmentIn(
            customer_id=row.customer_id,
            dependent_id=row.dependent_id,
            professional_id=row.professional_id,
            marked_at=row.marked_at,
            status="cancelada",
            notes=row.notes or "",
            urgent=bool(row.urgent),
            remote=bool(row.remote),
        ),
        db,
        user,
    )
    return {"status": True, "message": "Appointment cancelled"}


@router.get("/checkout")
def checkout(db: Session = Depends(get_db), user: User = Depends(_client)):
    subscription = current_subscription(db, user.customer_id)
    today = clock.today()
    if subscription is None or subscription_status(subscription, subscription.installments, today) != "ativa":
        return {"errors": ["An active plan is required to book."]}
    return {"errors": []}


@router.get("/categories")
def categories(db: Session = Depends(get_db), _user: User = Depends(_client)):
    rows = db.query(Specialty).order_by(Specialty.name).all()
    return [{"id": row.id, "name": row.name} for row in rows]


@router.get("/payment-methods")
def payment_methods(_user: User = Depends(_client)):
    return [{"id": 1, "name": "Plano"}]


@router.get("/subscriptions")
def subscription(db: Session = Depends(get_db), user: User = Depends(_client)):
    row = current_subscription(db, user.customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No plan subscription")
    customer = _holder(db, user)
    cents = monthly_amount(row.modality, len(customer.dependents), row.discount_percent)
    today = clock.today()
    charges = []
    for item in sorted(row.installments, key=lambda item: item.sequence):
        status = installment_status(item, today)
        label = {INSTALLMENT_PAID: "Pago", INSTALLMENT_OVERDUE: "Vencido"}.get(status, "Pendente")
        charges.append(
            {
                "id": item.id,
                "amount": item.amount_cents / 100,
                "due_date": item.due_on.isoformat(),
                "payday": item.paid_on.isoformat() if item.paid_on else None,
                "status": status,
                "status_label": label,
            }
        )
    return {
        "id": row.id,
        "value": cents / 100,
        "status": subscription_status(row, row.installments, today),
        "modality": {"id": row.modality_id, "name": row.modality.name, "value": cents / 100},
        "paymentMethod": {"name": "Plano"},
        "transactions": charges,
    }


@router.get("/sante-card")
def sante_card(db: Session = Depends(get_db), user: User = Depends(_client)):
    customer = _holder(db, user)
    card = _card_out(db, customer, customer, None)
    subscription = current_subscription(db, customer.id)
    grace = None
    joined = None
    if subscription is not None:
        joined = subscription.joined_on.isoformat()
        grace = waiting_until(subscription.joined_on, subscription.modality.grace_days).isoformat()
    return {
        "holder": customer.name,
        "user_code": card.card_number,
        "code": card.code,
        "dependents": [item.name for item in customer.dependents],
        "modality": card.plan_name or "Sem plano",
        "description": "Carteira do titular",
        "services": ["Consultas na rede"],
        "accession_date": joined or "",
        "grace_period_ends_at": grace or "",
    }


@router.get("/addresses")
def addresses(db: Session = Depends(get_db), user: User = Depends(_client)):
    address = _address_payload(_holder(db, user))
    return [address] if address else []


def _apply_address(customer: Customer, body: dict) -> None:
    customer.street = str(body.get("street") or "")
    customer.number = str(body.get("number") or "")
    customer.complement = str(body.get("complement") or "")
    customer.district = str(body.get("neighborhood") or "")
    customer.zip_code = str(body.get("zip_code") or body.get("zipcode") or "")
    city = body.get("city") or {}
    state = body.get("state") or {}
    if isinstance(city, dict):
        customer.city = str(city.get("label") or city.get("name") or customer.city)
    if isinstance(state, dict):
        customer.state = str(state.get("label") or state.get("uf") or customer.state).upper()[:2]


@router.post("/addresses", status_code=201)
def create_address(body: dict, db: Session = Depends(get_db), user: User = Depends(_client)):
    customer = _holder(db, user)
    _apply_address(customer, body)
    db.commit()
    return {"message": "Address saved", "data": _address_payload(_holder(db, user))}


@router.put("/addresses/{item_id}")
def update_address(item_id: int, body: dict, db: Session = Depends(get_db), user: User = Depends(_client)):
    customer = _holder(db, user)
    if item_id != customer.id:
        raise HTTPException(status_code=404, detail="Address not found")
    _apply_address(customer, body)
    db.commit()
    return {"message": "Address saved", "data": _address_payload(_holder(db, user))}


@router.get("/states")
def states(_user: User = Depends(_client)):
    return [{"id": UF_ID[uf], "name": name, "uf": uf} for uf, name in UFS.items()]


@router.get("/cities")
def cities(db: Session = Depends(get_db), _user: User = Depends(_client)):
    payload = []
    for name, uf in sorted(_places(db)):
        state = _state_payload(uf)
        payload.append({"id": _city_id(name, uf), "name": name, "state_id": state["id"], "state": state})
    return payload


@router.get("/accredited-network")
def accredited(db: Session = Depends(get_db), _user: User = Depends(_client)):
    clinics = db.query(Clinic).order_by(Clinic.state, Clinic.city, Clinic.name).all()
    people = db.query(Professional).options(joinedload(Professional.specialty)).all()
    by_clinic: dict[int, dict[int, str]] = {}
    for person in people:
        by_clinic.setdefault(person.clinic_id, {})[person.specialty_id] = person.specialty.name
    tree: dict[str, dict] = {}
    for clinic in clinics:
        uf = (clinic.state or "").upper()[:2] or "--"
        state = tree.setdefault(uf, {"id": UF_ID.get(uf, 0), "name": UFS.get(uf, clinic.state or uf), "cities": {}})
        city = state["cities"].setdefault(clinic.city or "", {"id": _city_id(clinic.city or "", uf), "name": clinic.city, "clinics": []})
        specs = [{"id": key, "name": value} for key, value in sorted(by_clinic.get(clinic.id, {}).items(), key=lambda item: item[1])]
        city["clinics"].append({"id": clinic.id, "name": clinic.name, "specialties": specs})
    return [
        {"id": state["id"], "name": state["name"], "cities": list(state["cities"].values())}
        for state in tree.values()
    ]


@public.get("/v1/attendances/schedules/available-times")
def available_times(
    professional_id: int,
    date: date,
    db: Session = Depends(get_db),
    _user: User = Depends(_client),
):
    from ..routers.professionals import get_slots

    days = get_slots(professional_id, date, date, None, db)
    times = []
    for day in days:
        for slot in day.slots:
            if slot.status == "available":
                times.append(slot.time)
    return {"data": times}
